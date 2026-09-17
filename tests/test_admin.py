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
        self.submission_limiter = app.SubmissionLimiter()
        with patch.dict("os.environ", {"ADMIN_USERNAME": "admin", "ADMIN_PASSWORD": "test-only-password-123"}):
            self.auth = AdminAuth(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def request(self, path, body=None, *, cookie="", csrf="", origin="https://test.example", method="POST", headers=None):
        handler = app.SiteHandler.__new__(app.SiteHandler)
        handler.path = path if path.startswith("/") else "/api/admin/" + path
        handler.server = SimpleNamespace(admin_auth=self.auth, submission_limiter=self.submission_limiter)
        handler.client_address = ("127.0.0.1", 1)
        handler.headers = Message()
        for key, value in {"Host": "test.example", "Origin": origin, "X-Forwarded-Proto": "https",
                           "Cookie": cookie, "X-CSRF-Token": csrf, "Content-Type": "application/json", **(headers or {})}.items():
            handler.headers[key] = value
        data = json.dumps(body).encode()
        handler.headers["Content-Length"] = str(len(data))
        handler.rfile = io.BytesIO(data)
        result = {}
        handler._send_json = lambda payload, status=200, headers=None: result.update(body=payload, status=status, headers=headers or {})
        handler._send_redirect = lambda location: result.update(location=location, status=302)
        with patch.object(app, "DATABASE_PATH", self.database_path), patch.object(app, "DATA_PATH", self.seed_path):
            handler.do_GET() if method == "GET" else handler.do_POST()
        return result

    def login(self):
        result = self.request("login", {"username": "admin", "password": "test-only-password-123"})
        self.assertEqual(result["status"], 200)
        cookie = result["headers"]["Set-Cookie"]
        return cookie, self.auth.session(cookie)["csrf"]

    def test_anonymous_cannot_mutate_any_admin_endpoint(self):
        for path in ("member", "account", "account-edit", "account-delete", "name", "honor", "confirm-members", "edit-members", "review-submission", "review-account-submission", "refresh-ratings", "resource-upload", "resource", "resource-delete", "logout"):
            self.assertEqual(self.request(path, {})["status"], 401)
        self.assertEqual(self.database.payload(self.seed)["members"], [])

    def test_admin_edits_and_deletes_accounts_with_auth_and_fresh_ratings(self):
        member_id = self.database.add_manual_member("杨君泓")
        self.database.set_handle(member_id, "codeforces", "Lance_J", rating=2024)
        cookie, csrf = self.login()
        body = {"memberId": member_id, "oldHandle": "Lance_J", "handle": "Farewell"}
        for path in ("account-edit", "account-delete"):
            self.assertEqual(self.request(path, body, cookie=cookie)["status"], 403)
            self.assertEqual(self.request(path, body, cookie=cookie, csrf=csrf, origin="https://attacker.example")["status"], 403)
        with patch("tools.sync_codeforces.fetch_ratings", return_value=[{"handle": "Farewell", "rating": 1551, "maxRating": 1595}]) as fetch:
            result = self.request("account-edit", body, cookie=cookie, csrf=csrf)
            self.assertEqual(result["status"], 200, result)
            fetch.assert_called_once_with(["Farewell"])
        member = self.database.payload(self.seed)["members"][0]
        self.assertEqual(member["handles"]["codeforces"]["handle"], "Farewell")
        self.assertEqual(member["handles"]["codeforces"]["maxRating"], 1595)
        with patch("tools.sync_codeforces.fetch_ratings") as fetch:
            self.assertEqual(self.request("account-delete", body, cookie=cookie, csrf=csrf)["status"], 200)
            fetch.assert_not_called()
        self.assertEqual(self.database.payload(self.seed)["members"][0]["accounts"], {})

    def test_admin_manages_published_and_draft_resource_links(self):
        cookie, csrf = self.login()
        common = {"category": "图论", "difficulty": "intermediate", "description": "最短路模板",
                  "tags": ["图论", "模板"], "published": True}
        created = self.request("resource", {**common, "title": "算法仓库", "resourceType": "github",
                                             "url": "https://github.com/example/algorithms"}, cookie=cookie, csrf=csrf)
        self.assertEqual(created["status"], 201, created)
        resource_id = created["body"]["resourceId"]
        draft = self.request("resource", {**common, "title": "草稿", "resourceType": "link",
                                           "url": "https://example.com/draft", "published": False}, cookie=cookie, csrf=csrf)
        self.assertEqual(draft["status"], 201, draft)

        public = self.request("/api/resources", method="GET")
        self.assertEqual([item["title"] for item in public["body"]["items"]], ["算法仓库"])
        admin = self.request("/api/admin/resources", cookie=cookie, method="GET")
        self.assertEqual(len(admin["body"]["items"]), 2)
        self.assertEqual(self.request("resource-delete", {"resourceId": resource_id}, cookie=cookie)["status"], 403)
        self.assertEqual(self.request("resource-delete", {"resourceId": resource_id}, cookie=cookie, csrf=csrf)["status"], 200)
        self.assertEqual(self.request("/api/resources", method="GET")["body"]["items"], [])

    def test_public_pdf_uses_private_download_redirect_and_drafts_stay_closed(self):
        cookie, csrf = self.login()
        body = {"title": "讲义", "resourceType": "pdf", "category": "图论", "difficulty": "advanced",
                "description": "", "tags": ["网络流"], "published": True,
                "objectKey": "resources/2026/09/0123456789abcdef0123456789abcdef.pdf",
                "originalFilename": "flow.pdf", "contentType": "application/pdf", "fileSize": 4096}
        created = self.request("resource", body, cookie=cookie, csrf=csrf)
        resource_id = created["body"]["resourceId"]
        public = self.request("/api/resources", method="GET")["body"]["items"][0]
        self.assertEqual(public["openUrl"], f"/api/resources/{resource_id}/open")
        self.assertNotIn("objectKey", public)
        storage = {"RESOURCE_S3_ENDPOINT": "https://account.r2.cloudflarestorage.com",
                   "RESOURCE_S3_BUCKET": "resources", "RESOURCE_S3_REGION": "auto",
                   "RESOURCE_S3_ACCESS_KEY_ID": "access", "RESOURCE_S3_SECRET_ACCESS_KEY": "secret"}
        with patch.dict("os.environ", storage):
            opened = self.request(f"/api/resources/{resource_id}/open", method="GET")
        self.assertEqual(opened["status"], 302)
        self.assertIn("X-Amz-Signature=", opened["location"])

        self.assertEqual(self.request("resource", {**body, "resourceId": resource_id, "published": False},
                                      cookie=cookie, csrf=csrf)["status"], 200)
        self.assertEqual(self.request(f"/api/resources/{resource_id}/open", method="GET")["status"], 404)

    def test_pdf_upload_api_enforces_bucket_hard_limit(self):
        cookie, csrf = self.login()
        storage = app.ObjectStorage(
            endpoint="https://test.r2.cloudflarestorage.com", bucket="hard-cap-admin", region="auto",
            access_key_id="access", secret_access_key="secret", max_file_bytes=1000, storage_limit_bytes=1000,
        )
        usage = {"usedBytes": 950, "objectCount": 1, "_objectKeys": set()}
        with patch.object(app.ObjectStorage, "from_env", return_value=storage), \
             patch.object(app.ObjectStorage, "bucket_usage", return_value=usage):
            result = self.request("resource-upload", {
                "filename": "notes.pdf", "fileSize": 51, "contentType": "application/pdf",
            }, cookie=cookie, csrf=csrf)
        self.assertEqual(result["status"], 400)
        self.assertIn("硬限制", result["body"]["error"])

    def test_admin_resource_list_stays_available_when_usage_check_fails(self):
        cookie, _csrf = self.login()
        storage = app.ObjectStorage(
            endpoint="https://test.r2.cloudflarestorage.com", bucket="offline-admin", region="auto",
            access_key_id="access", secret_access_key="secret",
        )
        with patch.object(app.ObjectStorage, "from_env", return_value=storage), \
             patch.object(app.ObjectStorage, "bucket_usage", side_effect=OSError("R2 暂不可用")):
            result = self.request("/api/admin/resources", cookie=cookie, method="GET")
        self.assertEqual(result["status"], 200)
        self.assertFalse(result["body"]["storage"]["uploadEnabled"])
        self.assertIn("R2 暂不可用", result["body"]["storage"]["error"])

    def test_edit_api_failure_saves_binding_without_wrong_old_rating(self):
        member_id = self.database.add_manual_member("杨君泓")
        self.database.set_handle(member_id, "codeforces", "Old", rating=2400)
        cookie, csrf = self.login()
        with patch("tools.sync_codeforces.fetch_ratings", side_effect=OSError("offline")):
            result = self.request("account-edit", {"memberId": member_id, "oldHandle": "Old", "handle": "New"}, cookie=cookie, csrf=csrf)
        self.assertEqual(result["status"], 200)
        self.assertIn("offline", result["body"]["warning"])
        account = self.database.payload(self.seed)["members"][0]["handles"]["codeforces"]
        self.assertEqual(account["handle"], "New")
        self.assertIsNone(account["rating"])

    def test_account_management_rejects_invalid_ids_handles_and_stale_old_account(self):
        member_id = self.database.add_manual_member("测试成员")
        self.database.set_handle(member_id, "codeforces", "Old")
        cookie, csrf = self.login()
        for path in ("account-edit", "account-delete"):
            for change in ({"memberId": True}, {"memberId": 999999}, {"handle": "a;b"}, {"handle": ""}, {"handle": "Other", "oldHandle": "Missing"}):
                body = {"memberId": member_id, "oldHandle": "Old", "handle": "Old", **change}
                self.assertEqual(self.request(path, body, cookie=cookie, csrf=csrf)["status"], 400)
        self.assertEqual(self.database.payload(self.seed)["members"][0]["handles"]["codeforces"]["handle"], "Old")

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

    def test_admin_confirms_existing_award_without_deleting_it(self):
        cookie, csrf = self.login()
        record = {"id": "historical", "event": "2018 ICPC", "date": "2018-10-01", "series": "ICPC", "team": "Old Team", "medal": "金牌"}
        self.database.import_historical_batch({"batchId": "test-v1", "honors": [record]})
        ids = [self.database.add_manual_member(name) for name in ("甲", "乙", "丙")]
        request = {"honorId": "historical", "memberIds": ids}
        self.assertEqual(self.request("confirm-members", request, cookie=cookie)["status"], 403)
        self.assertEqual(self.request("confirm-members", {**request, "memberIds": ids[:1]}, cookie=cookie, csrf=csrf)["status"], 400)
        self.assertEqual(self.request("confirm-members", request, cookie=cookie, csrf=csrf)["status"], 200)
        payload = self.database.payload(self.seed)
        self.assertEqual(payload["pendingHonors"], [])
        self.assertEqual(payload["honors"][0]["members"], ["甲", "乙", "丙"])
        self.assertEqual(payload["honors"][0]["medal"], "金牌")
        self.assertEqual(self.request("confirm-members", request, cookie=cookie, csrf=csrf)["status"], 400)

    def test_admin_confirms_names_and_ids_creating_only_missing_members(self):
        cookie, csrf = self.login()
        record = {"id": "historical", "event": "2018 ICPC", "date": "2018-10-01", "team": "Old Team", "medal": "金牌"}
        self.database.import_historical_batch({"batchId": "test-v1", "honors": [record]})
        he = self.database.add_manual_member("何泾")
        fu = self.database.add_manual_member("傅心语")
        request = {"honorId": "historical", "members": ["董霄然", "傅心语", he]}
        self.assertEqual(self.request("confirm-members", request)["status"], 401)
        self.assertEqual(self.request("confirm-members", request, cookie=cookie)["status"], 403)
        self.assertEqual(len(self.database.payload(self.seed)["members"]), 2)
        result = self.request("confirm-members", request, cookie=cookie, csrf=csrf)
        self.assertEqual(result["status"], 200, result)
        payload = self.database.payload(self.seed)
        self.assertEqual(len(payload["members"]), 3)
        self.assertEqual(next(m["id"] for m in payload["members"] if m["name"] == "傅心语"), fu)
        self.assertEqual(payload["honors"][0]["members"], ["董霄然", "傅心语", "何泾"])
        self.assertEqual(payload["pendingHonors"], [])

    def test_only_admin_can_fill_unknown_medal_while_confirming_or_editing_members(self):
        cookie, csrf = self.login()
        record = {"id": "historical", "event": "2018 ICPC", "date": "2018-10-01", "team": "Old Team", "medal": "金牌"}
        self.database.import_historical_batch({"batchId": "test-v1", "honors": [record]})
        with self.database.connect() as connection:
            connection.execute("UPDATE honors SET medal='' WHERE id='historical'")
        body = {"honorId": "historical", "members": ["甲", "乙", "丙"], "medal": "银牌"}
        self.assertEqual(self.request("confirm-members", body)["status"], 401)
        self.assertEqual(self.request("confirm-members", body, cookie=cookie)["status"], 403)
        self.assertEqual(self.request("confirm-members", body, cookie=cookie, csrf=csrf)["status"], 200)
        self.assertEqual(self.database.payload(self.seed)["honors"][0]["medal"], "银牌")
        body["medal"] = "铜牌"
        self.assertEqual(self.request("edit-members", body, cookie=cookie, csrf=csrf)["status"], 400)
        self.assertEqual(self.database.payload(self.seed)["honors"][0]["medal"], "银牌")

    def test_invalid_name_confirmation_rolls_back_without_partial_people(self):
        cookie, csrf = self.login()
        record = {"id": "historical", "event": "2018 ICPC", "date": "2018-10-01", "team": "Old Team", "medal": "金牌"}
        self.database.import_historical_batch({"batchId": "test-v1", "honors": [record]})
        for members in ("not a list", [], ["甲"], ["甲", "乙", True], ["甲", "乙", {}],
                        ["甲", "乙", " "], ["甲", "乙", "字" * 151], ["甲", "甲", "乙"]):
            with self.subTest(members=members):
                result = self.request("confirm-members", {"honorId": "historical", "members": members}, cookie=cookie, csrf=csrf)
                self.assertEqual(result["status"], 400, result)
                payload = self.database.payload(self.seed)
                self.assertEqual(payload["members"], [])
                self.assertEqual(len(payload["pendingHonors"]), 1)

    def test_admin_can_correct_confirmed_roster_but_guest_and_csrf_missing_are_rejected(self):
        record = {"id": "historical", "event": "2018 ICPC", "date": "2018-10-01", "team": "Old Team", "medal": "金牌"}
        self.database.import_historical_batch({"batchId": "test-edit-v1", "honors": [record]})
        self.database.confirm_honor_members("historical", ["甲", "乙", "丙"])
        body = {"honorId": "historical", "members": ["甲", "丁", "丙"]}
        self.assertEqual(self.request("edit-members", body)["status"], 401)
        cookie, csrf = self.login()
        self.assertEqual(self.request("edit-members", body, cookie=cookie)["status"], 403)
        self.assertEqual(self.request("edit-members", body, cookie=cookie, csrf=csrf, origin="https://attacker.example")["status"], 403)
        self.assertEqual(self.request("edit-members", body, cookie=cookie, csrf=csrf)["status"], 200)
        self.assertEqual(self.database.payload(self.seed)["honors"][0]["members"], ["甲", "丁", "丙"])
        self.assertEqual(self.request("confirm-members", body, cookie=cookie, csrf=csrf)["status"], 400)

    def submission_fixture(self):
        record = {"id": "historical", "event": "2018 ICPC", "date": "2018-10-01", "team": "Old Team", "medal": "金牌"}
        self.database.import_historical_batch({"batchId": "test-v1", "honors": [record]})
        return {"honorId": "historical", "members": ["甲", "乙", "丙"], "note": "private evidence"}

    def test_guest_submission_then_admin_approval_requires_session_and_csrf(self):
        request = self.submission_fixture()
        result = self.request("/api/roster-submissions", {**request, "approve": True, "reviewer": "attacker"})
        self.assertEqual(result["status"], 201, result)
        submission = result["body"]["submissionId"]
        self.assertEqual(self.database.payload(self.seed)["members"], [])
        self.assertEqual(self.request("submissions", method="GET")["status"], 401)
        review = {"submissionId": submission, "approve": True, "reviewer": "attacker"}
        self.assertEqual(self.request("review-submission", review)["status"], 401)
        cookie, csrf = self.login()
        queue = self.request("submissions", method="GET", cookie=cookie)
        self.assertEqual(queue["status"], 200, queue)
        self.assertEqual(queue["body"]["submissions"][0]["note"], "private evidence")
        self.assertEqual(self.request("review-submission", review, cookie=cookie)["status"], 403)
        self.assertEqual(self.request("review-submission", review, cookie=cookie, csrf=csrf, origin="https://attacker.example")["status"], 403)
        result = self.request("review-submission", review, cookie=cookie, csrf=csrf)
        self.assertEqual(result["status"], 200, result)
        self.assertEqual(len(self.database.payload(self.seed)["members"]), 3)
        self.assertEqual(self.database.roster_submissions(status="approved")["submissions"][0]["reviewer"], "admin")
        self.assertEqual(self.request("review-submission", review, cookie=cookie, csrf=csrf)["status"], 400)

    def test_guest_account_submission_then_authenticated_approval_fetches_ratings(self):
        member = self.database.add_manual_member("队员")
        body = {"memberId": member, "handle": "Example", "note": "private evidence", "approve": True, "rating": 9999}
        with patch("tools.sync_codeforces.fetch_ratings") as fetch:
            result = self.request("/api/account-submissions", body)
            self.assertEqual(result["status"], 201)
            fetch.assert_not_called()
        submission = result["body"]["submissionId"]
        self.assertEqual(self.database.payload(self.seed)["members"][0]["accounts"], {})
        self.assertNotIn("private evidence", json.dumps(self.request("/api/site", method="GET")["body"]))
        self.assertEqual(self.request("submissions?kind=account", method="GET")["status"], 401)
        review = {"submissionId": submission, "approve": True, "reviewer": "attacker"}
        self.assertEqual(self.request("review-account-submission", review)["status"], 401)
        cookie, csrf = self.login()
        self.assertEqual(self.request("review-account-submission", review, cookie=cookie)["status"], 403)
        self.assertEqual(self.request("review-account-submission", review, cookie=cookie, csrf=csrf, origin="https://attacker.example")["status"], 403)
        queue = self.request("submissions?kind=account", method="GET", cookie=cookie)["body"]
        self.assertEqual((queue["kind"], queue["totalPendingCount"], queue["submissions"][0]["note"]), ("account", 1, "private evidence"))
        with patch("tools.sync_codeforces.fetch_ratings", return_value=[{"handle": "Example", "rating": 1800, "maxRating": 2300}]) as fetch:
            approved = self.request("review-account-submission", review, cookie=cookie, csrf=csrf)
            self.assertEqual(approved["status"], 200, approved)
            fetch.assert_called_once_with(["Example"])
        account = self.database.payload(self.seed)["members"][0]["handles"]["codeforces"]
        self.assertEqual((account["handle"], account["rating"], account["maxRating"]), ("Example", 1800, 2300))
        self.assertEqual(self.database.account_submissions(status="approved")["submissions"][0]["reviewer"], "admin")
        self.assertEqual(self.request("review-account-submission", review, cookie=cookie, csrf=csrf)["status"], 400)

    def test_guest_account_request_validation_duplicates_and_shared_rate_limiter(self):
        member = self.database.add_manual_member("队员")
        body = {"memberId": member, "handle": "Example"}
        for origin in ("", "https://attacker.example"):
            self.assertEqual(self.request("/api/account-submissions", body, origin=origin)["status"], 403)
        self.assertEqual(self.request("/api/account-submissions", body, headers={"Content-Type": "text/plain"})["status"], 400)
        for changes in ({"memberId": True}, {"memberId": 99999}, {"handle": "bad;handle"}):
            self.assertEqual(self.request("/api/account-submissions", {**body, **changes})["status"], 400)
        self.assertEqual(self.database.account_submissions()["total"], 0)
        first = self.request("/api/account-submissions", body)
        duplicate = self.request("/api/account-submissions", {**body, "handle": "eXample"})
        self.assertEqual((first["status"], duplicate["status"]), (201, 200))
        self.assertTrue(duplicate["body"]["duplicate"])
        self.assertEqual(first["body"]["submissionId"], duplicate["body"]["submissionId"])
        roster = self.submission_fixture()
        self.submission_limiter = app.SubmissionLimiter()
        for index in range(20):
            self.assertIn(self.request("/api/account-submissions" if index % 2 else "/api/roster-submissions", body if index % 2 else roster)["status"], (200, 201))
        self.assertEqual(self.request("/api/account-submissions", body)["status"], 429)
        self.assertEqual(self.request("/api/roster-submissions", roster)["status"], 429)

    def test_account_review_rejects_without_fetching_and_rating_failure_retains_approved_binding(self):
        member = self.database.add_manual_member("队员")
        rejected, _ = self.database.submit_account(member, "Wrong")
        approved, _ = self.database.submit_account(member, "Good")
        cookie, csrf = self.login()
        with patch("tools.sync_codeforces.fetch_ratings") as fetch:
            result = self.request("review-account-submission", {"submissionId": rejected, "approve": False}, cookie=cookie, csrf=csrf)
            self.assertEqual(result["status"], 200)
            fetch.assert_not_called()
        with patch("tools.sync_codeforces.fetch_ratings", side_effect=OSError("offline")):
            result = self.request("review-account-submission", {"submissionId": approved, "approve": True}, cookie=cookie, csrf=csrf)
        self.assertEqual(result["status"], 200)
        self.assertIn("offline", result["body"]["warning"])
        account = self.database.payload(self.seed)["members"][0]["handles"]["codeforces"]
        self.assertEqual(account["handle"], "Good")
        self.assertIsNone(account["rating"])
        self.assertEqual(self.request("submissions?kind=account&status=approved", method="GET", cookie=cookie)["body"]["total"], 1)
        self.assertEqual(self.request("submissions?kind=invalid", method="GET", cookie=cookie)["status"], 400)

    def test_guest_cross_origin_invalid_json_format_and_duplicates_are_safe(self):
        request = self.submission_fixture()
        self.assertEqual(self.request("/api/roster-submissions", request, origin="https://attacker.example")["status"], 403)
        self.assertEqual(self.request("/api/roster-submissions", request, origin="")["status"], 403)
        self.assertEqual(self.request("/api/roster-submissions", request, headers={"Content-Type": "text/plain"})["status"], 400)
        self.assertEqual(self.request("/api/roster-submissions", [request])["status"], 400)
        for changes in ({"members": ["甲", "甲", "丙"]}, {"members": ["甲", "乙", True]}, {"honorId": "missing"}):
            self.assertEqual(self.request("/api/roster-submissions", {**request, **changes})["status"], 400)
        self.assertEqual(self.database.roster_submissions()["total"], 0)
        first = self.request("/api/roster-submissions", request)
        duplicate = self.request("/api/roster-submissions", {**request, "members": ["丙", "乙", "甲"]})
        self.assertEqual((first["status"], duplicate["status"]), (201, 200))
        self.assertTrue(duplicate["body"]["duplicate"])
        self.assertEqual(first["body"]["submissionId"], duplicate["body"]["submissionId"])
        self.assertEqual(self.database.payload(self.seed)["members"], [])

    def test_guest_rate_limit_prevents_unbounded_requests(self):
        request = self.submission_fixture()
        for _ in range(20):
            self.assertIn(self.request("/api/roster-submissions", request)["status"], (200, 201))
        self.assertEqual(self.request("/api/roster-submissions", request)["status"], 429)
        self.assertEqual(self.database.roster_submissions()["total"], 1)

    def test_proxy_headers_used_for_rate_limit_only_when_explicitly_trusted(self):
        request = self.submission_fixture()
        for trusted, expected in (("0", "127.0.0.1"), ("1", "192.0.2.20")):
            with patch.dict("os.environ", {"TRUST_PROXY_HEADERS": trusted}), patch.object(self.submission_limiter, "allow", return_value=True) as allow:
                self.request("/api/roster-submissions", request, headers={"X-Forwarded-For": "192.0.2.1, 192.0.2.20"})
                allow.assert_called_once_with(expected)

    def test_admin_rejects_and_validates_decisions_and_queue_filters(self):
        request = self.submission_fixture()
        submission = self.request("/api/roster-submissions", request)["body"]["submissionId"]
        cookie, csrf = self.login()
        for changes in ({"approve": "yes"}, {"submissionId": True}, {"submissionId": 99999}):
            review = {"submissionId": submission, "approve": True, **changes}
            self.assertEqual(self.request("review-submission", review, cookie=cookie, csrf=csrf)["status"], 400)
        self.assertEqual(self.request("review-submission", {"submissionId": submission, "approve": False}, cookie=cookie, csrf=csrf)["status"], 200)
        self.assertEqual(self.database.payload(self.seed)["members"], [])
        self.assertEqual(self.request("submissions?status=rejected&page=1", method="GET", cookie=cookie)["body"]["total"], 1)
        for query in ("status=invalid", "page=no", "page=0", "school=other"):
            self.assertEqual(self.request(f"submissions?{query}", method="GET", cookie=cookie)["status"], 400)

    def test_guest_can_propose_even_if_admin_is_not_configured_yet(self):
        with patch.dict("os.environ", {"ADMIN_PASSWORD": "", "ADMIN_PASSWORD_FILE": str(self.root / "missing")}):
            self.auth = AdminAuth(self.root)
        request = self.submission_fixture()
        self.assertEqual(self.request("/api/roster-submissions", request)["status"], 201)
        self.assertEqual(self.database.payload(self.seed)["members"], [])

    def test_submission_limiter_expires_and_bounds_address_memory(self):
        limiter = app.SubmissionLimiter()
        self.assertTrue(all(limiter.allow("a", now=0) for _ in range(20)))
        self.assertFalse(limiter.allow("a", now=599))
        self.assertTrue(limiter.allow("a", now=600))
        self.assertTrue(limiter.allow("b", now=600))
        for index in range(4094):
            self.assertTrue(limiter.allow(str(index), now=600))
        self.assertFalse(limiter.allow("overflow", now=600))
        self.assertTrue(limiter.allow("overflow", now=1200))


if __name__ == "__main__":
    unittest.main()
