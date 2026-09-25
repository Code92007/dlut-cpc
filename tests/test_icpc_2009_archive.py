import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from database import Database, load_seed_file
from official_imports import contest_key


ROOT = Path(__file__).resolve().parents[1]


class ICPC2009ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "site.sqlite3")
        self.database.initialize({"meta": {}, "honors": [], "training": []})
        self.batch = json.loads((ROOT / "data/icpc_2009_honors.json").read_text(encoding="utf-8"))

    def tearDown(self):
        self.temporary.cleanup()

    def test_archive_adds_seven_results_from_the_2009_season(self):
        report = self.database.merge_official_batch(self.batch)
        self.assertEqual((report["added"], report["merged"], report["conflict"]), (7, 0, 0))

        payload = self.database.payload({"meta": {}})
        self.assertEqual(len(payload["honors"]), 7)
        self.assertEqual(len(payload["pendingHonors"]), 7)
        self.assertEqual(Counter(item["medal"] for item in payload["honors"]), {
            "银牌": 4,
            "铁牌": 2,
            "铜牌": 1,
        })
        self.assertEqual({item["date"][:4] for item in payload["honors"]}, {"2009"})
        self.assertEqual({item["location"] for item in payload["honors"]}, {
            "哈尔滨", "合肥", "宁波", "上海", "武汉",
        })

    def test_wuhan_enriches_rankland_result_without_creating_a_duplicate(self):
        rankland = json.loads(
            (ROOT / "data/rankland_supplement_honors.json").read_text(encoding="utf-8")
        )
        self.database.merge_official_batch(rankland)
        report = self.database.merge_official_batch(self.batch)
        self.assertEqual((report["added"], report["merged"], report["conflict"]), (6, 1, 0))

        payload = self.database.payload({"meta": {}})
        wuhan = [item for item in payload["honors"] if item["date"] == "2009-11-01" and item["team"] == "Bombee"]
        self.assertEqual(len(wuhan), 1)
        self.assertEqual((wuhan[0]["location"], wuhan[0]["medal"], wuhan[0]["rank"], wuhan[0]["overallRank"]),
                         ("武汉", "银牌", "27 / 124", "33 / 132"))
        self.assertEqual(wuhan[0]["suggestedMembers"], ["黄宏韬", "周晨扬", "雷思宇"])
        self.assertFalse(wuhan[0]["rosterConfirmed"])
        self.assertIn("RankLand 2009 武汉站历史榜单", {source["name"] for source in wuhan[0]["sources"]})
        self.assertIn("大连理工大学 2009 ACM-ICPC 亚洲区域赛获奖记录",
                      {source["name"] for source in wuhan[0]["sources"]})

    def test_rosters_preserve_source_spelling_and_resolved_team_names(self):
        records = {record["id"]: record for record in self.batch["honors"]}
        honorable = records["icpc-official-2009-harbin-chen-feng"]
        self.assertEqual(honorable["suggestedMembers"], ["陈沣", "杨凯", "张萌"])
        self.assertEqual(honorable["archive"]["rawSchoolAward"], "荣誉奖")
        self.assertEqual(honorable["medal"], "铁牌")

        hefei = records["icpc-official-2009-hefei-huang-hongtao"]
        self.assertEqual(hefei["team"], "Bombee")
        self.assertEqual(hefei["rank"], "")
        self.assertEqual(hefei["archive"]["rawTeam"], "Bombee")
        self.assertEqual(hefei["source"]["url"],
                         "https://icpc.global/regionals/finder/Hefei-2010/standings")

    def test_v2_corrects_hefei_team_in_an_existing_v1_database(self):
        old_batch = copy.deepcopy(self.batch)
        old_batch["batchId"] = "icpc-official-dlut-2009-recovery-20260922-v1"
        hefei = next(record for record in old_batch["honors"] if record["location"] == "合肥")
        hefei["team"] = "大连理工大学（黄宏韬队，原队名待考）"
        hefei["externalTeamId"] = "Dalian University of Technology:roster-huang-hongtao-zhou-chenyang-lei-siyu"
        hefei.pop("authoritativeCorrections")
        hefei["archive"]["rawTeam"] = None

        self.database.merge_official_batch(old_batch)
        report = self.database.merge_official_batch(self.batch)
        self.assertEqual((report["added"], report["merged"], report["conflict"]), (0, 7, 0))
        correction = next(item for item in report["records"] if item["date"] == "2009-10-11")
        self.assertEqual(correction["correctedFields"], ["team"])

        payload = self.database.payload({"meta": {}})
        hefei_result = next(item for item in payload["honors"] if item["location"] == "合肥")
        self.assertEqual(hefei_result["team"], "Bombee")
        self.assertEqual(hefei_result["externalTeamId"], "Dalian University of Technology:Bombee")
        self.assertEqual(hefei_result["source"]["url"],
                         "https://icpc.global/regionals/finder/Hefei-2010/standings")

    def test_seed_loader_and_ningbo_contest_key_include_the_archive(self):
        seed = load_seed_file(ROOT / "data/site.json")
        batch_ids = {batch["batchId"] for batch in seed["officialImports"]}
        self.assertIn(self.batch["batchId"], batch_ids)

        ningbo = next(record for record in self.batch["honors"] if record["location"] == "宁波")
        self.assertEqual(contest_key(ningbo), ("ICPC", "2009", "宁波"))


if __name__ == "__main__":
    unittest.main()
