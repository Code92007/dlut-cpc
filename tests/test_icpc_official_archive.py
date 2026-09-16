import json
import tempfile
import unittest
from pathlib import Path

from database import Database, load_seed_file


ROOT = Path(__file__).resolve().parents[1]


class ICPCOfficialArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "site.sqlite3")
        self.seed = {"meta": {}, "honors": [], "training": []}
        self.database.initialize(self.seed)
        self.batch = json.loads((ROOT / "data/icpc_official_honors.json").read_text(encoding="utf-8"))

    def tearDown(self):
        self.temporary.cleanup()

    def test_beijing_archive_adds_both_dut_teams_as_pending_results(self):
        report = self.database.merge_official_batch(self.batch)
        self.assertEqual((report["added"], report["merged"], report["conflict"]), (2, 0, 0))
        payload = self.database.payload(self.seed)
        self.assertEqual({item["team"] for item in payload["honors"]}, {"Rainbow", "Thesedays"})
        self.assertEqual({item["medal"] for item in payload["honors"]}, {"金牌", "铁牌"})
        pending = {item["team"]: item for item in payload["pendingHonors"]}
        self.assertEqual(pending["Thesedays"]["suggestedMembers"], ["黄宇凡", "张寿奎", "金航宇"])
        self.assertEqual(pending["Rainbow"]["suggestedMembers"], ["Yiming Deng", "Yicheng Liu", "xuting li"])

    def test_later_public_copy_merges_with_icpc_archive(self):
        self.database.merge_official_batch(self.batch)
        public = {
            **self.batch["honors"][0],
            "id": "later-public-copy",
            "externalProvider": "cpcfinder",
            "externalAwardId": "later-public-award",
            "members": ["黄宇凡", "张寿奎", "金航宇"],
            "source": {"name": "CPC Finder", "url": "https://cpcfinder.com/source"},
        }
        self.database.initialize({**self.seed, "honors": [public]})
        self.assertEqual(len(self.database.payload(self.seed)["honors"]), 2)
        with self.database.connect() as connection:
            honor_id = connection.execute(
                "SELECT honor_id FROM honor_source_records WHERE provider='cpcfinder' AND external_id='later-public-award'"
            ).fetchone()[0]
            self.assertEqual(honor_id, "icpc-official-2018-beijing-thesedays")

    def test_seed_loader_includes_independent_icpc_archive(self):
        loaded = load_seed_file(ROOT / "data/site.json")
        batch_ids = {batch["batchId"] for batch in loaded["officialImports"]}
        self.assertIn("icpc-official-beijing-2018-20260916-v1", batch_ids)


if __name__ == "__main__":
    unittest.main()
