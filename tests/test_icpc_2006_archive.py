import json
import tempfile
import unittest
from pathlib import Path

from database import Database, load_seed_file
from official_imports import contest_key


ROOT = Path(__file__).resolve().parents[1]


class ICPC2006ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "site.sqlite3")
        self.database.initialize()
        self.batch = json.loads(
            (ROOT / "data/icpc_2006_honors.json").read_text(encoding="utf-8")
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_archive_adds_the_xian_honorable_mention_as_iron(self):
        report = self.database.merge_official_batch(self.batch)
        self.assertEqual((report["added"], report["merged"], report["conflict"]), (1, 0, 0))

        payload = self.database.payload({"meta": {}})
        self.assertEqual(len(payload["honors"]), 1)
        honor = payload["honors"][0]
        self.assertEqual((honor["date"], honor["location"], honor["medal"]),
                         ("2006-12-17", "西安", "铁牌"))
        self.assertEqual(honor["suggestedMembers"], ["刘炯", "张岳", "张剑峰"])
        self.assertEqual(honor["source"], {
            "name": "大连理工大学官方报道（2006 西安站）",
            "url": "https://chuangxin.dlut.edu.cn/info/1017/2246.htm",
        })
        self.assertEqual(
            {source["url"] for source in honor["sources"]},
            {
                "https://icpc.global/regionals/finder/Xian-2007",
                "https://chuangxin.dlut.edu.cn/info/1017/2246.htm",
                "https://chuangxin.dlut.edu.cn/info/1017/1924.htm",
            },
        )
        archived = self.batch["honors"][0]["archive"]
        self.assertEqual(archived["rawSchoolAward"], "优胜奖")
        self.assertEqual(archived["officialContestId"], 1284)

    def test_roster_stays_pending_while_unknown_scoreboard_fields_stay_empty(self):
        self.database.merge_official_batch(self.batch)
        payload = self.database.payload({"meta": {}})
        self.assertEqual(len(payload["pendingHonors"]), 1)
        honor = payload["pendingHonors"][0]
        self.assertEqual(honor["members"], [])
        self.assertEqual((honor["rank"], honor["overallRank"]), ("", ""))
        self.assertIn("原队名待考", honor["team"])

    def test_seed_loader_includes_the_2006_recovery_batch(self):
        seed = load_seed_file(ROOT / "data/site.json")
        batch_ids = {batch["batchId"] for batch in seed["officialImports"]}
        self.assertIn(self.batch["batchId"], batch_ids)

    def test_xian_is_a_stable_contest_match_key(self):
        record = self.batch["honors"][0]
        self.assertEqual(contest_key(record), ("ICPC", "2006", "西安"))


if __name__ == "__main__":
    unittest.main()
