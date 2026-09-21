import json
import tempfile
import unittest
from pathlib import Path

from database import Database, load_seed_file
from official_imports import contest_key


ROOT = Path(__file__).resolve().parents[1]


class ICPCEarlyArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "site.sqlite3")
        self.database.initialize()
        self.batch = json.loads(
            (ROOT / "data/icpc_early_2007_2008_honors.json").read_text(encoding="utf-8")
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_archive_adds_six_results_with_recovered_team_names(self):
        report = self.database.merge_official_batch(self.batch)
        self.assertEqual((report["added"], report["merged"], report["conflict"]), (6, 0, 0))

        payload = self.database.payload({"meta": {}})
        honors = {item["id"]: item for item in payload["honors"]}
        self.assertEqual(len(honors), 6)
        self.assertEqual(
            {item["team"] for item in honors.values() if item["location"] == "合肥"},
            {"VIPers", "Random"},
        )
        self.assertEqual(honors["icpc-official-2007-chengdu-cippus-acfly"]["team"], "Cippus_ACFly")
        self.assertEqual(honors["icpc-official-2008-hefei-vipers"]["rank"], "16 / 103")
        self.assertEqual(honors["icpc-official-2008-hefei-random"]["rank"], "53 / 103")

    def test_changchun_honorable_mentions_are_normalized_to_iron(self):
        records = [record for record in self.batch["honors"] if record["location"] == "长春"]
        self.assertEqual(len(records), 2)
        self.assertTrue(all(record["medal"] == "铁牌" for record in records))
        self.assertTrue(all(record["archive"]["officialResultCategory"] == "Honorable Mention" for record in records))
        self.assertTrue(all(record["archive"]["rawSchoolAward"] == "二等奖" for record in records))

    def test_school_rosters_remain_pending_and_keep_all_sources(self):
        self.database.merge_official_batch(self.batch)
        payload = self.database.payload({"meta": {}})
        pending = {item["id"]: item for item in payload["pendingHonors"]}
        self.assertEqual(len(pending), 6)
        self.assertEqual(
            pending["icpc-official-2008-hefei-vipers"]["suggestedMembers"],
            ["金鑫", "李鹤", "徐宁"],
        )
        honor = next(item for item in payload["honors"] if item["id"] == "icpc-official-2008-hefei-vipers")
        source_names = {item["name"] for item in honor["sources"]}
        self.assertIn("中国科学技术大学 2008 合肥站官方榜单历史快照", source_names)
        self.assertIn("大连理工大学全国 ACM 获奖统计", source_names)
        self.assertIn("大工新闻网 2008 合肥站报道", source_names)

    def test_seed_loader_includes_the_recovery_batch(self):
        seed = load_seed_file(ROOT / "data/site.json")
        batch_ids = {batch["batchId"] for batch in seed["officialImports"]}
        self.assertIn(self.batch["batchId"], batch_ids)

    def test_chengdu_is_a_stable_contest_match_key(self):
        record = next(record for record in self.batch["honors"] if record["location"] == "成都")
        self.assertEqual(contest_key(record), ("ICPC", "2007", "成都"))


if __name__ == "__main__":
    unittest.main()
