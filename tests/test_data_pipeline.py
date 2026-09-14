import importlib.util
import json
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "sync_public_data.py"
SPEC = importlib.util.spec_from_file_location("sync_public_data", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class DataPipelineTests(unittest.TestCase):
    def test_parse_filters_old_and_unawarded_rows(self):
        document = """
        <table><tbody>
          <tr><td>赛事 A</td><td>Team A</td><td>金牌</td><td>2024-11-03</td><td>南京</td><td>12 / 300</td><td>15 / 320</td></tr>
          <tr><td>赛事 B</td><td>Team B</td><td></td><td>2024-11-03</td><td>南京</td><td>99 / 300</td><td>100 / 320</td></tr>
          <tr><td>赛事 C</td><td>Team C</td><td>银牌</td><td>2019-11-03</td><td>南京</td><td>50 / 300</td><td>55 / 320</td></tr>
        </tbody></table>
        """
        result = MODULE.parse_cpcfinder(document, "https://example.com")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["team"], "Team A")

    def test_deduplicate_normalizes_team_punctuation(self):
        base = {
            "event": "ICPC 武汉站",
            "date": "2025-11-02",
            "location": "武汉",
            "medal": "金牌",
            "rank": "37 / 446",
            "series": "ICPC",
            "members": [],
            "source": {"name": "CPC Finder", "url": "https://example.com/a"},
        }
        records = [
            {**base, "team": "关注带工喵！"},
            {**base, "team": "关注带工喵 !", "members": ["甲", "乙", "丙"], "source": {"name": "官方榜单", "url": "https://example.com/b"}},
        ]
        result = MODULE.deduplicate(records)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["members"], ["甲", "乙", "丙"])
        self.assertEqual(result[0]["source"]["name"], "官方榜单")

    def test_parse_api_uses_official_rank_and_filters_medals(self):
        document = json.dumps({"data": [
            {"awardId": 101, "contestId": 22, "teamId": 303, "contestName": "第 50 届 ICPC 亚洲区域赛武汉站", "teamName": "Team A", "medal": "金牌", "date": "2025-11-02", "place": "武汉", "rank": 49, "officialRank": 37, "totalTeams": 520, "totalOfficialTeams": 446},
            {"contestName": "第 50 届 ICPC 亚洲区域赛武汉站", "teamName": "Team B", "date": "2025-11-02", "place": "武汉", "rank": 100, "totalTeams": 520},
        ]}, ensure_ascii=False)
        result = MODULE.parse_cpcfinder(document, "https://cpcfinder.com/api/school/test-id/awards")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["rank"], "37 / 446")
        self.assertEqual(result[0]["overallRank"], "49 / 520")
        self.assertEqual(result[0]["source"]["url"], "https://cpcfinder.com/school/test-id")
        self.assertEqual(result[0]["cpcfinderAwardId"], 101)
        self.assertEqual(result[0]["cpcfinderContestId"], 22)

    def test_contest_member_map_matches_award_and_keeps_student_identity(self):
        document = json.dumps({"data": [
            {
                "awardId": 101,
                "members": [
                    {"studentId": "student-a", "name": "甲"},
                    {"studentId": "student-b", "name": "乙"},
                    {"studentId": "student-c", "name": "丙"},
                ],
            },
            {"awardId": 102, "members": [{"studentId": "other", "name": "其他学校成员"}]},
        ]}, ensure_ascii=False)
        result = MODULE.contest_member_map(document, {"101"})
        self.assertEqual([item["name"] for item in result["101"]], ["甲", "乙", "丙"])
        self.assertEqual(result["101"][0]["externalId"], "student-a")
        self.assertNotIn("102", result)


if __name__ == "__main__":
    unittest.main()
