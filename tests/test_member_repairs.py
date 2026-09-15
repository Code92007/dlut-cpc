import copy
import tempfile
import unittest
from pathlib import Path

from database import Database


PANJIN = "大连理工大学盘锦校区"


def person(identity, name="陈嘉佑", school=PANJIN, rating=0):
    return {"name": name, "school": school, "provider": "cpcfinder", "externalId": identity,
            "source": {"name": "CPC Finder 选手库", "url": f"https://cpcfinder.com/student/{identity}"},
            "cpcfinder": {"rating": rating, "bronzeCount": 1, "ironCount": 2}}


def honor(identifier, member, medal="铜牌", official=True):
    return {"id": identifier, "cpcfinderAwardId": identifier, "event": "2024 ICPC Regional", "series": "ICPC",
            "date": "2024-11-01", "team": identifier, "medal": medal, "official": official,
            "members": [member["name"]], "memberDetails": [member], "source": {"name": "CPC Finder"}}


class MemberRepairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temporary.name) / "site.sqlite3")
        self.db.initialize()

    def tearDown(self):
        self.temporary.cleanup()

    def legacy_duplicates(self):
        ids = [self.db.add_manual_member("陈嘉佑", school=PANJIN) for _ in range(2)]
        with self.db.connect() as connection:
            source = self.db._source(connection, {"name": "CPC Finder"})
            for identity, member_id in zip(("one", "two"), ids):
                connection.execute("INSERT INTO member_identities(provider,external_id,member_id) VALUES ('cpcfinder',?,?)", (identity, member_id))
                self.db._set_handle(connection, member_id, "codeforces", {"handle": f"account_{identity}", "maxRating": 2000}, source)
        return ids

    def test_legacy_merge_preserves_membership_accounts_sources_and_local_fields(self):
        first, second = self.legacy_duplicates()
        self.db.set_display_name(second, "陈嘉佑", ["English Registration"])
        with self.db.connect() as connection:
            connection.execute("UPDATE members SET notes='manual profile', entry_year=2022 WHERE id=?", (second,))
        seed = {"publicMembers": [person("one", rating=130), person("two")],
                "honors": [honor("a", person("one")), honor("b", person("two"))]}
        self.db.initialize(seed)
        payload = self.db.payload({**seed, "meta": {}})
        self.assertEqual(len(payload["members"]), 1)
        member = payload["members"][0]
        self.assertEqual(member["id"], first)
        self.assertEqual(member["medals"]["bronze"], 2)
        self.assertEqual(member["honorCount"], 2)
        self.assertEqual(member["cpcfinder"]["rating"], 130)
        self.assertEqual(len(member["accounts"]["codeforces"]), 2)
        self.assertIn("English Registration", member["aliases"])
        self.assertTrue(all(len(h["members"]) == 1 for h in payload["honors"]))
        self.assertEqual(self.db.submit_account(second, "new_account")[1], True)
        self.assertEqual(self.db.account_submissions()["submissions"][0]["memberId"], first)
        with self.db.connect() as connection:
            self.assertEqual(connection.execute("SELECT notes FROM members WHERE id=?", (first,)).fetchone()[0], "manual profile")
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.db.initialize(seed)
        self.assertEqual(len(self.db.payload({**seed, "meta": {}})["members"]), 1)

    def test_new_sync_merges_panjin_but_not_other_campuses_or_other_same_names(self):
        people = [person("one"), person("two"), person("main-a", school="大连理工大学"),
                  person("main-b", school="大连理工大学"), person("city", school="大连理工大学城市学院")]
        self.db.initialize({"publicMembers": people, "honors": [honor(p["externalId"], p) for p in people]})
        members = self.db.payload({"meta": {}})["members"]
        self.assertEqual(len(members), 4)
        panjin = next(m for m in members if m["school"] == PANJIN)
        self.assertEqual(panjin["medals"]["bronze"], 2)
        self.assertEqual(len([m for m in members if m["school"] == "大连理工大学"]), 2)

    def test_merge_preserves_pending_approvals_and_supersedes_only_duplicate_accounts(self):
        first, second = self.legacy_duplicates()
        self.db.submit_account(first, "same")
        self.db.submit_account(second, "same")
        self.db.submit_account(second, "different")
        self.db.initialize()
        queue = self.db.account_submissions(status="all")["submissions"]
        self.assertEqual(len(queue), 3)
        self.assertEqual(sum(s["status"] == "pending" for s in queue), 2)
        self.assertTrue(all(s["memberId"] == first for s in queue))
        self.db.review_account_submission(next(s["id"] for s in queue if s["handle"] == "different"), True, reviewer="admin")

    def test_merge_preserves_roster_approvals_and_deleted_account_tombstones(self):
        first, second = self.legacy_duplicates()
        self.db.import_historical_batch({"batchId": "panjin-history", "honors": [
            {"id": "pending", "event": "2018 ICPC Regional", "date": "2018-10-01", "series": "ICPC",
             "team": "pending", "medal": "金牌", "school": PANJIN, "expectedMembers": 3}]})
        first_request, _ = self.db.submit_roster("pending", [first, "甲", "乙"])
        self.db.submit_roster("pending", [second, "甲", "乙"])
        self.db.delete_handle(second, "codeforces", "account_two")
        self.db.initialize()
        queue = self.db.roster_submissions(status="all")["submissions"]
        self.assertEqual(len(queue), 2)
        self.assertEqual(sum(s["status"] == "pending" for s in queue), 1)
        self.assertTrue(all(s["members"][0]["value"] == first for s in queue))
        self.db.review_roster_submission(first_request, True, reviewer="admin")
        self.db.initialize({"publicMembers": [{**person("two"), "handles": {"codeforces": {"handle": "account_two"}}}]})
        payload = self.db.payload({"meta": {}})
        self.assertEqual(payload["honors"][0]["members"], ["陈嘉佑", "甲", "乙"])
        self.assertNotIn("account_two", [h["handle"] for m in payload["members"] for h in m["accounts"].get("codeforces", [])])

    def test_nonofficial_results_are_blank_or_starred_and_never_counted(self):
        member = person("one", school="大连理工大学")
        seed = {"publicMembers": [member], "honors": [honor("star", member, "铁牌", False), honor("silver", member, "银牌", False)]}
        self.db.initialize(seed)
        payload = self.db.payload({**seed, "meta": {}})
        self.assertEqual(payload["medalSummary"], [])
        results = {h["id"]: h for h in payload["honors"]}
        self.assertEqual(results["star"]["medal"], "")
        self.assertEqual(results["star"]["resultLabel"], "")
        self.assertEqual(results["silver"]["resultLabel"], "打星银牌")
        self.assertEqual(payload["members"][0]["medals"]["iron"], 1)
        seed = copy.deepcopy(seed)
        seed["publicMembers"][0]["cpcfinder"].update(ironCount=1, ironExcludesUnofficial=True)
        self.db.initialize(seed)
        self.assertEqual(self.db.payload({**seed, "meta": {}})["members"][0]["medals"]["iron"], 1)


if __name__ == "__main__":
    unittest.main()
