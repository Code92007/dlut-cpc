import copy
import json
import tempfile
import unittest
from pathlib import Path

from database import Database, load_seed_file


ROOT = Path(__file__).resolve().parents[1]


def historical_batch():
    return {
        "batchId": "roster-correction-history-v1",
        "source": "https://rl.algoux.cn/search",
        "honors": [
            {
                "id": "historic-one",
                "externalProvider": "rankland",
                "externalAwardId": "contest:team",
                "externalContestId": "contest",
                "externalTeamId": "team",
                "event": "2015 CCPC 南阳站",
                "series": "CCPC",
                "date": "2015-10-18",
                "team": "测试队",
                "medal": "银牌",
                "rank": "1 / 10",
                "school": "大连理工大学",
                "expectedMembers": 3,
                "source": {"name": "RankLand 历史榜单", "url": "https://rl.algoux.cn"},
                "suggestedMembers": [],
            }
        ],
    }


def correction():
    return {
        "id": "official-roster-v1",
        "honorId": "historic-one",
        "members": ["甲", "乙", "丙"],
        "source": {
            "name": "CCPC 官方参赛队伍信息",
            "url": "https://ccpc.io/post/78",
            "kind": "public",
            "priority": 80,
        },
    }


class RosterCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "site.sqlite3")

    def tearDown(self):
        self.temporary.cleanup()

    def seed(self, *, with_correction=True):
        value = {"meta": {}, "honors": [], "training": [], "historicalImports": [historical_batch()]}
        if with_correction:
            value["rosterCorrections"] = [correction()]
        return value

    def test_correction_confirms_pending_roster_and_adds_official_source(self):
        seed = self.seed()
        self.database.initialize(seed)
        payload = self.database.payload(seed)
        self.assertEqual(payload["honors"][0]["members"], ["甲", "乙", "丙"])
        self.assertEqual(payload["pendingHonors"], [])
        self.assertEqual(
            {item["name"] for item in payload["honors"][0]["sources"]},
            {"RankLand 历史榜单", "CCPC 官方参赛队伍信息"},
        )
        self.assertNotIn("rosterCorrections", payload)

    def test_correction_runs_once_and_does_not_overwrite_later_admin_edit(self):
        seed = self.seed()
        self.database.initialize(seed)
        self.database.edit_honor_members("historic-one", ["丁", "戊", "己"])
        self.database.initialize(seed)
        self.assertEqual(self.database.payload(seed)["honors"][0]["members"], ["丁", "戊", "己"])

    def test_first_correction_preserves_an_existing_confirmed_roster(self):
        base = self.seed(with_correction=False)
        self.database.initialize(base)
        self.database.confirm_honor_members("historic-one", ["原甲", "原乙", "原丙"])
        updated = copy.deepcopy(base)
        updated["rosterCorrections"] = [correction()]
        self.database.initialize(updated)
        self.assertEqual(self.database.payload(updated)["honors"][0]["members"], ["原甲", "原乙", "原丙"])
        with self.database.connect() as connection:
            value = json.loads(connection.execute(
                "SELECT value FROM metadata WHERE key='roster_correction:official-roster-v1'"
            ).fetchone()[0])
        self.assertEqual(value["status"], "preserved-existing")

    def test_real_seed_targets_only_the_two_awarded_2015_teams(self):
        seed = load_seed_file(ROOT / "data/site.json")
        corrections = {item["honorId"]: item for item in seed["rosterCorrections"]}
        self.assertEqual(
            corrections["rankland-09f023fcdc3a926331bf"]["members"],
            ["许思航", "邹家树", "刘庆周"],
        )
        self.assertEqual(
            corrections["rankland-c3f6dee6b9ed1c25e0b9"]["members"],
            ["马少楠", "熊昆", "刘小坤"],
        )
        self.assertEqual(len(corrections), 2)


if __name__ == "__main__":
    unittest.main()
