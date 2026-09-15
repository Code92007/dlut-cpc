#!/usr/bin/env python3
"""Build an offline, one-time historical snapshot from RankLand's public SRK pages."""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from html.parser import HTMLParser
from http.client import HTTPException
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from schools import SCHOOL_GROUPS, school_group  # noqa: E402
ORIGIN = "https://rl.algoux.cn"
BATCH_ID = "rankland-pre2020-v1"
# CCPC calls its regional contests "Site" rather than "Regional".
CCPC_REGIONAL_KEYS = {
    "ccpc2015nanyang", "ccpc2016hangzhou", "ccpc2016hefei", "ccpc2016changchun",
    "ccpc2017qinhuangdao", "ccpc2017hangzhou", "ccpc2017harbin",
    "ccpc2018guilin", "ccpc2018jilin", "ccpc2018qinhuangdao",
    "ccpc2019qinhuangdao", "ccpc2019haerbin", "ccpc2019xiamen",
}

class StateParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.in_script = False
        self.parts: list[str] = []
        self.scripts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "script":
            self.in_script = True
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.in_script:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self.scripts.append("".join(self.parts))
            self.in_script = False


def parse_initial_state(document: str) -> dict:
    parser = StateParser()
    parser.feed(document)
    for script in parser.scripts:
        match = re.fullmatch(r"\s*window\.__INITIAL_STATE__\s*=\s*(.*?)\s*;?\s*", script, re.S)
        if not match:
            continue
        # Parse only a string literal; never execute code supplied by a webpage.
        encoded = ast.literal_eval(match.group(1))
        if not isinstance(encoded, str):
            raise ValueError("RankLand initial state is not a string literal")
        state = json.loads(encoded)
        if not isinstance(state, dict) or state.get("loadFailed"):
            raise ValueError("RankLand page did not load a ranklist")
        return state
    raise ValueError("RankLand page has no structured initial state")


def cached_page(url: str, path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    request = Request(url, headers={"User-Agent": "DLUT-CPC historical archive/1.0", "Accept": "text/html"})
    with urlopen(request, timeout=25) as response:
        document = response.read().decode("utf-8")
    parse_initial_state(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return document


def excluded_event(key: str, title: str) -> bool:
    text = f"{key} {title}".casefold()
    return bool(re.search(r"preliminary|province|provincial|invitational|邀请|省赛|省级|网络|预选|女子|女队|girls|women", text))


def parse_ranklist(document: str, *, convert=None) -> tuple[list[dict], dict]:
    if convert is None:
        from standard_ranklist_utils import convert_to_static_ranklist
        convert = convert_to_static_ranklist
    data = parse_initial_state(document)["ranklistData"]
    info, srk = data["info"], data["srk"]
    key = info["uniqueKey"]
    titles = srk["contest"].get("title", info["name"])
    title = titles.get("zh-CN") or titles.get("fallback") if isinstance(titles, dict) else titles
    all_titles = " ".join(str(value) for value in titles.values()) if isinstance(titles, dict) else str(titles)
    date = dt.datetime.fromisoformat(srk["contest"]["startAt"].replace("Z", "+00:00")).date().isoformat()
    audit = {"key": key, "title": title, "date": date, "url": f"{ORIGIN}/ranklist/{key}"}
    if date >= "2020-01-01" or excluded_event(key, all_titles):
        return [], {**audit, "excluded": "date-or-event-type"}
    if key not in CCPC_REGIONAL_KEYS and not re.search(r"regional|区域|final|总决赛", all_titles, re.I):
        return [], {**audit, "excluded": "unverified-event-type"}
    medal_series = [i for i, series in enumerate(srk.get("series", []))
                    if any(segment.get("style") in {"gold", "silver", "bronze"} for segment in series.get("segments", []))]
    if len(medal_series) != 1:
        return [], {**audit, "excluded": "missing-or-ambiguous-medal-configuration"}
    medal_index = medal_series[0]
    count = srk["series"][medal_index].get("rule", {}).get("options", {}).get("count", {}).get("value")
    if count == [0, 0, 0]:
        return [], {**audit, "excluded": "missing-medal-boundaries"}
    static = srk if srk.get("type") == "static" else convert(srk)
    official_total = sum(row["user"].get("official", True) is not False for row in static["rows"])
    result = []
    matching_schools = set()
    for position, row in enumerate(static["rows"], 1):
        user = row["user"]
        original_school = user.get("organization", "")
        if isinstance(original_school, dict):
            original_school = original_school.get("zh-CN") or original_school.get("fallback") or ""
        group = school_group(original_school)
        if not group:
            continue
        matching_schools.add(original_school)
        value = row["rankValues"][medal_index]
        segment = value.get("segmentIndex")
        if user.get("official", True) is False or segment is None:
            continue
        style = srk["series"][medal_index]["segments"][segment].get("style")
        medal = {"gold": "金牌", "silver": "银牌", "bronze": "铜牌"}.get(style)
        if not medal or value.get("rank") is None:
            continue
        team = user.get("name") or str(user["id"])
        if isinstance(team, dict):
            team = team.get("zh-CN") or team.get("fallback")
        identity = f"{key}:{user['id']}:{group}"
        result.append({
            "id": "rankland-" + hashlib.sha256(identity.encode()).hexdigest()[:20],
            "event": title, "series": "CCPC" if key.startswith("ccpc") else "ICPC",
            "date": date, "location": "总决赛" if re.search(r"final|总决赛", all_titles, re.I) else "",
            "team": team, "school": group, "originalSchool": original_school,
            "members": [], "expectedMembers": 3, "medal": medal,
            "rank": f"{value['rank']} / {official_total}", "overallRank": f"{position} / {len(static['rows'])}",
            "official": True, "source": {"name": "RankLand 历史榜单", "url": audit["url"]},
            "externalProvider": "rankland", "externalAwardId": identity,
            "externalContestId": key, "externalTeamId": str(user["id"]),
            "archive": {"fileId": info.get("fileID"), "score": row.get("score"),
                        "medalSeries": srk["series"][medal_index], "rankValue": value,
                        "pageSha256": hashlib.sha256(document.encode()).hexdigest(),
                        "rawLinks": srk["contest"].get("refLinks", [])},
            "suggestedMembers": [member["name"] for member in user.get("teamMembers", [])
                                 if member.get("role", "contestant") not in {"coach", "reserve"} and member.get("name")],
        })
    return result, {**audit, "awards": len(result), "schools": sorted(matching_schools)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "runtime/source_cache/rankland")
    parser.add_argument("--catalog-file", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "data/historical_honors.json")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if args.output.exists():
        sys.exit("Snapshot already exists; no external requests were made. Keep the existing one-time archive.")
    try:
        from standard_ranklist_utils import convert_to_static_ranklist
    except ImportError:
        sys.exit("Install optional import dependencies: pip install -r tools/requirements-rankland.txt")
    catalog = args.catalog_file.read_text(encoding="utf-8") if args.catalog_file else cached_page(f"{ORIGIN}/search", args.cache_dir / "search.html")
    entries = parse_initial_state(catalog)["ranklistIndex"]["ranks"]
    candidates = [entry for entry in entries if re.match(r"^(?:icpc|ccpc)(?:19|20)\d{2}", entry["uniqueKey"])
                  and int(re.search(r"\d{4}", entry["uniqueKey"])[0]) < 2020
                  and not excluded_event(entry["uniqueKey"], entry["name"])]
    honors, audit, errors = [], [], []
    def fetch(entry: dict) -> tuple[list[dict], dict]:
        key = entry["uniqueKey"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", key):
            raise ValueError("Invalid contest key")
        page = cached_page(f"{ORIGIN}/ranklist/{key}", args.cache_dir / f"{key}.html")
        return parse_ranklist(page, convert=convert_to_static_ranklist)
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 4))) as pool:
        futures = {pool.submit(fetch, entry): entry for entry in candidates}
        for future in as_completed(futures):
            entry = futures[future]
            try:
                records, report = future.result()
                honors.extend(records)
                audit.append(report)
                print(f"{report['key']}: awards={len(records)} {report.get('excluded', '')}", flush=True)
            except (OSError, HTTPException, ValueError, KeyError, TypeError) as exc:
                errors.append({"key": entry["uniqueKey"], "error": str(exc)})
                print(f"{entry['uniqueKey']}: ERROR {exc}", flush=True)
    if errors:
        sys.exit(f"Incomplete import ({len(errors)} failures); cached pages retained. No snapshot written. Errors: {errors}")
    snapshot = {"batchId": BATCH_ID, "fetchedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "source": f"{ORIGIN}/search", "beforeDate": "2020-01-01", "schoolAliases": SCHOOL_GROUPS,
                "catalogSha256": hashlib.sha256(catalog.encode()).hexdigest(),
                "contests": sorted(audit, key=lambda x: (x["date"], x["key"])),
                "honors": sorted(honors, key=lambda x: (x["date"], x["event"], x["school"], x["team"]))}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"archived={len(honors)} contests={len(audit)} output={args.output}")


if __name__ == "__main__":
    main()
