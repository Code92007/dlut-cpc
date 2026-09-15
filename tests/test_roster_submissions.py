import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from database import Database, SCHEMA_VERSION


def record(identifier="historical", school="大连理工大学"):
    return {"id": identifier, "event": "2018 ICPC Regional", "date": "2018-10-01", "series": "ICPC",
            "team": identifier, "medal": "金牌", "school": school, "expectedMembers": 3}


class RosterSubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "test.sqlite3")
        self.seed = {"meta": {}, "honors": [], "training": []}
        self.database.initialize(self.seed)
        self.database.import_historical_batch({"batchId": "test-v1", "honors": [record(), record("panjin", "大连理工大学盘锦校区")]})

    def tearDown(self):
        self.temporary.cleanup()

    def test_submission_is_private_and_never_changes_formal_members_or_medals(self):
        before = self.database.payload(self.seed)
        submission, created = self.database.submit_roster("historical", ["甲", "乙", "丙"], note="private evidence")
        self.assertTrue(created)
        after = self.database.payload(self.seed)
        self.assertEqual(after["members"], before["members"])
        self.assertEqual(after["medalSummary"], before["medalSummary"])
        self.assertTrue(all(not h["members"] for h in after["honors"]))
        self.assertNotIn("private evidence", json.dumps(after))
        self.assertNotIn("rosterSubmissions", after)
        honor = next(h for h in after["honors"] if h["id"] == "historical")
        self.assertEqual(honor["pendingSubmissionCount"], 1)
        queue = self.database.roster_submissions()
        self.assertEqual(queue["pendingCount"], 1)
        self.assertEqual(queue["submissions"][0]["id"], submission)
        self.assertEqual(queue["submissions"][0]["note"], "private evidence")

    def test_approval_merges_or_creates_and_survives_restart(self):
        he = self.database.add_manual_member("何泾", notes="keep this note")
        fu = self.database.add_manual_member("傅心语")
        submission, _ = self.database.submit_roster("historical", ["董霄然", "傅心语", he])
        self.assertEqual(len(self.database.payload(self.seed)["members"]), 2)
        self.database.review_roster_submission(submission, True, reviewer="admin")
        self.database.initialize(self.seed)
        payload = self.database.payload(self.seed)
        self.assertEqual(len(payload["members"]), 3)
        honor = next(h for h in payload["honors"] if h["id"] == "historical")
        self.assertEqual(honor["members"], ["董霄然", "傅心语", "何泾"])
        self.assertEqual(honor["medal"], "金牌")
        self.assertEqual(honor["memberDetails"][1]["id"], fu)
        self.assertTrue(all(m["medals"]["gold"] == 1 for m in payload["members"]))
        self.assertEqual(self.database.roster_submissions()["total"], 0)
        approved = self.database.roster_submissions(status="approved")["submissions"][0]
        self.assertEqual(approved["reviewer"], "admin")
        self.assertTrue(approved["reviewedAt"])
        self.assertTrue(all(not m["newMember"] and type(m["value"]) is int for m in approved["members"]))
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT notes FROM members WHERE id=?", (he,)).fetchone()[0], "keep this note")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM honor_members WHERE is_manual=1").fetchone()[0], 3)

    def test_rejection_keeps_evidence_but_changes_no_formal_data(self):
        submission, _ = self.database.submit_roster("historical", ["甲", "乙", "丙"], note="evidence")
        before = self.database.payload(self.seed)
        self.database.review_roster_submission(submission, False, reviewer="admin", reason="wrong roster")
        after = self.database.payload(self.seed)
        self.assertEqual(after["members"], before["members"])
        self.assertEqual(after["medalSummary"], before["medalSummary"])
        self.assertEqual(len(after["pendingHonors"]), 2)
        rejected = self.database.roster_submissions(status="rejected")["submissions"][0]
        self.assertEqual((rejected["note"], rejected["reviewNote"]), ("evidence", "wrong roster"))
        with self.assertRaisesRegex(ValueError, "已处理"):
            self.database.review_roster_submission(submission, True, reviewer="admin")
        self.assertTrue(self.database.submit_roster("historical", ["甲", "乙", "丙"])[1])

    def test_duplicate_deduplicates_order_whitespace_alias_and_member_id(self):
        a = self.database.add_manual_member("甲")
        self.database.set_display_name(a, "甲", ["Registration Name"])
        first, _ = self.database.submit_roster("historical", ["甲", "乙", "丙"])
        for roster in ([" 丙 ", a, "乙"], ["乙", " registration name ", "丙"]):
            self.assertEqual(self.database.submit_roster("historical", roster), (first, False))
        self.assertEqual(self.database.roster_submissions()["total"], 1)

    def test_invalid_inputs_and_scope_never_create_proposals_or_people(self):
        city = self.database.add_manual_member("城市成员", school="大连理工大学城市学院")
        for roster in (None, [], ["甲"], ["甲", "甲", "乙"], ["甲", "乙", True],
                       ["甲", "乙", " "], ["甲", "乙", "字" * 151], ["甲", "乙", city], ["甲", "乙", 99999]):
            with self.subTest(roster=roster), self.assertRaises(ValueError):
                self.database.submit_roster("historical", roster)
        with self.assertRaises(ValueError):
            self.database.submit_roster("missing", ["甲", "乙", "丙"])
        with self.assertRaises(ValueError):
            self.database.submit_roster("historical", ["甲", "乙", "丙"], note="字" * 2001)
        self.assertEqual(self.database.roster_submissions()["total"], 0)
        self.assertEqual(len(self.database.payload(self.seed)["members"]), 1)

    def test_ambiguous_names_require_selection_and_existing_ids_are_frozen(self):
        a = self.database.add_manual_member("同名")
        b = self.database.add_manual_member("同名")
        with self.assertRaisesRegex(ValueError, "具体成员 ID"):
            self.database.submit_roster("historical", ["同名", "乙", "丙"])
        submission, _ = self.database.submit_roster("historical", [a, b, "丙"])
        self.database.set_display_name(a, "甲")
        self.database.review_roster_submission(submission, True, reviewer="admin")
        honor = next(h for h in self.database.payload(self.seed)["honors"] if h["id"] == "historical")
        self.assertEqual([m["id"] for m in honor["memberDetails"][:2]], [a, b])
        self.assertEqual(honor["members"], ["甲", "同名", "丙"])

    def test_approval_of_one_proposal_supersedes_others_without_overwriting(self):
        a, _ = self.database.submit_roster("historical", ["甲", "乙", "丙"])
        b, _ = self.database.submit_roster("historical", ["丁", "戊", "己"])
        self.database.review_roster_submission(a, True, reviewer="admin")
        self.assertEqual(self.database.roster_submissions(status="superseded")["submissions"][0]["id"], b)
        for submission in (a, b):
            with self.assertRaises(ValueError):
                self.database.review_roster_submission(submission, True, reviewer="admin")
        with self.assertRaises(ValueError):
            self.database.submit_roster("historical", ["丁", "戊", "己"])
        self.assertEqual(len(self.database.payload(self.seed)["members"]), 3)
        self.assertEqual(len(self.database.payload(self.seed)["honors"]), 2)

    def test_direct_admin_confirmation_supersedes_guest_proposals(self):
        submission, _ = self.database.submit_roster("historical", ["甲", "乙", "丙"])
        self.database.confirm_honor_members("historical", ["丁", "戊", "己"])
        with self.assertRaises(ValueError):
            self.database.review_roster_submission(submission, True, reviewer="admin")
        self.assertEqual(self.database.roster_submissions(status="superseded")["total"], 1)
        self.assertEqual(len(self.database.payload(self.seed)["members"]), 3)

    def test_approval_error_rolls_back_new_people_and_leaves_proposal_pending(self):
        submission, _ = self.database.submit_roster("historical", ["新成员", "同名", "第三人"])
        self.database.add_manual_member("同名")
        self.database.add_manual_member("同名")
        with self.assertRaisesRegex(ValueError, "具体成员 ID"):
            self.database.review_roster_submission(submission, True, reviewer="admin")
        self.assertEqual(len(self.database.payload(self.seed)["members"]), 2)
        self.assertEqual(self.database.roster_submissions()["total"], 1)
        self.assertTrue(all(not h["members"] for h in self.database.payload(self.seed)["honors"]))

    def test_independent_group_inherited_on_approval_without_merging_other_school(self):
        main = self.database.add_manual_member("甲")
        submission, _ = self.database.submit_roster("panjin", ["甲", "乙", "丙"])
        self.database.review_roster_submission(submission, True, reviewer="admin")
        members = self.database.payload(self.seed)["members"]
        self.assertEqual(len(members), 4)
        self.assertEqual(next(m for m in members if m["id"] == main)["medals"]["gold"], 0)
        self.assertEqual(len([m for m in members if m["school"] == "大连理工大学盘锦校区"]), 3)

    def test_concurrent_duplicate_submission_and_conflicting_approval_are_serialized(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.database.submit_roster("historical", ["甲", "乙", "丙"]), range(2)))
        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sum(created for _, created in results), 1)
        other, _ = self.database.submit_roster("historical", ["丁", "戊", "己"])
        def approve(submission):
            try:
                self.database.review_roster_submission(submission, True, reviewer="admin")
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            self.assertEqual(sum(executor.map(approve, [results[0][0], other])), 1)
        self.assertEqual(len(self.database.payload(self.seed)["members"]), 3)

    def test_queue_filters_pagination_and_history_are_persistent(self):
        records = [record(f"more-{index}") for index in range(51)]
        self.database.import_historical_batch({"batchId": "many-v1", "honors": records})
        for item in records:
            self.database.submit_roster(item["id"], ["甲", "乙", "丙"])
        self.database.submit_roster("panjin", ["甲", "乙", "丙"])
        self.database.initialize(self.seed)
        first = self.database.roster_submissions(school="大连理工大学")
        last = self.database.roster_submissions(page=999, school="大连理工大学")
        self.assertEqual((first["total"], first["pages"], len(first["submissions"])), (51, 2, 50))
        self.assertEqual((last["page"], len(last["submissions"])), (2, 1))
        self.assertEqual(self.database.roster_submissions(school="大连理工大学盘锦校区")["total"], 1)
        for changes in ({"status": "invalid"}, {"page": 0}, {"school": "other"}):
            with self.assertRaises(ValueError):
                self.database.roster_submissions(**changes)

    def test_per_honor_and_global_queue_are_bounded(self):
        for index in range(5):
            self.database.submit_roster("historical", [f"甲{index}", "乙", "丙"])
        with self.assertRaisesRegex(ValueError, "多份名单"):
            self.database.submit_roster("historical", ["其他", "乙", "丙"])
        with self.database.connect() as connection:
            connection.executemany("INSERT INTO roster_submissions(honor_id, members_json, fingerprint) VALUES ('historical', '[]', ?)",
                                   [(f"fixture-{index}",) for index in range(995)])
        with self.assertRaisesRegex(ValueError, "队列已满"):
            self.database.submit_roster("panjin", ["甲", "乙", "丙"])

    def test_v5_upgrade_creates_queue_without_losing_existing_member_data(self):
        member = self.database.add_manual_member("甲")
        with self.database.connect() as connection:
            connection.execute("DROP TABLE roster_submissions")
            connection.execute("PRAGMA user_version=5")
        self.database.initialize(self.seed)
        self.assertEqual(self.database.roster_submissions()["total"], 0)
        self.assertEqual(self.database.payload(self.seed)["members"][0]["id"], member)
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
