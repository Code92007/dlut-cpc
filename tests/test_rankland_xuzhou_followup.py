import json
import tempfile
import unittest
from pathlib import Path

from database import Database, load_seed_file


ROOT = Path(__file__).resolve().parents[1]


class RankLandXuzhouFollowupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "site.sqlite3")
        self.database.initialize({"meta": {}, "honors": [], "training": []})
        self.batch = json.loads((ROOT / "data/rankland_xuzhou_2018_honors.json").read_text(encoding="utf-8"))

    def tearDown(self):
        self.temporary.cleanup()

    def test_dingguagua_is_added_as_iron_with_confirmable_roster(self):
        report = self.database.merge_official_batch(self.batch)
        self.assertEqual((report["added"], report["merged"], report["conflict"]), (1, 0, 0))
        payload = self.database.payload({"meta": {}})
        result = payload["honors"][0]
        self.assertEqual((result["team"], result["medal"], result["rank"]),
                         ("顶呱呱 (Dingguagua)", "铁牌", "188 / 278"))
        self.assertEqual(payload["pendingHonors"][0]["suggestedMembers"], ["吴将凯", "胡小涛", "钱昕予"])

    def test_xuzhou_identity_does_not_duplicate_existing_2018_results(self):
        seed = load_seed_file(ROOT / "data/site.json")
        self.database.initialize(seed)
        payload = self.database.payload({"meta": {}})
        xuzhou = [honor for honor in payload["honors"] if honor.get("externalContestId") == "icpc2018xuzhou"]
        self.assertEqual({honor["team"] for honor in xuzhou}, {
            "扶我起来还能A (WakemeuptogetAC)",
            "梁队 (Liang Dui)",
            "顶呱呱 (Dingguagua)",
        })
        self.assertEqual(sum(honor["team"] == "顶呱呱 (Dingguagua)" for honor in xuzhou), 1)

    def test_followup_batch_is_loaded_without_replacing_older_archives(self):
        loaded = load_seed_file(ROOT / "data/site.json")
        batch_ids = {batch["batchId"] for batch in loaded["officialImports"]}
        self.assertIn("rankland-icpc2018xuzhou-dingguagua-20260916-v1", batch_ids)
        self.assertIn("rankland-supplement-20260916-v1", batch_ids)


if __name__ == "__main__":
    unittest.main()
