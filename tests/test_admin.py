import io
import json
import tempfile
import time
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app
from admin_auth import AdminAuth
from database import Database


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "site.sqlite3"
        self.seed = {"meta": {}, "honors": [], "training": []}
        self.seed_path = self.root / "site.json"
        self.seed_path.write_text(json.dumps(self.seed), encoding="utf-8")
        self.database = Database(self.database_path)
        self.database.initialize(self.seed)
        with patch.dict("os.environ", {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "test-only-password-123"}):
            self.auth = AdminAuth(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def request(self, path, body, *, cookie="", csrf="", origin="https://test.example"):
        handler = app.SiteHandler.__new__(app.SiteHandler)
        handler.path = "/api/admin/" + path
        handler.server = SimpleNamespace(admin_auth=self.auth)
        handler.client_address = ("127.0.0.1", 1)
        handler.headers = Message()
        for key, value in {"Host": "test.example", "Origin": origin, "X-Forwarded-Proto": "https",
                           "Cookie": cookie, "X-CSRF-Token": csrf, "Content-Type": "application/json"}.items():
            handler.headers[key] = value
        data = json.dumps(body).encode()
        handler.headers["Content-Length"] = str(len(data))
        handler.rfile = io.BytesIO(data)
        result = {}
        handler._send_json = lambda payload, status=200, headers=None: result.update(body=payload, status=status, headers=headers or {})
        with patch.object(app, "DATABASE_PATH", self.database_path), patch.object(app, "DATA_PATH", self.seed_path):
            handler.do_POST()
        return result

    def login(self):
        result = self.request("login", {"username": "admin", "password": "test-only-password-123"})
        self.assertEqual(result["status"], 200)
        cookie = result["headers"]["Set-Cookie"]
        return cookie, self.auth.session(cookie)["csrf"]

    def test_anonymous_cannot_mutate_any_admin_endpoint(self):
        for path in ("member", "account", "name", "honor", "refresh-ratings", "logout"):
            self.assertEqual(self.request(path, {})["status"], 401)
        self.assertEqual(self.database.payload(self.seed)["members"], [])

    def test_cookie_security_csrf_and_same_origin_are_required(self):
        cookie, csrf = self.login()
        for attribute in ("Secure", "HttpOnly", "SameSite=Strict", "Max-Age=28800"):
            self.assertIn(attribute, cookie)
        self.assertEqual(self.request("member", {"name": "旧成员"}, cookie=cookie)["status"], 403)
        self.assertEqual(self.request("member", {"name": "旧成员"}, cookie=cookie, csrf=csrf, origin="https://attacker.example")["status"], 403)
        self.assertEqual(self.request("login", {"username": "admin", "password": "test-only-password-123"}, origin="https://attacker.example")["status"], 403)

    def test_admin_can_add_member_account_alias_and_historical_iron(self):
        cookie, csrf = self.login()
        result = self.request("member", {"name": "Old Member", "entryYear": 2007, "graduationYear": 2011, "notes": "private"}, cookie=cookie, csrf=csrf)
        member_id = result["body"]["memberId"]
        with patch("tools.sync_codeforces.fetch_ratings", return_value=[{"handle": "old_cf", "rating": 1800, "maxRating": 2200}]):
            self.assertEqual(self.request("account", {"memberId": member_id, "handle": "old_cf"}, cookie=cookie, csrf=csrf)["status"], 200)
        self.assertEqual(self.request("name", {"memberId": member_id, "displayName": "古早成员", "aliases": ["English"]}, cookie=cookie, csrf=csrf)["status"], 200)
        result = self.request("honor", {"event": "2009 ICPC", "date": "2009-10-01", "team": "Old Team", "medal": "铁牌", "memberIds": [member_id]}, cookie=cookie, csrf=csrf)
        self.assertEqual(result["status"], 200)
        self.database.initialize(self.seed)
        payload = self.database.payload(self.seed)
        member = payload["members"][0]
        self.assertEqual(member["name"], "古早成员")
        self.assertIn("Old Member", member["aliases"])
        self.assertEqual(member["handles"]["codeforces"]["maxRating"], 2200)
        self.assertEqual(member["medals"]["iron"], 1)
        self.assertNotIn("notes", member)
        self.assertEqual(payload["honors"][0]["members"], ["古早成员"])

    def test_invalid_members_dates_sources_and_duplicates_do_not_mutate(self):
        cookie, csrf = self.login()
        self.assertEqual(self.request("member", {"name": "姓名"}, cookie=cookie, csrf=csrf)["status"], 200)
        self.assertEqual(self.request("member", {"name": "姓名"}, cookie=cookie, csrf=csrf)["status"], 400)
        record = {"event": "Event", "date": "2009-10-01", "team": "Team", "medal": "金牌", "memberIds": [1]}
        for change in ({"date": "invalid"}, {"sourceUrl": "javascript:alert(1)"}, {"memberIds": [999999]}, {"memberIds": [1, 1]}):
            self.assertEqual(self.request("honor", {**record, **change}, cookie=cookie, csrf=csrf)["status"], 400)
        self.assertEqual(self.database.payload(self.seed)["honors"], [])

    def test_logout_expiry_wrong_password_and_rate_limit(self):
        cookie, csrf = self.login()
        self.assertEqual(self.request("logout", {}, cookie=cookie, csrf=csrf)["status"], 200)
        self.assertIsNone(self.auth.session(cookie))
        cookie, _ = self.login()
        self.auth.sessions[self.auth.cookie_token(cookie)]["expires"] = time.time() - 1
        self.assertIsNone(self.auth.session(cookie))
        for _ in range(10):
            self.assertEqual(self.request("login", {"username": "admin", "password": "wrong"})["status"], 401)
        self.assertEqual(self.request("login", {"username": "admin", "password": "wrong"})["status"], 429)

    def test_admin_is_disabled_without_strong_password(self):
        with patch.dict("os.environ", {"ADMIN_PASSWORD": "", "ADMIN_PASSWORD_FILE": str(self.root / "missing")}):
            self.auth = AdminAuth(self.root)
        self.assertEqual(self.request("login", {"username": "admin", "password": ""})["status"], 503)


if __name__ == "__main__":
    unittest.main()
