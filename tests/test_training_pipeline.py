import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "sync_training_data.py"
SPEC = importlib.util.spec_from_file_location("sync_training_data", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


class TrainingPipelineTests(unittest.TestCase):
    def test_parse_nowcoder_uses_global_rank_and_problem_results(self):
        payload = {
            "code": 0,
            "data": {
                "basicInfo": {"contestBeginTime": 1752552000000, "contestEndTime": 1752570000000},
                "problemData": [{"name": "A", "problemId": 1}, {"name": "B", "problemId": 2}],
                "rankData": [{
                    "school": "大连理工大学",
                    "ranking": 172,
                    "userName": "点铁成金",
                    "acceptedCount": 1,
                    "penaltyTime": 661000,
                    "scoreList": [
                        {"problemId": 1, "submit": True, "accepted": True, "acceptedTime": 1752552600000, "failedCount": 1, "firstBlood": True},
                        {"problemId": 2, "submit": True, "accepted": False, "acceptedTime": -1, "failedCount": 3, "firstBlood": False},
                    ],
                }],
            },
        }

        contest = MODULE.parse_nowcoder_rank(payload, 108298, 1)

        self.assertEqual(contest["date"], "2025-07-15")
        self.assertEqual(contest["teams"][0]["rank"], 172)
        self.assertEqual(contest["teams"][0]["penalty"], 11)
        self.assertEqual(contest["teams"][0]["problems"]["A"], {"solved": True, "tries": 2, "time": "00:10", "first": True})
        self.assertEqual(contest["teams"][0]["problems"]["B"], {"solved": False, "tries": 3})

    def test_parse_nowcoder_excludes_zero_solve_and_other_schools(self):
        payload = {
            "code": 0,
            "data": {
                "basicInfo": {"contestBeginTime": 1752552000000, "contestEndTime": 1752570000000},
                "problemData": [],
                "rankData": [
                    {"school": "大连理工大学", "acceptedCount": 0, "ranking": 999, "userName": "未上榜"},
                    {"school": "其他学校", "acceptedCount": 3, "ranking": 10, "userName": "其他队"},
                ],
            },
        }

        contest = MODULE.parse_nowcoder_rank(payload, 108298, 1)

        self.assertEqual(contest["teams"], [])

    def test_merge_removes_demo_and_preserves_manual_training(self):
        site = {"training": [
            {"id": "demo", "demo": True},
            {"id": "manual", "source": {"provider": "manual"}, "date": "2024-01-01"},
            {"id": "old-nowcoder", "source": {"provider": "nowcoder"}, "date": "2024-01-01"},
        ]}
        imported = [{"id": "new-nowcoder", "source": {"provider": "nowcoder"}, "date": "2025-01-01"}]

        result = MODULE.merge_training(site, imported)

        self.assertEqual([item["id"] for item in result], ["new-nowcoder", "manual"])


if __name__ == "__main__":
    unittest.main()
