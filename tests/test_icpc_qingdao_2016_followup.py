import json
import tempfile
import unittest
from pathlib import Path

from database import Database, load_seed_file


ROOT = Path(__file__).resolve().parents[1]


class Qingdao2016FollowupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "site.sqlite3")
        self.database.initialize()
        self.rankland = json.loads((ROOT / "data/rankland_supplement_honors.json").read_text(encoding="utf-8"))
        self.followup = json.loads((ROOT / "data/icpc_qingdao_2016_honors.json").read_text(encoding="utf-8"))

    def tearDown(self):
        self.temporary.cleanup()

    def test_archive_has_three_honorable_mentions_without_guessed_members(self):
        records = self.followup["honors"]
        self.assertEqual({record["team"] for record in records},
                         {"winter is coming", "Innovator", "lingering_sound"})
        self.assertTrue(all(record["medal"] == "铁牌" for record in records))
        self.assertTrue(all(record["members"] == record["suggestedMembers"] == [] for record in records))
        self.assertEqual(sum(record["originalSchool"] == "Dalian University of Technology" for record in records), 1)
        self.assertEqual(sum(record["originalSchool"].startswith("School of Software Technology") for record in records), 2)
        self.assertTrue(all(record["archive"]["officialResultCategory"] == "Honorable Mention" for record in records))
        self.assertTrue(all(record["archive"]["problemsSolved"] == 0 for record in records))

    def test_followup_merges_rankland_rows_and_fills_only_the_award(self):
        self.database.merge_official_batch(self.rankland)
        before = {
            honor["team"]: honor
            for honor in self.database.payload({"meta": {}})["honors"]
            if honor.get("externalContestId") == "icpc2016qingdao"
        }
        self.assertEqual(len(before), 3)
        self.assertTrue(all(not honor["medal"] for honor in before.values()))

        report = self.database.merge_official_batch(self.followup)
        self.assertEqual((report["added"], report["merged"], report["conflict"]), (0, 3, 0))
        after = {
            honor["team"]: honor
            for honor in self.database.payload({"meta": {}})["honors"]
            if honor.get("externalContestId") == "icpc2016qingdao"
        }
        self.assertEqual(set(after), set(before))
        self.assertTrue(all(honor["medal"] == "铁牌" and honor["members"] == [] for honor in after.values()))
        self.assertEqual({team: honor["id"] for team, honor in after.items()},
                         {team: honor["id"] for team, honor in before.items()})
        self.assertTrue(all(len(honor["sources"]) == 2 for honor in after.values()))

    def test_seed_loader_includes_the_followup_batch(self):
        seed = load_seed_file(ROOT / "data/site.json")
        batch_ids = {batch["batchId"] for batch in seed["officialImports"]}
        self.assertIn(self.followup["batchId"], batch_ids)


if __name__ == "__main__":
    unittest.main()
