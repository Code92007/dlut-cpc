#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import html
import json
import re
import sys
import tempfile
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import Database  # noqa: E402


DEFAULT_SITE_DATA = ROOT / "data" / "site.json"
DEFAULT_DATABASE = ROOT / "runtime" / "dlut_cpc.sqlite3"
DEFAULT_SOURCE_URL = "https://cpcfinder.com/api/school/9c417252-c487-4eae-8822-fcd1e74b9329/awards"
MEDAL_POINTS = {"金牌": 10, "银牌": 6, "铜牌": 3}


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._row is not None and self._cell is not None:
            self._row.append(normalize_space("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def normalize_team(value: str) -> str:
    value = normalize_space(value).lower()
    value = value.translate(str.maketrans({"！": "!", "，": ",", "（": "(", "）": ")", "～": "~"}))
    return re.sub(r"[\s~!,.，。()（）\-—_]+", "", value)


def record_key(record: dict) -> str:
    raw = "|".join((record.get("date", ""), record.get("event", ""), normalize_team(record.get("team", ""))))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:14]


def public_source_url(source_url: str) -> str:
    match = re.fullmatch(r"(https?://[^/]+)/api/school/([^/]+)/awards/?", source_url)
    return f"{match.group(1)}/school/{match.group(2)}" if match else source_url


def parse_cpcfinder_api(document: str, source_url: str, min_year: int = 2020) -> list[dict]:
    payload = json.loads(document)
    rows = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("CPC Finder API response does not contain an award list")

    honors = []
    for row in rows:
        date = str(row.get("date", ""))[:10]
        medal = row.get("medal")
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", date):
            continue
        if int(date[:4]) < min_year or medal not in MEDAL_POINTS:
            continue
        official_rank = row.get("officialRank") or row.get("rank")
        official_total = row.get("totalOfficialTeams") or row.get("totalTeams")
        overall_rank = row.get("rank")
        overall_total = row.get("totalTeams")
        event = normalize_space(str(row.get("contestName", "")))
        record = {
            "event": event,
            "series": "CCPC" if "CCPC" in event else "ICPC",
            "date": date,
            "location": normalize_space(str(row.get("place", ""))),
            "team": normalize_space(str(row.get("teamName", ""))),
            "members": [],
            "medal": medal,
            "rank": f"{official_rank} / {official_total}" if official_rank and official_total else str(official_rank or ""),
            "overallRank": f"{overall_rank} / {overall_total}" if overall_rank and overall_total else str(overall_rank or ""),
            "source": {"name": "CPC Finder", "url": public_source_url(source_url)},
            "sources": [{"name": "CPC Finder", "url": public_source_url(source_url)}],
            "cpcfinderAwardId": row.get("awardId"),
            "cpcfinderContestId": row.get("contestId"),
            "cpcfinderTeamId": row.get("teamId"),
        }
        record["id"] = record_key(record)
        honors.append(record)
    return deduplicate(honors)


def cpcfinder_origin(source_url: str) -> str:
    match = re.match(r"(https?://[^/]+)", source_url)
    if not match:
        raise ValueError(f"invalid CPC Finder URL: {source_url}")
    return match.group(1)


def contest_member_map(document: str, relevant_awards: set[str]) -> dict[str, list[dict]]:
    payload = json.loads(document)
    rows = payload.get("data", payload) if isinstance(payload, dict) else payload
    result: dict[str, list[dict]] = {}
    for row in rows if isinstance(rows, list) else []:
        award_id = str(row.get("awardId") or "")
        if award_id not in relevant_awards:
            continue
        members = []
        for member in row.get("members") or []:
            if not member.get("name"):
                continue
            members.append(
                {
                    "name": normalize_space(str(member["name"])),
                    "provider": "cpcfinder",
                    "externalId": str(member.get("studentId") or ""),
                }
            )
        if members:
            result[award_id] = members
    return result


def enrich_cpcfinder_members(records: list[dict], source_url: str, workers: int = 6) -> tuple[int, list[int]]:
    origin = cpcfinder_origin(source_url)
    awards_by_contest: dict[int, set[str]] = {}
    for record in records:
        contest_id = record.get("cpcfinderContestId")
        award_id = record.get("cpcfinderAwardId")
        if contest_id is not None and award_id is not None:
            awards_by_contest.setdefault(int(contest_id), set()).add(str(award_id))

    failed: list[int] = []

    def fetch(contest_id: int) -> tuple[int, dict[str, list[dict]]]:
        url = f"{origin}/api/contest/{contest_id}/awards"
        return contest_id, contest_member_map(fetch_text(url), awards_by_contest[contest_id])

    member_map: dict[str, list[dict]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetch, contest_id): contest_id for contest_id in awards_by_contest}
        for future in concurrent.futures.as_completed(futures):
            contest_id = futures[future]
            try:
                _, rows = future.result()
                member_map.update(rows)
            except Exception as exc:  # Keep partial sync results if one public endpoint is unavailable.
                failed.append(contest_id)
                print(f"warning: contest {contest_id} roster fetch failed: {exc}", file=sys.stderr)

    enriched = 0
    for record in records:
        details = member_map.get(str(record.get("cpcfinderAwardId") or ""))
        if not details:
            continue
        record["memberDetails"] = details
        record["members"] = [item["name"] for item in details]
        contest_id = record.get("cpcfinderContestId")
        record["memberSource"] = {
            "name": "CPC Finder 赛事榜单",
            "url": f"{origin}/contest/{contest_id}",
        }
        enriched += 1
    return enriched, sorted(failed)


def parse_cpcfinder(document: str, source_url: str, min_year: int = 2020) -> list[dict]:
    if document.lstrip().startswith(("{", "[")):
        return parse_cpcfinder_api(document, source_url, min_year)
    parser = TableParser()
    parser.feed(document)
    honors = []
    for row in parser.rows:
        if len(row) != 7:
            continue
        event, team, medal, date, location, rank, overall_rank = row
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", date):
            continue
        if int(date[:4]) < min_year or medal not in MEDAL_POINTS:
            continue
        record = {
            "event": event,
            "series": "CCPC" if "CCPC" in event else "ICPC",
            "date": date,
            "location": location,
            "team": team,
            "members": [],
            "medal": medal,
            "rank": rank,
            "overallRank": overall_rank,
            "source": {"name": "CPC Finder", "url": public_source_url(source_url)},
            "sources": [{"name": "CPC Finder", "url": public_source_url(source_url)}],
        }
        record["id"] = record_key(record)
        honors.append(record)
    return deduplicate(honors)


def deduplicate(records: Iterable[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for incoming in records:
        key = record_key(incoming)
        current = merged.get(key)
        if not current:
            merged[key] = {**incoming, "id": incoming.get("id") or key}
            continue
        if incoming.get("members") and not current.get("members"):
            current["members"] = incoming["members"]
            if incoming.get("memberDetails"):
                current["memberDetails"] = incoming["memberDetails"]
            if incoming.get("memberSource"):
                current["memberSource"] = incoming["memberSource"]
        if incoming.get("rank") and current.get("rank") in {None, "", "*", "—"}:
            current["rank"] = incoming["rank"]
        for field in ("cpcfinderAwardId", "cpcfinderContestId", "cpcfinderTeamId"):
            if incoming.get(field) is not None and current.get(field) is None:
                current[field] = incoming[field]
        current["sources"] = merge_sources(current, incoming)
        if incoming.get("source", {}).get("name") != "CPC Finder":
            current["source"] = incoming["source"]
    return sorted(merged.values(), key=lambda item: (item["date"], item["event"], item["team"]), reverse=True)


def merge_sources(*records: dict) -> list[dict]:
    unique: dict[tuple[str, str], dict] = {}
    for record in records:
        sources = [item for item in record.get("sources", []) if isinstance(item, dict)]
        if isinstance(record.get("source"), dict):
            sources.append(record["source"])
        for source in sources:
            key = (str(source.get("name") or "未知来源"), str(source.get("url") or ""))
            unique[key] = source
    return list(unique.values())


def load_supplements(paths: list[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        items = payload if isinstance(payload, list) else payload.get("honors", [])
        for item in items:
            if int(str(item.get("date", "0000"))[:4] or 0) >= 2020:
                rows.append(item)
    return rows


def merge_with_existing(fetched: list[dict], existing: list[dict], supplements: list[dict]) -> list[dict]:
    enrichments: dict[str, dict] = {record_key(item): item for item in [*existing, *supplements]}
    merged = []
    for record in fetched:
        enrichment = enrichments.get(record_key(record))
        if enrichment:
            if enrichment.get("members") and enrichment.get("source", {}).get("name") != "CPC Finder":
                record["members"] = enrichment["members"]
                details_by_name = {item["name"]: item for item in record.get("memberDetails", [])}
                record["memberDetails"] = [details_by_name.get(name, {"name": name}) for name in enrichment["members"]]
                if enrichment.get("memberSource"):
                    record["memberSource"] = enrichment["memberSource"]
            for field in ("coach", "source"):
                if enrichment.get(field):
                    record[field] = enrichment[field]
            record["sources"] = merge_sources(record, enrichment)
        merged.append(record)
    fetched_keys = {record_key(item) for item in fetched}
    merged.extend(item for item in supplements if record_key(item) not in fetched_keys)
    return deduplicate(merged)


def medal_summary(honors: list[dict]) -> list[dict]:
    years: dict[str, dict] = {}
    fields = {"金牌": "gold", "银牌": "silver", "铜牌": "bronze"}
    for record in honors:
        year = record["date"][:4]
        item = years.setdefault(year, {"year": year, "gold": 0, "silver": 0, "bronze": 0})
        item[fields[record["medal"]]] += 1
    return [years[key] for key in sorted(years)]


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DLUTCPCDataSync/0.1 (+https://wannafly.cn)"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync and deduplicate DLUT public contest results")
    parser.add_argument("--source-url", default=DEFAULT_SOURCE_URL)
    parser.add_argument("--site-data", type=Path, default=DEFAULT_SITE_DATA)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--supplement", type=Path, action="append", default=[], help="JSON exported from XCPCIO/Gym/QOJ or an official list")
    parser.add_argument("--html-file", type=Path, help="parse a saved CPC Finder HTML or JSON response instead of fetching")
    parser.add_argument("--skip-members", action="store_true", help="skip per-contest roster enrichment")
    parser.add_argument("--workers", type=int, default=6, help="concurrent CPC Finder contest requests")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    site = json.loads(args.site_data.read_text(encoding="utf-8"))
    document = args.html_file.read_text(encoding="utf-8") if args.html_file else fetch_text(args.source_url)
    fetched = parse_cpcfinder(document, args.source_url)
    enriched = 0
    failed: list[int] = []
    if not args.html_file and not args.skip_members:
        enriched, failed = enrich_cpcfinder_members(fetched, args.source_url, args.workers)
    supplements = load_supplements(args.supplement)
    honors = merge_with_existing(fetched, site.get("honors", []), supplements)
    site["honors"] = honors
    site["medalSummary"] = medal_summary(honors)
    site["meta"]["updatedAt"] = dt.date.today().isoformat()
    site.pop("ratingGroups", None)

    missing = sum(not item.get("members") for item in honors)
    print(
        f"records={len(honors)} rosters={len(honors) - missing} missing={missing} "
        f"enriched={enriched} supplements={len(supplements)} failed_contests={len(failed)}"
    )
    if not args.dry_run:
        write_json_atomic(args.site_data, site)
        Database(args.database).initialize(site)


if __name__ == "__main__":
    main()
