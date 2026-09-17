from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass


DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024
DEFAULT_STORAGE_LIMIT_BYTES = 9_000_000_000
MAX_STORAGE_LIMIT_BYTES = 9_000_000_000
OBJECT_KEY_PATTERN = re.compile(r"^resources/\d{4}/\d{2}/[0-9a-f]{32}\.pdf$")
UPLOAD_RESERVATION_SECONDS = 900

_capacity_lock = threading.Lock()
_upload_reservations: dict[tuple[str, str], list[tuple[str, int, float]]] = {}


@dataclass(frozen=True)
class ObjectStorage:
    endpoint: str
    bucket: str
    region: str
    access_key_id: str
    secret_access_key: str
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    storage_limit_bytes: int = DEFAULT_STORAGE_LIMIT_BYTES

    @classmethod
    def from_env(cls) -> ObjectStorage | None:
        values = {
            "endpoint": os.environ.get("RESOURCE_S3_ENDPOINT", "").strip().rstrip("/"),
            "bucket": os.environ.get("RESOURCE_S3_BUCKET", "").strip(),
            "region": os.environ.get("RESOURCE_S3_REGION", "auto").strip() or "auto",
            "access_key_id": os.environ.get("RESOURCE_S3_ACCESS_KEY_ID", "").strip(),
            "secret_access_key": os.environ.get("RESOURCE_S3_SECRET_ACCESS_KEY", "").strip(),
        }
        if not any((values["endpoint"], values["bucket"], values["access_key_id"], values["secret_access_key"])):
            return None
        if not all((values["endpoint"], values["bucket"], values["access_key_id"], values["secret_access_key"])):
            raise ValueError("对象存储配置不完整")
        parsed = urllib.parse.urlsplit(values["endpoint"])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("RESOURCE_S3_ENDPOINT 无效")
        try:
            maximum = int(os.environ.get("RESOURCE_MAX_FILE_BYTES", str(DEFAULT_MAX_FILE_BYTES)))
        except ValueError as exc:
            raise ValueError("RESOURCE_MAX_FILE_BYTES 无效") from exc
        if not 1024 <= maximum <= 5 * 1024 * 1024 * 1024:
            raise ValueError("RESOURCE_MAX_FILE_BYTES 超出允许范围")
        try:
            storage_limit = int(os.environ.get("RESOURCE_STORAGE_LIMIT_BYTES", str(DEFAULT_STORAGE_LIMIT_BYTES)))
        except ValueError as exc:
            raise ValueError("RESOURCE_STORAGE_LIMIT_BYTES 无效") from exc
        if not 1024 * 1024 <= storage_limit <= MAX_STORAGE_LIMIT_BYTES:
            raise ValueError("RESOURCE_STORAGE_LIMIT_BYTES 必须在 1 MB 到 9 GB 之间")
        return cls(**values, max_file_bytes=maximum, storage_limit_bytes=storage_limit)

    @staticmethod
    def valid_object_key(value: str) -> bool:
        return bool(OBJECT_KEY_PATTERN.fullmatch(value))

    def _validate_upload(self, filename: str, file_size: int, content_type: str) -> None:
        if not filename or len(filename) > 255 or not filename.casefold().endswith(".pdf"):
            raise ValueError("只能上传 PDF 文件")
        if type(file_size) is not int or not 1 <= file_size <= self.max_file_bytes:
            raise ValueError(f"PDF 大小必须在 1 字节到 {self.max_file_bytes} 字节之间")
        if content_type not in {"application/pdf", "application/x-pdf", ""}:
            raise ValueError("文件类型必须是 PDF")

    def create_upload(
        self, filename: str, file_size: int, content_type: str, *, object_key: str | None = None
    ) -> dict:
        self._validate_upload(filename, file_size, content_type)
        now = dt.datetime.now(dt.timezone.utc)
        object_key = object_key or f"resources/{now:%Y/%m}/{uuid.uuid4().hex}.pdf"
        if not self.valid_object_key(object_key):
            raise ValueError("资源对象键无效")
        upload_url = self.presign(
            "PUT", object_key, expires=900, content_type="application/pdf", content_length=file_size, now=now
        )
        return {
            "objectKey": object_key,
            "uploadUrl": upload_url,
            "headers": {"Content-Type": "application/pdf"},
            "expiresIn": 900,
        }

    def prepare_upload(self, filename: str, file_size: int, content_type: str) -> dict:
        self._validate_upload(filename, file_size, content_type)
        now = dt.datetime.now(dt.timezone.utc)
        object_key = f"resources/{now:%Y/%m}/{uuid.uuid4().hex}.pdf"
        storage = self.reserve_upload(object_key, file_size)
        return {**self.create_upload(filename, file_size, content_type, object_key=object_key), "storage": storage}

    def bucket_usage(self) -> dict:
        total_bytes = 0
        object_count = 0
        object_keys: set[str] = set()
        continuation_token: str | None = None
        while True:
            query = {"list-type": "2", "max-keys": "1000"}
            if continuation_token:
                query["continuation-token"] = continuation_token
            url = self.presign("GET", None, expires=60, query=query)
            request = urllib.request.Request(url, method="GET")
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    payload = response.read(10 * 1024 * 1024 + 1)
                    if response.status != 200:
                        raise OSError(f"对象存储返回 HTTP {response.status}")
            except urllib.error.HTTPError as exc:
                raise OSError(f"对象存储用量查询失败：HTTP {exc.code}") from exc
            except urllib.error.URLError as exc:
                raise OSError(f"对象存储用量查询失败：{exc.reason}") from exc
            if len(payload) > 10 * 1024 * 1024:
                raise OSError("对象存储用量响应过大")
            try:
                root = ET.fromstring(payload)
                contents = root.findall(".//{*}Contents")
                if not contents:
                    contents = root.findall(".//Contents")
                for item in contents:
                    size_element = self._xml_child(item, "Size")
                    key_element = self._xml_child(item, "Key")
                    if size_element is None or size_element.text is None:
                        raise ValueError("对象大小缺失")
                    size = int(size_element.text)
                    if size < 0:
                        raise ValueError("对象大小无效")
                    total_bytes += size
                    object_count += 1
                    if key_element is not None and key_element.text:
                        object_keys.add(key_element.text)
                truncated_element = self._xml_child(root, "IsTruncated")
                truncated = (truncated_element.text or "").strip().casefold() == "true" if truncated_element is not None else False
                token_element = self._xml_child(root, "NextContinuationToken")
                continuation_token = token_element.text if token_element is not None else None
            except (ET.ParseError, TypeError, ValueError) as exc:
                raise OSError("对象存储返回的用量数据无效") from exc
            if not truncated:
                return {"usedBytes": total_bytes, "objectCount": object_count, "_objectKeys": object_keys}
            if not continuation_token:
                raise OSError("对象存储分页响应缺少继续标记")

    @staticmethod
    def _xml_child(element: ET.Element, name: str) -> ET.Element | None:
        child = element.find(f"{{*}}{name}")
        return child if child is not None else element.find(name)

    def capacity_status(self) -> dict:
        with _capacity_lock:
            usage = self.bucket_usage()
            pending = self._pending_reservations(usage["_objectKeys"])
            reserved_bytes = sum(item[1] for item in pending)
            return self._capacity_payload(usage, reserved_bytes)

    def reserve_upload(self, object_key: str, file_size: int) -> dict:
        if not self.valid_object_key(object_key):
            raise ValueError("资源对象键无效")
        if type(file_size) is not int or file_size < 1:
            raise ValueError("PDF 文件大小无效")
        with _capacity_lock:
            usage = self.bucket_usage()
            pending = self._pending_reservations(usage["_objectKeys"])
            reserved_bytes = sum(item[1] for item in pending)
            projected = usage["usedBytes"] + reserved_bytes + file_size
            if projected > self.storage_limit_bytes:
                limit_gb = self.storage_limit_bytes / 1_000_000_000
                raise ValueError(
                    f"PDF 上传已被硬限制拦截：本次上传会超过 {limit_gb:g} GB 的对象存储上限"
                )
            pending.append((object_key, file_size, time.monotonic() + UPLOAD_RESERVATION_SECONDS))
            _upload_reservations[self._reservation_key] = pending
            return self._capacity_payload(usage, reserved_bytes + file_size)

    @property
    def _reservation_key(self) -> tuple[str, str]:
        return self.endpoint, self.bucket

    def _pending_reservations(self, stored_keys: set[str]) -> list[tuple[str, int, float]]:
        now = time.monotonic()
        pending = [
            item for item in _upload_reservations.get(self._reservation_key, [])
            if item[2] > now and item[0] not in stored_keys
        ]
        if pending:
            _upload_reservations[self._reservation_key] = pending
        else:
            _upload_reservations.pop(self._reservation_key, None)
        return pending

    def _capacity_payload(self, usage: dict, reserved_bytes: int) -> dict:
        available = max(0, self.storage_limit_bytes - usage["usedBytes"] - reserved_bytes)
        return {
            "usedBytes": usage["usedBytes"],
            "reservedBytes": reserved_bytes,
            "remainingBytes": available,
            "objectCount": usage["objectCount"],
            "storageLimitBytes": self.storage_limit_bytes,
        }

    def download_url(self, object_key: str) -> str:
        if not self.valid_object_key(object_key):
            raise ValueError("资源对象键无效")
        return self.presign("GET", object_key, expires=300)

    def delete_object(self, object_key: str) -> None:
        if not self.valid_object_key(object_key):
            raise ValueError("资源对象键无效")
        request = urllib.request.Request(self.presign("DELETE", object_key, expires=60), method="DELETE")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status not in {200, 202, 204}:
                    raise OSError(f"对象存储返回 HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                raise OSError(f"对象存储删除失败：HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise OSError(f"对象存储删除失败：{exc.reason}") from exc

    def presign(
        self,
        method: str,
        object_key: str | None,
        *,
        expires: int,
        content_type: str | None = None,
        content_length: int | None = None,
        now: dt.datetime | None = None,
        query: dict[str, str] | None = None,
    ) -> str:
        if object_key is not None and not self.valid_object_key(object_key):
            raise ValueError("资源对象键无效")
        if not 1 <= expires <= 3600:
            raise ValueError("签名有效期无效")
        stamp = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        date = stamp.strftime("%Y%m%d")
        amz_date = stamp.strftime("%Y%m%dT%H%M%SZ")
        scope = f"{date}/{self.region}/s3/aws4_request"
        endpoint = urllib.parse.urlsplit(self.endpoint)
        base_path = endpoint.path.rstrip("/")
        canonical_uri = f"{base_path}/{urllib.parse.quote(self.bucket, safe='')}"
        if object_key is not None:
            canonical_uri += f"/{urllib.parse.quote(object_key, safe='/')}"
        headers = {"host": endpoint.netloc}
        if content_type:
            headers["content-type"] = content_type
        if content_length is not None:
            if type(content_length) is not int or content_length < 0:
                raise ValueError("签名文件大小无效")
            headers["content-length"] = str(content_length)
        signed_header_names = sorted(headers)
        canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in signed_header_names)
        signed_headers = ";".join(signed_header_names)
        parameters = dict(query or {})
        parameters.update({
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.access_key_id}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires),
            "X-Amz-SignedHeaders": signed_headers,
        })
        canonical_query = urllib.parse.urlencode(sorted(parameters.items()), quote_via=urllib.parse.quote, safe="~")
        canonical_request = "\n".join(
            (method, canonical_uri, canonical_query, canonical_headers, signed_headers, "UNSIGNED-PAYLOAD")
        )
        string_to_sign = "\n".join(
            (
                "AWS4-HMAC-SHA256",
                amz_date,
                scope,
                hashlib.sha256(canonical_request.encode()).hexdigest(),
            )
        )
        signing_key = self._signing_key(date)
        parameters["X-Amz-Signature"] = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
        query = urllib.parse.urlencode(sorted(parameters.items()), quote_via=urllib.parse.quote, safe="~")
        return urllib.parse.urlunsplit((endpoint.scheme, endpoint.netloc, canonical_uri, query, ""))

    def _signing_key(self, date: str) -> bytes:
        date_key = hmac.new(("AWS4" + self.secret_access_key).encode(), date.encode(), hashlib.sha256).digest()
        region_key = hmac.new(date_key, self.region.encode(), hashlib.sha256).digest()
        service_key = hmac.new(region_key, b"s3", hashlib.sha256).digest()
        return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()


def storage_status(*, include_usage: bool = False) -> dict:
    try:
        storage = ObjectStorage.from_env()
        status = {
            "enabled": storage is not None,
            "uploadEnabled": storage is not None,
            "maxFileBytes": storage.max_file_bytes if storage else DEFAULT_MAX_FILE_BYTES,
            "storageLimitBytes": storage.storage_limit_bytes if storage else DEFAULT_STORAGE_LIMIT_BYTES,
            "error": None,
        }
        if storage and include_usage:
            try:
                status.update(storage.capacity_status())
            except OSError as exc:
                status.update(uploadEnabled=False, error=str(exc))
        return status
    except ValueError as exc:
        return {
            "enabled": False,
            "uploadEnabled": False,
            "maxFileBytes": DEFAULT_MAX_FILE_BYTES,
            "storageLimitBytes": DEFAULT_STORAGE_LIMIT_BYTES,
            "error": str(exc),
        }
