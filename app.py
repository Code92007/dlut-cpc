#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import sqlite3
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from database import Database, load_seed_file
from admin_auth import AdminAuth
from object_storage import DEFAULT_MAX_FILE_BYTES, ObjectStorage, storage_status


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DATA_PATH = Path(os.environ.get("SITE_DATA_PATH", ROOT / "data" / "site.json"))
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", ROOT / "runtime" / "dlut_cpc.sqlite3"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
SPA_ROUTES = {"/", "/home", "/honor", "/rating", "/training", "/resources", "/admin", "/pending"}


class SubmissionLimiter:
    def __init__(self) -> None:
        self.requests: dict[str, deque] = {}
        self.lock = threading.Lock()

    def allow(self, address: str, *, now: float | None = None) -> bool:
        with self.lock:
            stamp = time.monotonic() if now is None else now
            self.requests = {ip: times for ip, times in self.requests.items() if times[-1] > stamp - 600}
            times = self.requests.get(address)
            if times is None:
                if len(self.requests) >= 4096:
                    return False
                times = self.requests.setdefault(address, deque())
            while times and times[0] <= stamp - 600:
                times.popleft()
            if len(times) >= 20:
                return False
            times.append(stamp)
            return True


def load_seed_data() -> dict:
    data = load_seed_file(DATA_PATH)
    required = {"meta", "honors", "training"}
    missing = sorted(required.difference(data))
    if missing:
        raise ValueError(f"site data missing keys: {', '.join(missing)}")
    return data


def load_site_data() -> dict:
    seed = load_seed_data()
    database = Database(DATABASE_PATH)
    if not DATABASE_PATH.exists():
        database.initialize(seed)
    return database.payload(seed)


class SiteHandler(BaseHTTPRequestHandler):
    server_version = "DLUTCPC/0.1"

    def do_HEAD(self) -> None:  # noqa: N802
        self._head_only = True
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/admin/session":
            auth = self.server.admin_auth
            session = auth.session(self.headers.get("Cookie", ""))
            self._send_json({"enabled": auth.enabled, "authenticated": bool(session),
                             "username": session["username"] if session else None,
                             "csrf": session["csrf"] if session else None,
                             "storage": storage_status() if session else None})
            return
        if path == "/api/admin/resources":
            if not self.server.admin_auth.session(self.headers.get("Cookie", "")):
                self._send_json({"error": "请先以管理员身份登录"}, HTTPStatus.UNAUTHORIZED)
                return
            try:
                self._send_json({"items": self._resources_payload(include_drafts=True), "storage": storage_status()})
            except (OSError, ValueError, sqlite3.Error) as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/admin/submissions":
            if not self.server.admin_auth.session(self.headers.get("Cookie", "")):
                self._send_json({"error": "请先以管理员身份登录"}, HTTPStatus.UNAUTHORIZED)
                return
            try:
                query = parse_qs(urlsplit(self.path).query)
                self._send_json(Database(DATABASE_PATH).review_submissions(
                    kind=query.get("kind", ["roster"])[0],
                    status=query.get("status", ["pending"])[0], page=int(query.get("page", ["1"])[0]),
                    school=query.get("school", ["all"])[0]))
            except (OSError, ValueError, sqlite3.Error) as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/healthz":
            self._send_json({"ok": True})
            return
        if path == "/api/site":
            try:
                self._send_json(load_site_data())
            except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if path == "/api/resources":
            try:
                items = self._resources_payload(include_drafts=False)
                categories: dict[str, int] = {}
                tags: dict[str, int] = {}
                for item in items:
                    categories[item["category"]] = categories.get(item["category"], 0) + 1
                    for tag in item["tags"]:
                        tags[tag] = tags.get(tag, 0) + 1
                self._send_json({
                    "items": items,
                    "categories": [{"name": name, "count": count} for name, count in sorted(categories.items())],
                    "tags": [{"name": name, "count": count} for name, count in sorted(tags.items(), key=lambda item: (-item[1], item[0]))],
                })
            except (OSError, ValueError, sqlite3.Error) as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        resource_open = re.fullmatch(r"/api/resources/(\d+)/open", path)
        if resource_open:
            try:
                resource = Database(DATABASE_PATH).get_resource(int(resource_open.group(1)))
                if not resource or resource["resourceType"] != "pdf":
                    self._send_json({"error": "资源不存在"}, HTTPStatus.NOT_FOUND)
                    return
                private = Database(DATABASE_PATH).get_resource(int(resource_open.group(1)), include_drafts=True)
                storage = ObjectStorage.from_env()
                if not storage:
                    raise ValueError("PDF 对象存储尚未配置")
                self._send_redirect(storage.download_url(private["objectKey"]))
            except (OSError, ValueError, sqlite3.Error) as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
            return

        if path in SPA_ROUTES:
            self._send_file(WEB_ROOT / "index.html", cache=False)
            return

        asset = (WEB_ROOT / path.lstrip("/")).resolve()
        try:
            asset.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if asset.is_file():
            self._send_file(asset, cache=True)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path in {"/api/roster-submissions", "/api/account-submissions"}:
            self._submit_public_submission("account" if path.endswith("account-submissions") else "roster")
            return
        if not path.startswith("/api/admin/"):
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        auth = self.server.admin_auth
        session = auth.session(self.headers.get("Cookie", ""))
        if path != "/api/admin/login":
            if not session:
                self._send_json({"error": "请先以管理员身份登录"}, HTTPStatus.UNAUTHORIZED)
                return
            if not hmac.compare_digest(self.headers.get("X-CSRF-Token", "").encode(), session["csrf"].encode()):
                self._send_json({"error": "登录验证已失效，请刷新页面"}, HTTPStatus.FORBIDDEN)
                return
        scheme = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
        if self.headers.get("Origin") != f"{scheme}://{self.headers.get('Host')}":
            self._send_json({"error": "拒绝跨站修改请求"}, HTTPStatus.FORBIDDEN)
            return
        try:
            body = self._read_json()
            if path == "/api/admin/login":
                if not auth.enabled:
                    self._send_json({"error": "管理员账号尚未配置"}, HTTPStatus.SERVICE_UNAVAILABLE)
                    return
                token, limited = auth.login(self._text(body, "username", 100), self._text(body, "password", 512), self.client_address[0])
                if not token:
                    self._send_json({"error": "登录尝试过多，请十分钟后重试" if limited else "账号或密码错误"},
                                    HTTPStatus.TOO_MANY_REQUESTS if limited else HTTPStatus.UNAUTHORIZED)
                    return
                previous = self.headers.get("Cookie", "")
                auth.logout(previous)
                cookie = f"dlut_admin={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=28800"
                if scheme == "https":
                    cookie += "; Secure"
                self._send_json({"ok": True}, headers={"Set-Cookie": cookie})
                return
            if path == "/api/admin/logout":
                auth.logout(self.headers.get("Cookie", ""))
                self._send_json({"ok": True}, headers={"Set-Cookie": "dlut_admin=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
                return
            database = Database(DATABASE_PATH)
            source = {"name": f"管理员人工补录（{session['username']}）", "kind": "manual"}
            if path == "/api/admin/member":
                name = self._text(body, "name", 150, required=True)
                school = self._text(body, "school", 100) or "大连理工大学"
                from database import normalize_name
                with database.connect() as connection:
                    duplicate = connection.execute("SELECT 1 FROM members WHERE (normalized_name=? OR display_name=?) AND school=?", (normalize_name(name), name, school)).fetchone()
                if duplicate and body.get("allowSameName") is not True:
                    raise ValueError("已有同名成员；仅在确认是不同选手时勾选“独立的同名成员”")
                entry = self._year(body.get("entryYear"))
                graduation = self._year(body.get("graduationYear"))
                if entry and graduation and entry > graduation:
                    raise ValueError("入学年份不能晚于毕业年份")
                status = body.get("status", "alumni")
                if status not in {"current", "alumni", "unknown"}:
                    raise ValueError("成员类别无效")
                member_id = database.add_manual_member(name, entry_year=entry, graduation_year=graduation,
                                                      status=status, notes=self._text(body, "notes", 2000), source=source, school=school)
                self._send_json({"ok": True, "memberId": member_id})
            elif path in {"/api/admin/account", "/api/admin/account-edit", "/api/admin/account-delete"}:
                handle = self._text(body, "handle", 100, required=True)
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", handle):
                    raise ValueError("Codeforces 账号格式无效")
                member_id = self._member_id(body.get("memberId"))
                if path.endswith("account-delete"):
                    database.delete_handle(member_id, "codeforces", handle, source=source)
                    self._send_json({"ok": True})
                    return
                if path.endswith("account-edit"):
                    old_handle = self._text(body, "oldHandle", 100, required=True)
                    if not re.fullmatch(r"[A-Za-z0-9_.-]+", old_handle):
                        raise ValueError("原 Codeforces 账号格式无效")
                    database.edit_handle(member_id, "codeforces", old_handle, handle, source=source)
                else:
                    database.set_handle(member_id, "codeforces", handle, source=source)
                warning = self._refresh_account_rating(database, handle)
                self._send_json({"ok": True, "warning": warning})
            elif path == "/api/admin/name":
                aliases = body.get("aliases", [])
                if not isinstance(aliases, list) or len(aliases) > 20 or any(not isinstance(alias, str) or len(alias) > 150 for alias in aliases):
                    raise ValueError("别名列表无效")
                database.set_display_name(self._member_id(body.get("memberId")), self._text(body, "displayName", 150, required=True), aliases)
                self._send_json({"ok": True})
            elif path == "/api/admin/honor":
                member_ids = body.get("memberIds")
                if not isinstance(member_ids, list) or not 1 <= len(member_ids) <= 3:
                    raise ValueError("请选择一至三位参赛成员")
                member_ids = [self._member_id(value) for value in member_ids]
                if len(set(member_ids)) != len(member_ids):
                    raise ValueError("参赛成员不能重复")
                date = self._text(body, "date", 10, required=True)
                if dt.date.fromisoformat(date).isoformat() != date:
                    raise ValueError("比赛日期格式无效")
                medal = body.get("medal")
                if medal not in {"金牌", "银牌", "铜牌", "铁牌"}:
                    raise ValueError("成绩类别无效")
                source_url = self._text(body, "sourceUrl", 1500)
                if source_url and urlsplit(source_url).scheme not in {"http", "https"}:
                    raise ValueError("来源链接必须使用 HTTP 或 HTTPS")
                record = {"event": self._text(body, "event", 300, required=True), "date": date,
                          "team": self._text(body, "team", 200, required=True), "medal": medal,
                          "series": self._text(body, "series", 100) or "其他", "location": self._text(body, "location", 150),
                          "rank": self._text(body, "rank", 100), "source": {**source, "url": source_url}}
                honor_id = database.add_manual_honor_with_members(record, member_ids)
                self._send_json({"ok": True, "honorId": honor_id})
            elif path in {"/api/admin/confirm-members", "/api/admin/edit-members"}:
                members = body.get("members") if "members" in body else body.get("memberIds")
                if not isinstance(members, list) or not 1 <= len(members) <= 3:
                    raise ValueError("参赛成员列表无效")
                if "members" not in body:
                    members = [self._member_id(value) for value in members]
                action = database.edit_honor_members if path.endswith("edit-members") else database.confirm_honor_members
                action(self._text(body, "honorId", 150, required=True), members, source=source,
                       medal=self._text(body, "medal", 20, required=True) if "medal" in body else None)
                self._send_json({"ok": True})
            elif path == "/api/admin/review-submission":
                if type(body.get("approve")) is not bool:
                    raise ValueError("审核决定无效")
                database.review_roster_submission(self._member_id(body.get("submissionId")), body["approve"],
                                                 reviewer=session["username"], reason=self._text(body, "reason", 2000))
                self._send_json({"ok": True})
            elif path == "/api/admin/review-account-submission":
                if type(body.get("approve")) is not bool:
                    raise ValueError("审核决定无效")
                handle = database.review_account_submission(self._member_id(body.get("submissionId")), body["approve"],
                                                            reviewer=session["username"], reason=self._text(body, "reason", 2000))
                warning = self._refresh_account_rating(database, handle) if body["approve"] else None
                self._send_json({"ok": True, "warning": warning})
            elif path == "/api/admin/refresh-ratings":
                from tools.sync_codeforces import sync_ratings
                updates, errors = sync_ratings(database, load_seed_data())
                self._send_json({"ok": True, "updated": len(updates), "warning": "; ".join(errors) or None})
            elif path == "/api/admin/resource-upload":
                storage = ObjectStorage.from_env()
                if not storage:
                    raise ValueError("PDF 对象存储尚未配置")
                upload = storage.create_upload(
                    self._text(body, "filename", 255, required=True),
                    body.get("fileSize"),
                    self._text(body, "contentType", 100),
                )
                self._send_json({"ok": True, **upload})
            elif path == "/api/admin/resource":
                resource_id = self._optional_id(body.get("resourceId"), "资源 ID")
                resource = self._resource_body(body)
                if resource_id:
                    existing = database.get_resource(resource_id, include_drafts=True)
                    if not existing:
                        raise ValueError("资源不存在")
                    if existing["resourceType"] == "pdf" and (
                        resource["resourceType"] != "pdf" or resource["objectKey"] != existing["objectKey"]
                    ):
                        raise ValueError("已上传的 PDF 不能替换或改为链接；请删除后重新添加")
                saved_id = database.save_resource(resource, resource_id=resource_id, created_by=session["username"])
                self._send_json({"ok": True, "resourceId": saved_id}, HTTPStatus.CREATED if resource_id is None else HTTPStatus.OK)
            elif path == "/api/admin/resource-delete":
                resource_id = self._optional_id(body.get("resourceId"), "资源 ID", required=True)
                resource = database.get_resource(resource_id, include_drafts=True)
                if not resource:
                    raise ValueError("资源不存在")
                if resource["resourceType"] == "pdf" and resource.get("objectKey"):
                    storage = ObjectStorage.from_env()
                    if not storage:
                        raise ValueError("对象存储未配置，无法同步删除 PDF")
                    storage.delete_object(resource["objectKey"])
                database.delete_resource(resource_id)
                self._send_json({"ok": True})
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    @staticmethod
    def _refresh_account_rating(database: Database, handle: str) -> str | None:
        try:
            from tools.sync_codeforces import fetch_ratings
            updates = fetch_ratings([handle])
            database.update_account_ratings(updates, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
        except (OSError, ValueError) as exc:
            return f"账号已保存，Rating 未更新：{exc}"
        return None

    def _submit_public_submission(self, kind: str) -> None:
        scheme = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
        if self.headers.get("Origin") != f"{scheme}://{self.headers.get('Host')}":
            self._send_json({"error": "拒绝跨站提交请求"}, HTTPStatus.FORBIDDEN)
            return
        address = self.client_address[0]
        if os.environ.get("TRUST_PROXY_HEADERS") == "1":
            forwarded = self.headers.get("X-Forwarded-For", "").split(",")[-1].strip()
            try:
                address = str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        if not self.server.submission_limiter.allow(address):
            self._send_json({"error": "提交过于频繁，请十分钟后再试"}, HTTPStatus.TOO_MANY_REQUESTS)
            return
        try:
            body = self._read_json()
            database = Database(DATABASE_PATH)
            if kind == "account":
                submission_id, created = database.submit_account(self._member_id(body.get("memberId")),
                    self._text(body, "handle", 100, required=True), note=self._text(body, "note", 2000))
            else:
                submission_id, created = database.submit_roster(
                    self._text(body, "honorId", 150, required=True), body.get("members"), note=self._text(body, "note", 2000))
            self._send_json({"ok": True, "submissionId": submission_id, "duplicate": not created},
                            HTTPStatus.CREATED if created else HTTPStatus.OK)
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 32_768:
            raise ValueError("请求大小无效")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("请求必须使用 JSON")
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError("请求数据无效")
        return body

    @staticmethod
    def _resources_payload(*, include_drafts: bool) -> list[dict]:
        items = Database(DATABASE_PATH).list_resources(include_drafts=include_drafts)
        for item in items:
            if item["resourceType"] == "pdf":
                item["openUrl"] = f"/api/resources/{item['id']}/open"
        return items

    @classmethod
    def _resource_body(cls, body: dict) -> dict:
        resource_type = cls._text(body, "resourceType", 20, required=True)
        if resource_type not in {"pdf", "github", "link"}:
            raise ValueError("资料类型无效")
        difficulty = cls._text(body, "difficulty", 20) or "all"
        if difficulty not in {"all", "beginner", "intermediate", "advanced"}:
            raise ValueError("资料难度无效")
        tags = body.get("tags", [])
        if not isinstance(tags, list) or len(tags) > 12:
            raise ValueError("资料标签无效")
        normalized_tags = []
        seen = set()
        for tag in tags:
            if not isinstance(tag, str) or not tag.strip() or len(tag.strip()) > 30:
                raise ValueError("资料标签无效")
            value = tag.strip()
            if value.casefold() not in seen:
                seen.add(value.casefold())
                normalized_tags.append(value)
        published = body.get("published", True)
        if type(published) is not bool:
            raise ValueError("发布状态无效")
        result = {
            "title": cls._text(body, "title", 200, required=True),
            "resourceType": resource_type,
            "category": cls._text(body, "category", 80, required=True),
            "difficulty": difficulty,
            "description": cls._text(body, "description", 2000),
            "tags": normalized_tags,
            "published": published,
            "url": "",
            "objectKey": "",
            "originalFilename": "",
            "contentType": "",
            "fileSize": None,
        }
        if resource_type == "pdf":
            object_key = cls._text(body, "objectKey", 300, required=True)
            if not ObjectStorage.valid_object_key(object_key):
                raise ValueError("PDF 对象键无效")
            filename = cls._text(body, "originalFilename", 255, required=True)
            if not filename.casefold().endswith(".pdf"):
                raise ValueError("PDF 文件名无效")
            file_size = body.get("fileSize")
            maximum = storage_status().get("maxFileBytes", DEFAULT_MAX_FILE_BYTES)
            if type(file_size) is not int or not 1 <= file_size <= maximum:
                raise ValueError("PDF 文件大小无效")
            result.update(objectKey=object_key, originalFilename=filename, contentType="application/pdf", fileSize=file_size)
        else:
            url = cls._text(body, "url", 2000, required=True)
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("资料链接必须使用 HTTP 或 HTTPS")
            if resource_type == "github" and (parsed.hostname or "").casefold() not in {"github.com", "www.github.com"}:
                raise ValueError("GitHub 资料必须使用 github.com 链接")
            result["url"] = url
        return result

    @staticmethod
    def _optional_id(value: object, label: str, *, required: bool = False) -> int | None:
        if value in (None, "") and not required:
            return None
        if type(value) is not int or value <= 0:
            raise ValueError(f"{label}无效")
        return value

    @staticmethod
    def _text(body: dict, field: str, maximum: int, *, required: bool = False) -> str:
        value = body.get(field, "")
        if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
            raise ValueError(f"{field} 字段无效")
        return value.strip() if field != "password" else value

    @staticmethod
    def _year(value: object) -> int | None:
        if value is None or value == "":
            return None
        if type(value) is not int or not 1900 <= value <= dt.date.today().year + 20:
            raise ValueError("年份无效")
        return value

    @staticmethod
    def _member_id(value: object) -> int:
        if type(value) is not int or value <= 0:
            raise ValueError("请选择有效的成员")
        return value

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK, *, headers: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/admin/") else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)

    def _send_redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_file(self, path: Path, *, cache: bool) -> None:
        body = path.read_bytes()
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if media_type.startswith("text/") or media_type in {"application/javascript", "image/svg+xml"}:
            media_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", media_type)
        cache_asset = cache and path.suffix not in {".js", ".css"}
        self.send_header("Cache-Control", "public, max-age=3600" if cache_asset else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DLUT CPC team website")
    parser.add_argument("--check", action="store_true", help="validate data and exit")
    args = parser.parse_args()
    seed = load_seed_data()
    Database(DATABASE_PATH).initialize(seed)
    data = load_site_data()
    if args.check:
        print(
            f"ok: {len(data['honors'])} honors, {len(data['members'])} members, "
            f"{data['meta']['memberCoverage']}% roster coverage"
        )
        return
    server = ThreadingHTTPServer((HOST, PORT), SiteHandler)
    server.admin_auth = AdminAuth(ROOT)
    server.submission_limiter = SubmissionLimiter()
    print(f"DLUT CPC listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
