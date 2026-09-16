import json
import tempfile
import unittest
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from database import Database, load_seed_file
from official_imports import contest_key, match_result
from tools.import_ccpc import RankHTML, columns, excluded_event, rows_to_results, season_region
from tools.merge_ccpc import main as merge_cli


def record(**changes):
    return {"id": "official-1", "externalProvider": "ccpc-official", "externalAwardId": "2017:杭州:team1",
            "event": "2017 CCPC 杭州站", "series": "CCPC", "date": "2017-11-05", "location": "杭州",
            "team": "旧队伍", "teamAliases": ["old team"], "medal": "铜牌", "rank": "56",
            "school": "大连理工大学", "originalSchool": "大连理工大学软件学院", "expectedMembers": 3,
            "suggestedMembers": ["甲", "乙", "丙"],
            "source": {"name": "CCPC 官方获奖名单", "url": "https://ccpc.io/post/97"},
            "archive": {"rawAward": "铜奖", "rawCells": ["56", "铜奖", "旧队伍"]}, **changes}


def batch(*records, batch_id="ccpc-test-v1"):
    return {"batchId": batch_id, "honors": list(records or [record()])}


class OfficialMatchingTests(unittest.TestCase):
    def test_alias_and_formatted_rank_merge_without_false_warning(self):
        existing = record(id="existing", team="old team", rank="56 / 200")
        status, target, warnings = match_result(record(), [existing])
        self.assertEqual((status, target["id"]), ("merged", "existing"))
        self.assertEqual(warnings, ["team-disagreement"])

    def test_same_team_in_other_year_is_a_new_result(self):
        existing = record(event="2018 CCPC 杭州站", date="2018-11-05")
        self.assertEqual(match_result(record(), [existing])[0], "added")

    def test_unknown_region_accepts_nullable_contest_identity(self):
        self.assertIsNone(contest_key({"event": "2018 ICPC Regional", "location": "", "date": "2018-10-01",
                                       "series": "ICPC", "externalContestId": None}))

    def test_next_year_final_matches_ccpc_edition_not_calendar_year(self):
        new = record(event="2024 CCPC 总决赛", date="2025-05-11", location="总决赛")
        old = record(event="第 10 届 CCPC 中国大学生程序设计竞赛总决赛", date="2025-05-10", location="总决赛")
        self.assertEqual(contest_key(new), contest_key(old))
        self.assertEqual(match_result(new, [old])[0], "merged")

    def test_same_day_ccpc_official_and_cpcfinder_match_despite_title_and_series(self):
        official = record(event="2024 CCPC 总决赛", date="2025-05-11", location="总决赛", team="逆元", rank="93")
        public = record(id="public", externalProvider="cpcfinder", externalAwardId="22896",
                        event="第 10 届中国大学生程序设计竞赛总决赛", series="ICPC",
                        date="2025-05-11", location="广州", team="逆元", rank="93 / 124")
        self.assertEqual(match_result(official, [public])[:2], ("merged", public))

    def test_independent_campuses_do_not_merge(self):
        for school in ("大连理工大学城市学院", "大连理工大学盘锦校区"):
            self.assertEqual(match_result(record(), [record(school=school)])[0], "added")
        self.assertEqual(match_result(record(), [record(school="大连理工大学开发区校区")])[0], "merged")

    def test_only_complete_same_contest_roster_resolves_team_name(self):
        old = record(team="报名英文名", teamAliases=[], members=["丙", "甲", "乙"])
        self.assertEqual(match_result(record(), [old])[0], "merged")
        self.assertEqual(match_result(record(rank="57"), [{**old, "members": ["甲", "乙", "丁"]}])[0], "added")

    def test_same_rank_different_team_and_ambiguous_matches_are_conflicts(self):
        old = record(id="old", team="别的队", teamAliases=[], members=[])
        self.assertEqual(match_result(record(), [old])[0], "conflict")
        self.assertEqual(match_result(record(), [record(id="one"), record(id="two")])[0], "conflict")

    def test_medal_disagreement_is_never_overwritten(self):
        self.assertEqual(match_result(record(), [record(medal="银牌")])[0], "conflict")

    def test_roster_disagreement_is_reported_not_assigned(self):
        old = record(members=["甲", "丁", "戊"])
        status, target, warnings = match_result(record(), [old])
        self.assertEqual(status, "merged")
        self.assertIn("roster-disagreement-existing-members-preserved", warnings)
        self.assertEqual(target["members"], ["甲", "丁", "戊"])

    def test_header_does_not_confuse_team_rank_and_medal(self):
        layout = columns(["队伍排名", "学校排名", "获奖团队名", "学校", "成员一", "奖项"])
        self.assertEqual((layout["rank"], layout["team"], layout["school"], layout["medal"]), (0, 2, 3, 5))

    def test_only_explicit_awards_not_blank_or_ranking_qualify(self):
        rows = [["排名", "奖项", "学校", "队名"], ["1", "金奖", "大连理工大学", "金队"],
                ["200", "优胜奖", "大连理工大学", "铁队"], ["250", "", "大连理工大学", "未知"],
                ["20", "银奖", "大连海事大学", "他校"]]
        found, issues = rows_to_results(rows, 1)
        self.assertEqual([r["medal"] for r in found], ["金牌", "铁牌"])
        self.assertEqual(len(issues), 1)

    def test_pdf_merged_award_cells_are_explicitly_opted_in(self):
        rows = [["排名", "奖项", "学校", "队名"], ["1", "铜奖", "某高校", "一"],
                ["2", None, "大连理工大学", "二"]]
        self.assertEqual(rows_to_results(rows, 1)[0], [])
        self.assertEqual(rows_to_results(rows, 1, merged_medals=True)[0][0]["medal"], "铜牌")

    def test_incomplete_html_does_not_emit_partial_rows(self):
        html = RankHTML()
        html.feed("<table><tr><td>完整</td></tr><tr><td>大连理工大学")
        self.assertEqual(len(html.rows), 1)

    def test_event_scope_excludes_special_events_but_not_womens_teams(self):
        for title in ("CCPC 网络选拔赛", "CCPC 女生赛", "CCPC 高职专场", "CCPC 邀请赛", "CCPC 省赛"):
            self.assertTrue(excluded_event(title))
        self.assertFalse(excluded_event("2019 CCPC 厦门站"))
        self.assertEqual(season_region("第十届 CCPC 总决赛"), (2024, "总决赛"))


class OfficialDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = Database(self.root / "test.sqlite3")
        self.seed = {"meta": {}, "honors": [], "training": []}
        self.database.initialize(self.seed)

    def tearDown(self):
        self.temporary.cleanup()

    def test_new_result_is_pending_and_retains_archived_evidence(self):
        report = self.database.merge_official_batch(batch())
        self.assertEqual((report["added"], report["merged"], report["conflict"]), (1, 0, 0))
        payload = self.database.payload(self.seed)
        self.assertEqual(payload["members"], [])
        self.assertEqual(payload["pendingHonors"][0]["suggestedMembers"], ["甲", "乙", "丙"])
        with self.database.connect() as connection:
            self.assertEqual(json.loads(connection.execute("SELECT record_json FROM honor_source_records").fetchone()[0])["archive"]["rawAward"], "铜奖")

    def test_dry_run_leaves_results_sources_and_batch_marker_unchanged(self):
        self.assertEqual(self.database.merge_official_batch(batch(), dry_run=True)["added"], 1)
        with self.database.connect() as connection:
            for table in ("honors", "honor_sources", "honor_source_records", "honor_roster_reviews"):
                self.assertEqual(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
            self.assertIsNone(connection.execute("SELECT 1 FROM metadata WHERE key LIKE 'official_import:%'").fetchone())

    def test_manual_result_and_corrected_roster_survive_union_and_restart(self):
        honor_id = self.database.add_manual_honor({**record(), "id": "manual-one", "externalProvider": None,
                                                   "externalAwardId": None, "rank": "56 / 200", "source": {"name": "队内补录", "url": ""}})
        for name in ("甲", "丁", "戊"):
            self.database.link_member(honor_id, self.database.add_manual_member(name))
        self.database.initialize(self.seed)
        report = self.database.merge_official_batch(batch())
        self.assertEqual((report["added"], report["merged"]), (0, 1))
        self.assertIn("roster-disagreement-existing-members-preserved", report["records"][0]["warnings"])
        with patch("urllib.request.urlopen", side_effect=OSError("source offline")):
            self.database.initialize({**self.seed, "officialImports": [batch()]})
        result = self.database.payload(self.seed)["honors"][0]
        self.assertEqual((result["members"], result["rank"], result["manual"]), (["甲", "丁", "戊"], "56 / 200", True))
        self.assertEqual({s["name"] for s in result["sources"]}, {"队内补录", "CCPC 官方获奖名单"})

    def test_public_source_refresh_preserves_official_archived_source(self):
        public = record(id="public-one", externalProvider="cpcfinder", externalAwardId="one",
                        source={"name": "CPC Finder", "url": "https://cpcfinder.com/source"}, members=["甲", "乙", "丙"])
        self.seed["honors"] = [public]
        self.database.initialize(self.seed)
        self.assertEqual(self.database.merge_official_batch(batch())["merged"], 1)
        self.database.initialize(self.seed)
        result = self.database.payload(self.seed)["honors"][0]
        self.assertEqual(len(result["sources"]), 2)
        self.assertEqual(result["members"], ["甲", "乙", "丙"])

    def test_medal_conflict_and_occupied_rank_do_not_add_or_change_results(self):
        self.seed["honors"] = [record(id="existing", medal="银牌")]
        self.database.initialize(self.seed)
        self.assertEqual(self.database.merge_official_batch(batch())["conflict"], 1)
        self.assertEqual(self.database.payload(self.seed)["honors"][0]["medal"], "银牌")
        with self.database.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM honor_source_records").fetchone()[0], 0)

    def test_once_only_import_remains_once_after_confirmation(self):
        self.database.merge_official_batch(batch())
        self.database.confirm_honor_members("official-1", ["甲", "乙", "丙"])
        self.database.initialize({**self.seed, "officialImports": [batch()]})
        self.assertTrue(self.database.merge_official_batch(batch())["alreadyImported"])
        self.assertEqual(self.database.payload(self.seed)["pendingHonors"], [])
        self.assertEqual(len(self.database.payload(self.seed)["honors"]), 1)

    def test_same_result_from_two_batches_merges_not_duplicates(self):
        self.database.merge_official_batch(batch())
        self.assertEqual(self.database.merge_official_batch(batch(batch_id="ccpc-test-v2"))["merged"], 1)
        self.assertEqual(len(self.database.payload(self.seed)["honors"]), 1)

    def test_initialize_backfills_same_day_ccpc_public_duplicate(self):
        public = record(id="public", externalProvider="cpcfinder", externalAwardId="22896",
                        event="第 10 届中国大学生程序设计竞赛总决赛", series="ICPC",
                        date="2025-05-11", location="广州", team="逆元", rank="93 / 124",
                        members=["甲", "乙", "丙"], source={"name": "CPC Finder", "url": "https://cpcfinder.com/source"})
        self.seed["honors"] = [public]
        self.database.initialize(self.seed)
        self.database.merge_official_batch(batch(record(event="2024 CCPC 总决赛", date="2025-05-11",
                                                        location="总决赛", team="逆元", rank="93")))
        self.assertEqual(self.database.payload(self.seed)["honors"][0]["series"], "CCPC")
        with self.database.connect() as connection:
            connection.execute("UPDATE honors SET series='ICPC' WHERE id='public'")
            connection.execute("INSERT INTO honors SELECT 'official-1',event,'CCPC',date,'总决赛',team,normalized_team,medal,'93','',official,"
                               "'ccpc-official','2017:杭州:team1','ccpc2024:总决赛',NULL,primary_source_id,0,created_at,updated_at "
                               "FROM honors WHERE id='public'")
            connection.execute("UPDATE honor_source_records SET honor_id='official-1' WHERE provider='ccpc-official'")
            connection.execute("DELETE FROM honor_sources WHERE honor_id='public' AND source_id IN "
                               "(SELECT id FROM sources WHERE name='CCPC 官方获奖名单')")
            source_id = connection.execute("SELECT id FROM sources WHERE name='CCPC 官方获奖名单'").fetchone()[0]
            connection.execute("INSERT INTO honor_sources(honor_id,source_id,role) VALUES ('official-1',?,'archive')", (source_id,))
            connection.execute("INSERT INTO honor_roster_reviews(honor_id,batch_id,school,suggested_members_json) "
                               "VALUES ('official-1','legacy','大连理工大学','[\"甲\",\"乙\",\"丙\"]')")
        self.database.initialize()
        results = self.database.payload(self.seed)["honors"]
        self.assertEqual(len(results), 1)
        self.assertEqual((results[0]["id"], results[0]["series"], results[0]["members"]),
                         ("public", "CCPC", ["甲", "乙", "丙"]))
        self.assertEqual({source["name"] for source in results[0]["sources"]},
                         {"CPC Finder", "CCPC 官方获奖名单"})

    def test_duplicates_within_batch_merge_with_the_just_added_result(self):
        report = self.database.merge_official_batch(batch(record(), record(id="official-2", externalAwardId="2017:杭州:team2")), dry_run=True)
        self.assertEqual((report["added"], report["merged"]), (1, 1))
        report = self.database.merge_official_batch(batch(record(), record(id="official-2", externalAwardId="2017:杭州:team2")))
        self.assertEqual((report["added"], report["merged"]), (1, 1))
        self.assertEqual(len(self.database.payload(self.seed)["honors"]), 1)

    def test_invalid_record_rolls_back_the_whole_batch(self):
        for bad in (record(id="two", externalAwardId="two", medal="未知"),
                    record(id="two", externalAwardId="two", event="2017 CCPC 女生赛"),
                    record(id="two", externalAwardId="two", date="invalid"),
                    record(id="two", externalAwardId="two", school="大连海事大学")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.database.merge_official_batch(batch(record(), bad))
            self.assertEqual(self.database.payload(self.seed)["honors"], [])

    def test_unrelated_id_collision_rolls_back_preceding_result(self):
        self.seed["honors"] = [record(id="official-2", externalAwardId="other-year", event="2018 CCPC 杭州站", date="2018-11-05")]
        self.database.initialize(self.seed)
        with self.assertRaisesRegex(ValueError, "collides"):
            self.database.merge_official_batch(batch(record(), record(id="official-2", externalAwardId="two", team="另一队", teamAliases=[], rank="70")))
        self.assertEqual(len(self.database.payload(self.seed)["honors"]), 1)

    def test_unrelated_external_identity_collision_is_rejected(self):
        self.seed["honors"] = [record(id="old", event="2018 CCPC 杭州站", date="2018-11-05")]
        self.database.initialize(self.seed)
        with self.assertRaisesRegex(ValueError, "source identity collides"):
            self.database.merge_official_batch(batch())
        self.assertEqual(len(self.database.payload(self.seed)["honors"]), 1)

    def test_confirmed_official_results_augment_not_replace_cpcfinder_totals(self):
        self.seed["publicMembers"] = [{"name": "甲", "provider": "cpcfinder", "externalId": "student-one",
                                       "cpcfinder": {"goldCount": 2, "silverCount": 3, "bronzeCount": 4, "ironCount": 5}}]
        self.database.initialize(self.seed)
        self.database.merge_official_batch(batch(record(medal="铁牌")))
        self.database.confirm_honor_members("official-1", ["甲", "乙", "丙"])
        member = next(m for m in self.database.payload(self.seed)["members"] if m["name"] == "甲")
        self.assertEqual(member["medals"], {"gold": 2, "silver": 3, "bronze": 4, "iron": 6})

    def test_concurrent_import_is_idempotent(self):
        with ThreadPoolExecutor(max_workers=2) as executor:
            reports = list(executor.map(lambda _: self.database.merge_official_batch(batch()), range(2)))
        self.assertEqual(sum(bool(r.get("alreadyImported")) for r in reports), 1)
        self.assertEqual(len(self.database.payload(self.seed)["honors"]), 1)

    def test_archive_loader_and_payload_are_offline_and_do_not_expose_batch(self):
        path = self.root / "site.json"
        path.write_text(json.dumps(self.seed), encoding="utf-8")
        (self.root / "ccpc_official_honors.json").write_text(json.dumps(batch()), encoding="utf-8")
        loaded = load_seed_file(path)
        with patch("urllib.request.urlopen", side_effect=AssertionError("no network")):
            self.database.initialize(loaded)
            self.assertNotIn("officialImports", self.database.payload(loaded))

    def test_later_public_source_reuses_archived_result_and_preserves_confirmed_roster(self):
        self.database.merge_official_batch(batch())
        self.database.confirm_honor_members("official-1", ["甲", "乙", "丙"])
        self.seed["honors"] = [record(id="public-new", cpcfinderAwardId=101, externalProvider=None,
                                      members=["甲", "丁", "戊"], source={"name": "CPC Finder", "url": "https://cpcfinder.com/source"})]
        self.database.initialize(self.seed)
        self.database.initialize(self.seed)
        results = self.database.payload(self.seed)["honors"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["members"], ["甲", "乙", "丙"])
        self.assertEqual({s["name"] for s in results[0]["sources"]}, {"CCPC 官方获奖名单", "CPC Finder"})

    def test_later_public_source_does_not_auto_confirm_pending_roster(self):
        self.database.merge_official_batch(batch())
        self.seed["honors"] = [record(id="public-new", cpcfinderAwardId=101, externalProvider=None, members=["甲", "乙", "丙"])]
        self.database.initialize(self.seed)
        self.database.initialize(self.seed)
        payload = self.database.payload(self.seed)
        self.assertEqual(len(payload["honors"]), 1)
        self.assertEqual(len(payload["pendingHonors"]), 1)
        self.assertEqual(payload["honors"][0]["members"], [])

    def test_later_conflicting_public_source_is_not_added_as_a_duplicate(self):
        self.database.merge_official_batch(batch())
        self.seed["honors"] = [record(id="public-new", externalProvider="cpcfinder", externalAwardId="new", medal="银牌")]
        with self.assertRaisesRegex(ValueError, "conflicts"):
            self.database.initialize(self.seed)
        self.assertEqual(len(self.database.payload(self.seed)["honors"]), 1)
        self.assertEqual(self.database.payload(self.seed)["honors"][0]["medal"], "铜牌")

    def test_cli_backup_is_before_import_and_never_overwrites(self):
        snapshot = self.root / "snapshot.json"
        snapshot.write_text(json.dumps(batch()), encoding="utf-8")
        backup = self.root / "backup.sqlite3"
        argv = ["merge_ccpc.py", "--database", str(self.database.path), "--snapshot", str(snapshot), "--backup", str(backup)]
        with patch.object(sys, "argv", argv), patch("builtins.print"):
            merge_cli()
        self.assertEqual(Database(backup).payload(self.seed)["honors"], [])
        self.assertEqual(len(self.database.payload(self.seed)["honors"]), 1)
        with patch.object(sys, "argv", argv), self.assertRaises(FileExistsError):
            merge_cli()


if __name__ == "__main__":
    unittest.main()
