import copy
import json
import tempfile
import unittest
from pathlib import Path

from database import Database, load_seed_file


ROOT = Path(__file__).resolve().parents[1]


class MedalCorrectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "site.sqlite3")
        self.seed = {"meta": {}, "honors": [{"id": "unknown", "event": "2010 ICPC 哈尔滨站", "series": "ICPC",
                    "date": "2010-09-26", "team": "预知", "medal": "", "official": True,
                    "members": [], "source": {"name": "RankLand 历史榜单"}}],
                    "honorMedalCorrections": [{"id": "confirm-silver", "honorId": "unknown", "medal": "银牌",
                    "source": {"name": "队内确认（2026-11-08）", "kind": "manual"}}]}

    def tearDown(self):
        self.temporary.cleanup()

    def test_medal_confirmation_updates_history_but_keeps_roster_pending(self):
        self.database.initialize(self.seed)
        payload = self.database.payload(self.seed)
        self.assertEqual(payload["honors"][0]["medal"], "银牌")
        self.assertEqual(payload["honors"][0]["members"], [])
        self.assertFalse(payload["honors"][0]["rosterConfirmed"])
        self.assertEqual(payload["pendingHonors"][0]["medal"], "银牌")
        self.assertEqual(payload["medalSummary"], [{"year": "2010", "gold": 0, "silver": 1, "bronze": 0, "iron": 0}])
        self.assertNotIn("honorMedalCorrections", payload)
        self.database.initialize(self.seed)
        self.assertEqual(self.database.payload(self.seed)["medalSummary"], payload["medalSummary"])

    def test_existing_medal_and_later_admin_edit_are_preserved(self):
        seed = copy.deepcopy(self.seed)
        seed["honors"][0]["medal"] = "金牌"
        self.database.initialize(seed)
        self.assertEqual(self.database.payload(seed)["honors"][0]["medal"], "金牌")
        with self.database.connect() as connection:
            marker = json.loads(connection.execute("SELECT value FROM metadata WHERE key='medal_correction:confirm-silver'").fetchone()[0])
            self.assertEqual(marker["status"], "preserved-existing")
            connection.execute("UPDATE honors SET medal='铜牌',is_manual=1 WHERE id='unknown'")
        self.database.initialize(self.seed)
        self.assertEqual(self.database.payload(self.seed)["honors"][0]["medal"], "铜牌")

    def test_repository_corrections_add_one_silver_three_bronze_one_iron(self):
        seed = load_seed_file(ROOT / "data/site.json")
        corrections = seed.pop("honorMedalCorrections")
        self.database.initialize(seed)
        before = self.database.payload({"meta": {}})
        seed["honorMedalCorrections"] = corrections
        self.database.initialize(seed)
        after = self.database.payload({"meta": {}})
        expected = {"预知": "银牌", "2012": "铜牌", "不给力": "铜牌", "害羞幽灵": "铜牌", "One Minute Lost": "铁牌"}
        targets = {c["honorId"] for c in corrections}
        for h in after["honors"]:
            if h["id"] in targets:
                self.assertEqual(h["medal"], expected[h["team"]])
                self.assertFalse(h["rosterConfirmed"])
                self.assertEqual(h["members"], [])
        old = {row["year"]: row for row in before["medalSummary"]}
        new = {row["year"]: row for row in after["medalSummary"]}
        for year, deltas in {"2010": {"silver": 1, "bronze": 2}, "2018": {"bronze": 1, "iron": 1}}.items():
            for field in ("gold", "silver", "bronze", "iron"):
                self.assertEqual(new[year][field] - old[year][field], deltas.get(field, 0))
        self.assertEqual(len(after["honors"]), len(before["honors"]))
        self.assertEqual(len(after["pendingHonors"]), len(before["pendingHonors"]))


if __name__ == "__main__":
    unittest.main()
