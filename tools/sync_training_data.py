#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SITE_DATA = ROOT / "data" / "site.json"
DEFAULT_HDU_DATA = ROOT / "data" / "hdu_training_2023.json"
NOWCODER_API = "https://ac.nowcoder.com/acm-heavy/acm/contest/real-time-rank-data"
NOWCODER_CONTESTS = tuple(range(108298, 108308))
SCHOOL_NAME = "大连理工大学"
CHINA_TZ = dt.timezone(dt.timedelta(hours=8))


def format_minutes(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_nowcoder_rank(payload: dict, contest_id: int, round_number: int) -> dict:
    if payload.get("code") != 0 or not isinstance(payload.get("data"), dict):
        raise ValueError(f"Nowcoder contest {contest_id} returned an invalid response")

    data = payload["data"]
    basic = data.get("basicInfo") or {}
    begin_ms = int(basic.get("contestBeginTime") or 0)
    end_ms = int(basic.get("contestEndTime") or begin_ms)
    if not begin_ms:
        raise ValueError(f"Nowcoder contest {contest_id} has no start time")

    begin = dt.datetime.fromtimestamp(begin_ms / 1000, tz=CHINA_TZ)
    problems = [str(item["name"]) for item in data.get("problemData", [])]
    problem_names = {int(item["problemId"]): str(item["name"]) for item in data.get("problemData", [])}
    teams = []
    for row in data.get("rankData", []):
        if row.get("school") != SCHOOL_NAME or int(row.get("acceptedCount") or 0) <= 0:
            continue
        problem_results = {}
        for score in row.get("scoreList", []):
            name = problem_names.get(int(score.get("problemId") or -1))
            if not name or not score.get("submit"):
                continue
            failed = int(score.get("failedCount") or 0)
            accepted = bool(score.get("accepted"))
            result = {"solved": accepted, "tries": failed + 1 if accepted else max(1, failed)}
            if accepted:
                accepted_ms = int(score.get("acceptedTime") or begin_ms)
                result["time"] = format_minutes(max(0, (accepted_ms - begin_ms) // 60000))
                if score.get("firstBlood"):
                    result["first"] = True
            problem_results[name] = result
        teams.append({
            "rank": int(row.get("ranking") or 0),
            "name": str(row.get("userName") or row.get("uid") or "未知队伍"),
            "school": SCHOOL_NAME,
            "solved": int(row.get("acceptedCount") or 0),
            "penalty": int(row.get("penaltyTime") or 0) // 60000,
            "problems": problem_results,
        })

    teams.sort(key=lambda item: (item["rank"], -item["solved"], item["penalty"], item["name"]))
    return {
        "id": f"nowcoder-{contest_id}",
        "providerId": str(contest_id),
        "year": str(begin.year),
        "date": begin.date().isoformat(),
        "dateLabel": begin.strftime("%Y-%m-%d"),
        "sortKey": begin.strftime("%Y-%m-%d"),
        "title": f"{begin.year} 牛客暑期多校训练营 · Round {round_number:02d}",
        "series": "牛客多校",
        "platform": "Nowcoder",
        "duration": max(1, (end_ms - begin_ms) // 60000),
        "problems": problems,
        "teams": teams,
        "rankScope": "全榜名次",
        "resultType": "题目级最终榜",
        "rankHistoryAvailable": False,
        "source": {
            "name": "牛客竞赛公开榜单",
            "url": f"https://ac.nowcoder.com/acm/contest/{contest_id}#rank",
            "provider": "nowcoder",
        },
    }


def fetch_nowcoder_rank(contest_id: int) -> dict:
    query = urllib.parse.urlencode({
        "id": contest_id,
        "page": 1,
        "limit": 2000,
        "searchUserName": SCHOOL_NAME,
    })
    request = urllib.request.Request(
        f"{NOWCODER_API}?{query}",
        headers={
            "Referer": f"https://ac.nowcoder.com/acm/contest/{contest_id}",
            "User-Agent": "DLUTCPCDataSync/0.2 (+https://wannafly.cn)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def load_nowcoder_contests(json_dir: Path | None, workers: int) -> list[dict]:
    def load(item: tuple[int, int]) -> dict:
        round_number, contest_id = item
        if json_dir:
            payload = json.loads((json_dir / f"{contest_id}.json").read_text(encoding="utf-8"))
        else:
            payload = fetch_nowcoder_rank(contest_id)
        return parse_nowcoder_rank(payload, contest_id, round_number)

    items = list(enumerate(NOWCODER_CONTESTS, start=1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        contests = list(executor.map(load, items))
    return contests


def load_hdu_contests(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    contests = payload.get("training", payload) if isinstance(payload, dict) else payload
    if not isinstance(contests, list):
        raise ValueError("HDU training data must contain a list")
    return contests


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def merge_training(site: dict, imported: list[dict]) -> list[dict]:
    imported_providers = {item.get("source", {}).get("provider") for item in imported}
    preserved = [
        item for item in site.get("training", [])
        if not item.get("demo") and item.get("source", {}).get("provider") not in imported_providers
    ]
    return sorted(
        [*imported, *preserved],
        key=lambda item: (str(item.get("sortKey") or item.get("date") or ""), str(item.get("id") or "")),
        reverse=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync DLUT teams from public summer training standings")
    parser.add_argument("--site-data", type=Path, default=DEFAULT_SITE_DATA)
    parser.add_argument("--hdu-data", type=Path, default=DEFAULT_HDU_DATA)
    parser.add_argument("--nowcoder-json-dir", type=Path, help="read saved API responses instead of fetching")
    parser.add_argument("--skip-nowcoder", action="store_true")
    parser.add_argument("--skip-hdu", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    site = json.loads(args.site_data.read_text(encoding="utf-8"))
    imported: list[dict] = []
    if not args.skip_nowcoder:
        imported.extend(load_nowcoder_contests(args.nowcoder_json_dir, args.workers))
    if not args.skip_hdu:
        imported.extend(load_hdu_contests(args.hdu_data))
    site["training"] = merge_training(site, imported)
    site["meta"]["updatedAt"] = dt.date.today().isoformat()

    print(
        f"contests={len(site['training'])} teams={sum(len(item.get('teams', [])) for item in site['training'])} "
        f"nowcoder={sum(item.get('series') == '牛客多校' for item in site['training'])} "
        f"hdu={sum(item.get('series') == '杭电多校' for item in site['training'])}"
    )
    if not args.dry_run:
        write_json_atomic(args.site_data, site)


if __name__ == "__main__":
    main()
