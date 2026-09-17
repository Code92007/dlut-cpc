from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass


DEFAULT_MAX_FILE_BYTES = 50 * 1024 * 1024
OBJECT_KEY_PATTERN = re.compile(r"^resources/\d{4}/\d{2}/[0-9a-f]{32}\.pdf$")


@dataclass(frozen=True)
class ObjectStorage:
    endpoint: str
    bucket: str
    region: str
    access_key_id: str
    secret_access_key: str
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES

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
        return cls(**values, max_file_bytes=maximum)

    @staticmethod
    def valid_object_key(value: str) -> bool:
        return bool(OBJECT_KEY_PATTERN.fullmatch(value))

    def create_upload(self, filename: str, file_size: int, content_type: str) -> dict:
        if not filename or len(filename) > 255 or not filename.casefold().endswith(".pdf"):
            raise ValueError("只能上传 PDF 文件")
        if type(file_size) is not int or not 1 <= file_size <= self.max_file_bytes:
            raise ValueError(f"PDF 大小必须在 1 字节到 {self.max_file_bytes} 字节之间")
        if content_type not in {"application/pdf", "application/x-pdf", ""}:
            raise ValueError("文件类型必须是 PDF")
        now = dt.datetime.now(dt.timezone.utc)
        object_key = f"resources/{now:%Y/%m}/{uuid.uuid4().hex}.pdf"
        upload_url = self.presign("PUT", object_key, expires=900, content_type="application/pdf", now=now)
        return {
            "objectKey": object_key,
            "uploadUrl": upload_url,
            "headers": {"Content-Type": "application/pdf"},
            "expiresIn": 900,
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
        object_key: str,
        *,
        expires: int,
        content_type: str | None = None,
        now: dt.datetime | None = None,
    ) -> str:
        if not self.valid_object_key(object_key):
            raise ValueError("资源对象键无效")
        if not 1 <= expires <= 3600:
            raise ValueError("签名有效期无效")
        stamp = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
        date = stamp.strftime("%Y%m%d")
        amz_date = stamp.strftime("%Y%m%dT%H%M%SZ")
        scope = f"{date}/{self.region}/s3/aws4_request"
        endpoint = urllib.parse.urlsplit(self.endpoint)
        base_path = endpoint.path.rstrip("/")
        canonical_uri = f"{base_path}/{urllib.parse.quote(self.bucket, safe='')}/{urllib.parse.quote(object_key, safe='/')}"
        signed_header_names = ["host"]
        canonical_headers = f"host:{endpoint.netloc}\n"
        if content_type:
            signed_header_names.insert(0, "content-type")
            canonical_headers = f"content-type:{content_type}\n" + canonical_headers
        signed_headers = ";".join(signed_header_names)
        parameters = {
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.access_key_id}/{scope}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires),
            "X-Amz-SignedHeaders": signed_headers,
        }
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


def storage_status() -> dict:
    try:
        storage = ObjectStorage.from_env()
        return {
            "enabled": storage is not None,
            "maxFileBytes": storage.max_file_bytes if storage else DEFAULT_MAX_FILE_BYTES,
            "error": None,
        }
    except ValueError as exc:
        return {"enabled": False, "maxFileBytes": DEFAULT_MAX_FILE_BYTES, "error": str(exc)}
