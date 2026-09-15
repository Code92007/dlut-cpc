import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from database import Database, SCHEMA_VERSION


class AccountSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "site.sqlite3")
        self.seed = {"meta": {}, "honors": [], "training": []}
        self.database.initialize(self.seed)
        self.member = self.database.add_manual_member("队员甲", notes="private member note")
        self.other = self.database.add_manual_member("队员乙", school="大连理工大学城市学院")

    def tearDown(self):
        self.temporary.cleanup()

    def test_submission_stays_private_and_does_not_bind_or_modify_members(self):
        before = self.database.payload(self.seed)
        submission, created = self.database.submit_account(self.member, "Example", note="private evidence")
        self.assertTrue(created)
        self.assertEqual(self.database.payload(self.seed), before)
        self.assertNotIn("private evidence", json.dumps(before))
        item = self.database.review_submissions(kind="account")["submissions"][0]
        self.assertEqual((item["id"], item["memberName"], item["note"]), (submission, "队员甲", "private evidence"))
        self.assertEqual(item["existingAccounts"], [])

    def test_duplicate_handles_ignore_case_and_whitespace(self):
        first = self.database.submit_account(self.member, " Example ")
        duplicate = self.database.submit_account(self.member, "eXample", note="different evidence")
        self.assertEqual(duplicate, (first[0], False))
        self.assertEqual(self.database.account_submissions()["total"], 1)

    def test_approval_binds_without_removing_other_accounts_and_selects_main_by_max_rating(self):
        self.database.set_handle(self.member, "codeforces", "Existing")
        self.database.update_account_ratings([{"handle": "Existing", "rating": 1900, "maxRating": 2500}], "2026-09-15T01:00:00+00:00")
        submission, _ = self.database.submit_account(self.member, "Additional")
        self.assertEqual(self.database.review_account_submission(submission, True, reviewer="admin"), "Additional")
        self.database.update_account_ratings([{"handle": "Additional", "rating": 2300, "maxRating": 2400}], "2026-09-15T02:00:00+00:00")
        member = next(item for item in self.database.payload(self.seed)["members"] if item["id"] == self.member)
        self.assertEqual([item["handle"] for item in member["accounts"]["codeforces"]], ["Existing", "Additional"])
        approved = self.database.account_submissions(status="approved")["submissions"][0]
        self.assertEqual(approved["reviewer"], "admin")
        self.assertTrue(approved["reviewedAt"])
        self.assertEqual(approved["existingAccounts"], ["Existing", "Additional"])
        with self.assertRaises(ValueError):
            self.database.review_account_submission(submission, True, reviewer="admin")

    def test_rejection_is_recorded_and_does_not_modify_public_data(self):
        before = self.database.payload(self.seed)
        submission, _ = self.database.submit_account(self.member, "Example")
        self.database.review_account_submission(submission, False, reviewer="admin", reason="wrong account")
        self.assertEqual(self.database.payload(self.seed), before)
        rejected = self.database.account_submissions(status="rejected")["submissions"][0]
        self.assertEqual(rejected["reviewNote"], "wrong account")
        self.assertEqual(self.database.account_submissions()["total"], 0)

    def test_existing_owner_cannot_be_reassigned_by_a_submission(self):
        self.database.set_handle(self.other, "codeforces", "Owned")
        for member in (self.member, self.other):
            with self.assertRaises(ValueError):
                self.database.submit_account(member, "oWned")
        self.assertEqual(self.database.account_submissions()["total"], 0)

    def test_approval_rechecks_ownership_and_rolls_back_if_admin_has_bound_account_elsewhere(self):
        submission, _ = self.database.submit_account(self.member, "Example")
        self.database.set_handle(self.member, "codeforces", "Example")
        self.database.delete_handle(self.member, "codeforces", "Example")
        self.database.set_handle(self.other, "codeforces", "Example")
        with self.assertRaises(ValueError):
            self.database.review_account_submission(submission, True, reviewer="admin")
        self.assertEqual(self.database.account_submissions()["total"], 1)
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT member_id FROM member_handles WHERE handle='Example'").fetchone()[0], self.other)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM removed_member_handles WHERE member_id=?", (self.member,)).fetchone()[0], 1)

    def test_concurrent_conflicting_approvals_never_attach_one_account_to_two_people(self):
        submissions = [self.database.submit_account(member, "Shared")[0] for member in (self.member, self.other)]
        def approve(submission):
            try:
                self.database.review_account_submission(submission, True, reviewer="admin")
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(approve, submissions)), [False, True])
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM member_handles WHERE handle='Shared'").fetchone()[0], 1)

    def test_invalid_inputs_do_not_create_members_or_proposals(self):
        for member, handle, note in [(True, "Good", ""), (0, "Good", ""), (99999, "Good", ""),
                                     (self.member, "bad;handle", ""), (self.member, "", ""),
                                     (self.member, None, ""), (self.member, "x" * 101, ""),
                                     (self.member, "Good", "x" * 2001), (self.member, "Good", None)]:
            with self.assertRaises(ValueError):
                self.database.submit_account(member, handle, note=note)
        self.assertEqual(len(self.database.payload(self.seed)["members"]), 2)
        self.assertEqual(self.database.account_submissions()["total"], 0)

    def test_member_and_total_queue_limits_and_pagination(self):
        for index in range(5):
            self.database.submit_account(self.member, f"Example{index}")
        with self.assertRaises(ValueError):
            self.database.submit_account(self.member, "Overflow")
        with self.database.connect() as connection:
            connection.executemany("INSERT INTO account_submissions(member_id, handle) VALUES (?, ?)",
                                   [(self.other, f"Other{index}") for index in range(995)])
        self.assertEqual(self.database.review_submissions(kind="roster")["totalPendingCount"], 1000)
        with self.assertRaisesRegex(ValueError, "队列已满"):
            self.database.submit_account(self.database.add_manual_member("第三位"), "Overflow")
        page = self.database.account_submissions(page=2)
        self.assertEqual((page["total"], page["page"], page["pages"], len(page["submissions"])), (1000, 2, 20, 50))
        city = self.database.account_submissions(school="大连理工大学城市学院")
        self.assertEqual(city["total"], 995)
        self.assertTrue(all(item["memberId"] == self.other for item in city["submissions"]))

    def test_queue_filters_and_review_parameters_are_validated(self):
        submission, _ = self.database.submit_account(self.member, "Example")
        for kwargs in ({"status": "invalid"}, {"school": "invalid"}, {"page": 0}, {"page": True}, {"kind": "invalid"}):
            with self.assertRaises(ValueError):
                self.database.review_submissions(**kwargs)
        for kwargs in ({"submission_id": True}, {"approve": "yes"}, {"reviewer": ""}, {"reason": "x" * 2001}):
            with self.assertRaises(ValueError):
                self.database.review_account_submission(**{"submission_id": submission, "approve": True, "reviewer": "admin", **kwargs})

    def test_schema_upgrade_and_resync_preserve_proposals_and_approved_binding(self):
        self.database.set_handle(self.member, "codeforces", "Existing", rating=1800)
        with self.database.connect() as connection:
            connection.execute("DROP TABLE account_submissions")
            connection.execute("PRAGMA user_version=7")
        self.database.initialize(self.seed)
        submission, _ = self.database.submit_account(self.member, "Example", note="private evidence")
        self.database.initialize(self.seed)
        self.assertEqual(self.database.account_submissions()["submissions"][0]["note"], "private evidence")
        self.database.review_account_submission(submission, True, reviewer="admin")
        self.database.sync_site_data(self.seed)
        self.assertEqual(self.database.account_submissions(status="approved")["total"], 1)
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM member_handles WHERE member_id=?", (self.member,)).fetchone()[0], 2)
