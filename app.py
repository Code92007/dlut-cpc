#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hmac
import json
import mimetypes
import os
import re
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from database import Database, load_seed_file
from admin_auth import AdminAuth


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DATA_PATH = Path(os.environ.get("SITE_DATA_PATH", ROOT / "data" / "site.json"))
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", ROOT / "runtime" / "dlut_cpc.sqlite3"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
SPA_ROUTES = {"/", "/home", "/honor", "/rating", "/training", "/admin", "/pending"}


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
                             "csrf": session["csrf"] if session else None})
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
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 32_768:
                raise ValueError("请求大小无效")
            if self.headers.get_content_type() != "application/json":
                raise ValueError("请求必须使用 JSON")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError("请求数据无效")
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
            elif path == "/api/admin/account":
                handle = self._text(body, "handle", 100, required=True)
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", handle):
                    raise ValueError("Codeforces 账号格式无效")
                database.set_handle(self._member_id(body.get("memberId")), "codeforces", handle, source=source)
                warning = None
                try:
                    from tools.sync_codeforces import fetch_ratings
                    updates = fetch_ratings([handle])
                    database.update_account_ratings(updates, dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
                except (OSError, ValueError) as exc:
                    warning = f"账号已保存，Rating 未更新：{exc}"
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
            elif path == "/api/admin/confirm-members":
                members = body.get("members") if "members" in body else body.get("memberIds")
                if not isinstance(members, list) or not 1 <= len(members) <= 3:
                    raise ValueError("参赛成员列表无效")
                if "members" not in body:
                    members = [self._member_id(value) for value in members]
                database.confirm_honor_members(self._text(body, "honorId", 150, required=True),
                                               members, source=source)
                self._send_json({"ok": True})
            elif path == "/api/admin/refresh-ratings":
                from tools.sync_codeforces import sync_ratings
                updates, errors = sync_ratings(database, load_seed_data())
                self._send_json({"ok": True, "updated": len(updates), "warning": "; ".join(errors) or None})
            else:
                self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

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
    print(f"DLUT CPC listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
