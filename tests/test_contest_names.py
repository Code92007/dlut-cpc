import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contest_names import canonical_contest_name, honor_contest_name
from cpc_integration import Integration
from database import Database, load_seed_file
from official_imports import contest_key, match_result
from test_database import seed_data
from tools.sync_public_data import parse_cpcfinder_api, record_key


class ContestNameTests(unittest.TestCase):
    def test_screenshot_names_use_full_chinese_format(self):
        examples = [
            ("The 45th ICPC Asia Yinchuan Regional Programming Contest (Hosted by Ningxia Institute of Science and Technology, also known as IUPC 2021)",
             "第 45 届 ICPC 国际大学生程序设计竞赛亚洲区域赛银川站"),
            ("The 44th ICPC International Collegiate Programming Contest Asian Regional Contest (Nanchang) （江西师范大学）",
             "第 44 届 ICPC 国际大学生程序设计竞赛亚洲区域赛南昌站"),
            ("The 44th ICPC Asia Yinchuan Regional Programming Contest",
             "第 44 届 ICPC 国际大学生程序设计竞赛亚洲区域赛银川站"),
            ("2019 CCPC 总决赛", "第 5 届 CCPC 中国大学生程序设计竞赛总决赛"),
            ("2018 CCPC 秦皇岛站", "第 4 届 CCPC 中国大学生程序设计竞赛秦皇岛站"),
            ("第 11 届中国大学生程序设计竞赛总决赛", "第 11 届 CCPC 中国大学生程序设计竞赛总决赛"),
            ("第 8 届 CCPC 中国大学生程序设计竞赛桂林", "第 8 届 CCPC 中国大学生程序设计竞赛桂林站"),
        ]
        for old, new in examples:
            with self.subTest(event=old):
                self.assertEqual(canonical_contest_name(old), new)
                self.assertEqual(canonical_contest_name(new), new)

    def test_season_ids_handle_postponements_and_host_only_titles(self):
        examples = [
            ("ICPC xju onsite", "icpc2017urumchi", "第 42 届 ICPC 国际大学生程序设计竞赛亚洲区域赛乌鲁木齐站"),
            ("The 2020 ICPC Asia-East Continent Final Contest", "icpc2020ecfinal", "第 45 届 ICPC 国际大学生程序设计竞赛东亚区决赛"),
            ("2018-2019 ACM-ICPC, Asia East Continent Finals", "icpc2018ecfinal", "第 43 届 ICPC 国际大学生程序设计竞赛东亚区决赛"),
            ("The 2016 ACM-ICPC Asia China-Final (Shanghai) Contest （上海大学）", "icpc2016cnfinal", "第 41 届 ICPC 国际大学生程序设计竞赛中国区决赛"),
            ("2024 CCPC 总决赛", "ccpc2024:总决赛", "第 10 届 CCPC 中国大学生程序设计竞赛总决赛"),
        ]
        for old, identity, new in examples:
            with self.subTest(identity=identity):
                self.assertEqual(canonical_contest_name(old, contest_id=identity), new)

    def test_other_types_and_ambiguous_names_are_preserved(self):
        for event in ("ICPC 测试站", "2020 ICPC", "2018 ICPC 北京网络预选赛", "2020 CCPC 秦皇岛邀请赛",
                      "2020 ICPC World Finals", "2020 CCPC 女生赛", "2020 ICPC 北京上海站"):
            with self.subTest(event=event):
                self.assertEqual(canonical_contest_name(event), event)
        self.assertEqual(canonical_contest_name("第 44 届 ICPC 银川站", contest_id="icpc2020yinchuan"),
                         "第 44 届 ICPC 银川站")

    def test_old_archive_matches_canonical_postponed_contest(self):
        old = {"event": "The 2020 ICPC Asia-East Continent Final Contest", "series": "ICPC",
               "externalContestId": "icpc2020ecfinal", "date": "2021-04-18", "location": "总决赛",
               "team": "fixture", "school": "大连理工大学", "medal": "银牌"}
        new = {**old, "id": "existing", "event": honor_contest_name(old), "externalContestId": "public-uuid"}
        self.assertEqual(contest_key(old), ("ICPC", "2020", "总决赛"))
        self.assertEqual(contest_key(old), contest_key(new))
        self.assertEqual(match_result(old, [new])[:2], ("merged", new))

    def test_public_sync_keeps_original_result_id_when_normalizing_title(self):
        row = {"date": "2026-04-26", "medal": "铜牌", "contestName": "第 11 届中国大学生程序设计竞赛总决赛",
               "teamName": "fixture", "awardId": "fixture-award"}
        result = parse_cpcfinder_api(json.dumps([row]), "https://cpcfinder.com/api/school/fixture/awards")[0]
        self.assertEqual(result["event"], "第 11 届 CCPC 中国大学生程序设计竞赛总决赛")
        self.assertEqual(result["id"], record_key({**result, "event": row["contestName"]}))

    def test_archived_contests_all_have_canonical_names(self):
        seed = load_seed_file(Path(__file__).resolve().parents[1] / "data/site.json")
        batches = seed["historicalImports"] + seed["officialImports"]
        for record in seed["honors"] + [row for batch in batches for row in batch["honors"]]:
            with self.subTest(event=record["event"]):
                name = honor_contest_name(record)
                self.assertRegex(name, r"^第 \d+ 届 (ICPC 国际|CCPC 中国)大学生程序设计竞赛")
                self.assertEqual(canonical_contest_name(name), name)

    def test_database_migration_preserves_ids_rosters_sources_and_resync(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = copy.deepcopy(seed_data())
            old = "2019 CCPC 总决赛"
            new = "第 5 届 CCPC 中国大学生程序设计竞赛总决赛"
            (root / "contest_ranklists.json").write_text(json.dumps({"contests": {old: "https://rl.algoux.cn/ranklist/ccpc2019final"}}))
            environment = patch.dict(os.environ, {"SITE_DATA_PATH": str(root / "site.json")})
            environment.start()
            self.addCleanup(environment.stop)
            seed["honors"][0].update(event=old, series="CCPC", date="2019-11-17")
            database = Database(root / "site.sqlite3")
            database.initialize(seed)
            service = Integration(database)
            with database.connect() as connection:
                connection.execute("UPDATE honors SET event=?", (old,))
            before = service.roster()
            with database.connect() as connection:
                table_names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('honors','sqlite_sequence')")]
                before_tables = {table: list(map(tuple, connection.execute(f'SELECT * FROM "{table}"'))) for table in table_names}
            database.initialize()
            after = service.roster()
            expected = copy.deepcopy(before)
            expected["participations"][0]["event"] = new
            self.assertEqual(after, expected)
            with database.connect() as connection:
                for table, rows in before_tables.items():
                    self.assertEqual(list(map(tuple, connection.execute(f'SELECT * FROM "{table}"'))), rows, table)
                updated = connection.execute("SELECT updated_at FROM honors").fetchone()[0]
            database.initialize()
            with database.connect() as connection:
                self.assertEqual(connection.execute("SELECT updated_at FROM honors").fetchone()[0], updated)
            database.initialize(seed)
            self.assertEqual(service.roster(), after)
            # OJ Wall receives the canonical name and the existing standings URL.
            with patch.dict(os.environ, {"SITE_DATA_PATH": str(root / "site.json")}):
                row = service.roster()["participations"][0]
            self.assertEqual(row["event"], new)
            self.assertEqual(row["ranklist_url"], "https://rl.algoux.cn/ranklist/ccpc2019final")
            # Old spelling cannot bypass the existing public-result duplicate guard.
            with self.assertRaisesRegex(ValueError, "公开参赛成绩已存在"):
                database.add_manual_honor_with_members(seed["honors"][0], [])


if __name__ == "__main__":
    unittest.main()
