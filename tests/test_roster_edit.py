import copy
import tempfile
import unittest
from pathlib import Path

from database import Database


class RosterEditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "site.sqlite3")
        self.honor = {"id": "old", "event": "2018 ICPC", "date": "2018-10-01", "team": "Old Team",
                      "series": "ICPC", "medal": "金牌", "rank": "10 / 100", "externalProvider": "rankland",
                      "source": {"name": "RankLand", "url": "https://example.com/old"}}
        self.seed = {"meta": {}, "honors": [], "historicalImports": [{"batchId": "test-v1", "honors": [self.honor]}]}
        self.database.initialize(self.seed)

    def tearDown(self):
        self.temporary.cleanup()

    def payload(self):
        return self.database.payload(self.seed)

    def test_confirmed_historical_roster_can_be_corrected_and_stats_follow_links(self):
        self.database.confirm_honor_members("old", ["甲", "乙", "丙"])
        before = self.payload()
        ids = {member["name"]: member["id"] for member in before["members"]}
        self.assertTrue(before["honors"][0]["rosterEditable"])
        self.database.set_handle(ids["乙"], "codeforces", "KeepAccount")
        self.database.edit_honor_members("old", [ids["甲"], "丁", "丙"])
        self.database.initialize(self.seed)
        payload = self.payload()
        honor = payload["honors"][0]
        self.assertEqual(honor["members"], ["甲", "丁", "丙"])
        self.assertEqual(payload["pendingHonors"], [])
        for key in ("id", "event", "date", "team", "series", "medal", "rank", "source"):
            self.assertEqual(honor[key], before["honors"][0][key])
        members = {member["name"]: member for member in payload["members"]}
        self.assertEqual(members["甲"]["id"], ids["甲"])
        self.assertEqual(members["乙"]["medals"]["gold"], 0)
        self.assertEqual(members["乙"]["honorCount"], 0)
        self.assertEqual(members["乙"]["handles"]["codeforces"]["handle"], "KeepAccount")
        self.assertEqual(members["丁"]["medals"]["gold"], 1)

    def test_corrected_and_confirmed_rosters_survive_a_stale_full_snapshot(self):
        self.database.confirm_honor_members("old", ["甲", "乙", "丙"])
        stale = copy.deepcopy(self.seed)
        stale["honors"] = [{**self.honor, "members": ["甲", "错误乙", "丙"]}]
        self.database.initialize(stale)
        self.assertEqual(self.payload()["honors"][0]["members"], ["甲", "乙", "丙"])
        self.database.edit_honor_members("old", ["甲", "丁", "丙"])
        self.database.initialize(stale)
        self.assertEqual(self.payload()["honors"][0]["members"], ["甲", "丁", "丙"])
        self.assertNotIn("错误乙", [member["name"] for member in self.payload()["members"]])

    def test_invalid_edit_rolls_back_new_people_and_keeps_previous_roster(self):
        self.database.confirm_honor_members("old", ["甲", "乙", "丙"])
        city = self.database.add_manual_member("城市成员", school="大连理工大学城市学院")
        for members in (["新甲", "新乙", True], ["新甲", "新乙", city], ["新甲", "新乙", 99999],
                        ["新甲", "新甲", "新丙"], ["新甲"], [], None):
            with self.subTest(members=members):
                with self.assertRaises(ValueError):
                    self.database.edit_honor_members("old", members)
                payload = self.payload()
                self.assertEqual(payload["honors"][0]["members"], ["甲", "乙", "丙"])
                self.assertEqual(len(payload["members"]), 4)

    def test_pending_or_nonexistent_rosters_cannot_use_edit_endpoint(self):
        for honor_id in ("old", "missing"):
            with self.assertRaises(ValueError):
                self.database.edit_honor_members(honor_id, ["甲", "乙", "丙"])
        self.assertEqual(self.payload()["members"], [])

    def test_manual_one_member_roster_can_be_corrected_without_changing_count(self):
        first = self.database.add_manual_member("甲")
        honor_id = self.database.add_manual_honor_with_members({"event": "Old Solo", "date": "2008-01-01",
                                                              "team": "Solo", "medal": "铜牌"}, [first])
        self.database.edit_honor_members(honor_id, ["乙"])
        honor = next(item for item in self.payload()["honors"] if item["id"] == honor_id)
        self.assertEqual(honor["members"], ["乙"])
        self.assertTrue(honor["rosterEditable"])

    def test_untouched_public_cpcfinder_roster_not_in_local_edit_module(self):
        self.database.initialize({"honors": [{"id": "public", "event": "ICPC", "team": "Team", "date": "2025-01-01",
                                               "medal": "银牌", "members": ["甲", "乙", "丙"]}]})
        honor = next(item for item in self.payload()["honors"] if item["id"] == "public")
        self.assertFalse(honor["rosterEditable"])
        with self.assertRaises(ValueError):
            self.database.edit_honor_members("public", ["甲", "丁", "丙"])
