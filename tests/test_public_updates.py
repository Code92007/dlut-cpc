import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from database import Database
from tools.public_sync import refresh_public_data, refresh_public_data_periodically
from tools.public_updates import ingest_public_results, list_result_diffs, resolve_result_diff
from tools.rankland_sync import eligible_ranklist, fetch_rankland_updates, save_rankland_versions
from tools.sync_public_data import build_public_snapshot


class PublicUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = Database(self.root / "site.sqlite3")
        self.record = {
            "id": "old-result", "cpcfinderAwardId": 1, "cpcfinderContestId": 79, "cpcfinderTeamId": 10,
            "event": "第 51 届 ICPC 国际大学生程序设计竞赛亚洲区域赛西安站", "series": "ICPC", "date": "2026-10-18",
            "location": "西安", "team": "猫猫虫小队", "medal": "银牌", "rank": "103 / 402", "overallRank": "110 / 418",
            "official": True, "members": ["甲", "乙", "丙"],
            "memberDetails": [{"name": name, "provider": "cpcfinder", "externalId": identity} for name, identity in (("甲", "a"), ("乙", "b"), ("丙", "c"))],
            "source": {"name": "CPC Finder", "url": "https://cpcfinder.com/contest/79"},
        }
        self.seed = {"meta": {"updatedAt": "2026-10-18"}, "honors": [self.record], "training": []}
        self.seed_path = self.root / "site.json"
        self.seed_path.write_text(json.dumps(self.seed), encoding="utf-8")
        self.database.initialize(self.seed)

    def honors(self):
        return self.database.payload(self.seed)["honors"]

    def changed(self, **fields):
        return {**copy.deepcopy(self.record), **fields}

    def pending(self):
        return list_result_diffs(self.database)["items"]

    def test_new_results_are_added_once_and_existing_results_stay_unchanged(self):
        new = self.changed(id="new-result", cpcfinderAwardId=2, team="新队伍", rank="177 / 402")
        report = ingest_public_results(self.database, [self.record, new])
        self.assertEqual((report["added"], report["diffsCreated"]), (1, 0))
        self.assertEqual(len(self.honors()), 2)
        self.assertEqual(ingest_public_results(self.database, [new])["added"], 0)

    def test_changed_result_queues_one_diff_and_keep_a_does_not_repeat(self):
        changed = self.changed(rank="105 / 402", medal="铜牌")
        report = ingest_public_results(self.database, [changed])
        self.assertEqual(report["diffsCreated"], 1)
        self.assertEqual(self.honors()[0]["rank"], self.record["rank"])
        self.assertEqual(set(self.pending()[0]["fields"]), {"rank", "medal"})
        self.assertEqual(ingest_public_results(self.database, [changed])["diffsCreated"], 0)
        resolve_result_diff(self.database, self.pending()[0]["id"], "a", "admin")
        self.assertEqual(ingest_public_results(self.database, [changed])["diffsCreated"], 0)
        self.assertEqual(self.pending(), [])
        self.assertEqual(list_result_diffs(self.database, status="closed")["items"][0]["reviewer"], "admin")

    def test_accept_b_survives_seed_reimport_and_does_not_change_accounts_or_roster(self):
        changed = self.changed(rank="105 / 402")
        ingest_public_results(self.database, [changed])
        resolve_result_diff(self.database, self.pending()[0]["id"], "b", "admin")
        self.database.initialize(self.seed)
        self.assertEqual(self.honors()[0]["rank"], "105 / 402")
        self.assertEqual(self.honors()[0]["members"], self.record["members"])
        self.assertEqual(ingest_public_results(self.database, [changed])["diffsCreated"], 0)

    def test_changed_source_supersedes_previous_pending_diff(self):
        ingest_public_results(self.database, [self.changed(rank="104 / 402")])
        previous = self.pending()[0]["id"]
        ingest_public_results(self.database, [self.changed(rank="105 / 402")])
        self.assertEqual(len(self.pending()), 1)
        self.assertNotEqual(self.pending()[0]["id"], previous)
        self.assertEqual(list_result_diffs(self.database, status="closed")["items"][0]["status"], "superseded")

    def test_accepting_stale_diff_cannot_overwrite_newer_manual_change(self):
        ingest_public_results(self.database, [self.changed(rank="104 / 402")])
        diff_id = self.pending()[0]["id"]
        with self.database.connect() as connection:
            connection.execute("UPDATE honors SET rank='102 / 402' WHERE id=?", (self.record["id"],))
        with self.assertRaisesRegex(ValueError, "过期差异"):
            resolve_result_diff(self.database, diff_id, "b", "admin")
        self.assertEqual(self.honors()[0]["rank"], "102 / 402")

    def test_cross_source_match_attaches_provenance_without_duplicate_or_overwrite(self):
        rankland = self.rankland_record(rank="104 / 402")
        report = ingest_public_results(self.database, [rankland])
        self.assertEqual((report["added"], report["diffsCreated"]), (0, 1))
        self.assertEqual(len(self.honors()), 1)
        self.assertEqual(self.honors()[0]["rank"], self.record["rank"])

    def rankland_record(self, **changes):
        row = {key: value for key, value in self.record.items() if not key.startswith("cpcfinder")}
        row.update(id="rl-record", externalProvider="rankland", externalAwardId="icpc2026xi_an:Team1:大连理工大学",
                   externalContestId="icpc2026xi_an", externalTeamId="Team1", members=[], memberDetails=[],
                   suggestedMembers=self.record["members"], school="大连理工大学",
                   source={"name": "RankLand 榜单", "url": "https://rl.algoux.cn/ranklist/icpc2026xi_an"})
        return {**row, **changes}

    def test_rankland_new_result_keeps_unknown_medal_and_roster_unconfirmed(self):
        row = self.rankland_record(team="新队伍", rank="200 / 402", medal="", medalStatus="unknown", suggestedMembers=["新甲", "新乙", "新丙"])
        report = ingest_public_results(self.database, [row])
        self.assertEqual(report["added"], 1)
        added = next(item for item in self.honors() if item["team"] == "新队伍")
        self.assertEqual(added["medal"], "")
        self.assertEqual(added["members"], [])
        self.assertEqual(added["suggestedMembers"], ["新甲", "新乙", "新丙"])

    def test_official_status_change_is_reviewed_instead_of_duplicated(self):
        report = ingest_public_results(self.database, [self.rankland_record(official=False)])
        self.assertEqual((report["added"], report["diffsCreated"]), (0, 1))
        self.assertIn("official", self.pending()[0]["fields"])

    def test_starred_iron_and_student_display_alias_do_not_create_false_diffs(self):
        self.database.initialize({**self.seed, "honors": [self.changed(official=False, medal="铁牌")]})
        renamed = self.changed(official=False, medal="铁牌", members=["Jia", "乙", "丙"],
                               memberDetails=[{**self.record["memberDetails"][0], "name": "Jia"}, *self.record["memberDetails"][1:]])
        self.assertEqual(ingest_public_results(self.database, [renamed])["diffsCreated"], 0)

    def test_previously_merged_student_ids_do_not_create_false_roster_diffs_or_medal_offsets(self):
        with self.database.connect() as connection:
            member_id = connection.execute("SELECT member_id FROM member_identities WHERE external_id='c'").fetchone()[0]
            connection.execute("INSERT INTO member_identities(provider,external_id,member_id) VALUES ('cpcfinder','c-alias',?)", (member_id,))
        changed = self.changed(memberDetails=[*self.record["memberDetails"][:2], {"name": "丙", "provider": "cpcfinder", "externalId": "c-alias"}])
        members = [{"name": "丙", "provider": "cpcfinder", "externalId": "c-alias", "school": "大连理工大学",
                    "cpcfinder": {"goldCount": 0, "silverCount": 1, "bronzeCount": 0, "ironCount": 0}}]
        self.assertEqual(ingest_public_results(self.database, [changed], public_members=members)["diffsCreated"], 0)
        member = next(item for item in self.database.payload(self.seed)["members"] if item["id"] == member_id)
        self.assertEqual(member["medals"]["silver"], 1)

    def test_roster_change_requires_b_and_keeps_stable_student_identities(self):
        changed = self.changed(members=["甲", "乙", "丁"], memberDetails=[*self.record["memberDetails"][:2],
                               {"name": "丁", "provider": "cpcfinder", "externalId": "d"}])
        ingest_public_results(self.database, [changed])
        self.assertEqual(self.honors()[0]["members"], self.record["members"])
        resolve_result_diff(self.database, self.pending()[0]["id"], "b", "admin")
        self.database.initialize(self.seed)
        self.assertEqual(self.honors()[0]["members"], ["甲", "乙", "丁"])

    def test_public_medal_totals_follow_accepted_a_not_unreviewed_source_b(self):
        public_members = [{"name": name, "provider": "cpcfinder", "externalId": identity, "school": "大连理工大学",
                           "cpcfinder": {"silverCount": 0, "bronzeCount": 1, "goldCount": 0, "ironCount": 0}}
                          for name, identity in (("甲", "a"), ("乙", "b"), ("丙", "c"))]
        ingest_public_results(self.database, [self.changed(medal="铜牌")], public_members=public_members)
        member = next(item for item in self.database.payload(self.seed)["members"] if item["name"] == "甲")
        self.assertEqual((member["medals"]["silver"], member["medals"]["bronze"]), (1, 0))
        resolve_result_diff(self.database, self.pending()[0]["id"], "b", "admin")
        member = next(item for item in self.database.payload(self.seed)["members"] if item["name"] == "甲")
        self.assertEqual((member["medals"]["silver"], member["medals"]["bronze"]), (0, 1))

    def test_source_failure_does_not_block_other_source_or_remove_records(self):
        row = self.rankland_record(team="新增队", rank="200 / 402", suggestedMembers=["新甲", "新乙", "新丙"])
        with patch("tools.public_sync.build_public_snapshot", side_effect=OSError("offline")), \
                patch("tools.public_sync.fetch_rankland_updates", return_value=([row], [], {"failedContests": []})):
            result = refresh_public_data(self.database.path, self.seed_path)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["summary"]["added"], 1)
        self.assertEqual(len(self.honors()), 2)

    def test_periodic_refresh_retries_then_waits_one_day_after_success(self):
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = [False, True]
        with patch("tools.public_sync.refresh_public_data", side_effect=[OSError("offline"), {"status": "ok"}]) as refresh, patch("builtins.print"):
            refresh_public_data_periodically(stop, self.database.path, self.seed_path, 86400)
        self.assertEqual(refresh.call_count, 2)
        self.assertEqual([call.args[0] for call in stop.wait.call_args_list], [3600, 86400])

    def test_restart_uses_persisted_schedule_instead_of_running_immediately(self):
        status = {"status": "ok", "lastFinishedAt": dt.datetime.now(dt.timezone.utc).isoformat()}
        (self.root / "public_sync_status.json").write_text(json.dumps(status), encoding="utf-8")
        stop = Mock()
        stop.wait.return_value = True
        with patch("tools.public_sync.refresh_public_data") as refresh:
            refresh_public_data_periodically(stop, self.database.path, self.seed_path, 86400)
        refresh.assert_not_called()
        self.assertGreater(stop.wait.call_args.args[0], 86300)

    def test_empty_upstream_response_is_rejected_without_mutating_seed(self):
        with patch("tools.sync_public_data.fetch_text", return_value='{"data": []}'):
            with self.assertRaisesRegex(ValueError, "no results"):
                build_public_snapshot(self.seed)
        self.assertEqual(len(self.seed["honors"]), 1)

    def test_rankland_discovery_ignores_preliminary_invitational_and_world_finals(self):
        self.assertTrue(eligible_ranklist({"uniqueKey": "ccpc2026changchun", "name": "长春正式赛"}))
        self.assertTrue(eligible_ranklist({"uniqueKey": "icpc2026xi_an", "name": "西安区域赛"}))
        for key in ("icpc2026preliminary-1", "icpc2026invitational-shenyang", "icpc2026worldfinal", "../../other"):
            self.assertFalse(eligible_ranklist({"uniqueKey": key, "name": key}))

    def test_rankland_unchanged_content_reuses_observation_and_does_not_download_again(self):
        row = self.rankland_record(rank="104 / 402")
        ingest_public_results(self.database, [row])
        save_rankland_versions(self.database, [("icpc2026xi_an", "content-v1")])
        catalog = {"ranklistIndex": {"ranks": [{"uniqueKey": "icpc2026xi_an", "name": "西安区域赛", "fileID": "content-v1"}]}}
        document = '<script>window.__INITIAL_STATE__ = ' + repr(json.dumps(catalog)) + '</script>'
        with patch("tools.rankland_sync.fetch_text", return_value=document) as fetch:
            records, checked, summary = fetch_rankland_updates(self.database)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(summary["fetchedContests"], 0)
        self.assertEqual(records[0]["rank"], "104 / 402")
        self.assertEqual(checked, [("icpc2026xi_an", "content-v1")])
        self.assertEqual(ingest_public_results(self.database, records)["diffsCreated"], 0)

    def test_all_sources_failing_preserves_every_existing_result(self):
        with patch("tools.public_sync.build_public_snapshot", side_effect=OSError("cpc offline")), \
                patch("tools.public_sync.fetch_rankland_updates", side_effect=OSError("rankland offline")):
            status = refresh_public_data(self.database.path, self.seed_path)
        self.assertEqual(status["status"], "failed")
        self.assertEqual(len(self.honors()), 1)
        self.assertEqual(self.honors()[0]["rank"], self.record["rank"])

    def test_first_rankland_scan_baselines_unknown_old_catalog_without_backfilling_history(self):
        catalog = {"ranklistIndex": {"ranks": [{"uniqueKey": "icpc2016xian", "name": "西安区域赛", "fileID": "old-content-v1"}]}}
        document = '<script>window.__INITIAL_STATE__ = ' + repr(json.dumps(catalog)) + '</script>'
        with patch("tools.rankland_sync.fetch_text", return_value=document) as fetch:
            records, checked, summary = fetch_rankland_updates(self.database)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(records, [])
        self.assertEqual(checked, [("icpc2016xian", "old-content-v1")])
        self.assertEqual(summary["fetchedContests"], 0)


if __name__ == "__main__":
    unittest.main()
