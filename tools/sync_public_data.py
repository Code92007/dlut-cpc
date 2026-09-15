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
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import Database  # noqa: E402
from schools import SCHOOL_ALIASES, school_group  # noqa: E402


DEFAULT_SITE_DATA = ROOT / "data" / "site.json"
DEFAULT_DATABASE = ROOT / "runtime" / "dlut_cpc.sqlite3"
DEFAULT_SOURCE_URL = "https://cpcfinder.com/api/school/9c417252-c487-4eae-8822-fcd1e74b9329/awards"
DEFAULT_SCHOOL_NAME = "大连理工大学"
MEDAL_POINTS = {"金牌": 10, "银牌": 6, "铜牌": 3, "铁牌": 0}


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


def record_identity(record: dict) -> str:
    award_id = record.get("cpcfinderAwardId")
    return f"cpcfinder:{award_id}" if award_id is not None else record_key(record)


def public_source_url(source_url: str) -> str:
    match = re.fullmatch(r"(https?://[^/]+)/api/(school|student|contest)/([^/]+)/awards/?", source_url)
    return f"{match.group(1)}/{match.group(2)}/{match.group(3)}" if match else source_url


def result_medal(value: str | None, rank: object, medal_type: str | None = None) -> str | None:
    medal = normalize_space(str(value or ""))
    if medal in MEDAL_POINTS:
        return medal
    if not medal and medal_type in {None, "NONE"} and re.match(r"^[1-9]\d*(?:\s*/|$)", str(rank or "")):
        return "铁牌"
    return None


def student_list_api_url(source_url: str, school_name: str = DEFAULT_SCHOOL_NAME) -> str:
    query = urllib.parse.urlencode({"school": school_name, "sort": "rating", "current": 1, "pageSize": 500})
    return f"{cpcfinder_origin(source_url)}/api/student?{query}"


def parse_cpcfinder_students(
    document: str,
    source_url: str = DEFAULT_SOURCE_URL,
    school_name: str = DEFAULT_SCHOOL_NAME,
) -> list[dict]:
    payload = json.loads(document)
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    result = []
    for row in rows if isinstance(rows, list) else []:
        student_id = str(row.get("studentId") or "")
        name = normalize_space(str(row.get("name") or ""))
        school = normalize_space(str(row.get("schoolName") or ""))
        accepted_schools = SCHOOL_ALIASES if school_name == DEFAULT_SCHOOL_NAME else {school_name}
        if not student_id or not name or school not in accepted_schools:
            continue
        result.append({
            "name": name,
            "school": school_group(school),
            "provider": "cpcfinder",
            "externalId": student_id,
            "status": "auto",
            "cpcfinder": {
                "rating": row.get("rating"),
                "rank": row.get("rank"),
                "championCount": int(row.get("championCount") or 0),
                "secondCount": int(row.get("secCount") or 0),
                "thirdCount": int(row.get("thiCount") or 0),
                "goldCount": int(row.get("goldCount") or 0),
                "silverCount": int(row.get("silverCount") or 0),
                "bronzeCount": int(row.get("bronzeCount") or 0),
                "latestEventDate": str(row.get("latestEventDate") or ""),
            },
            "source": {
                "name": "CPC Finder 选手库",
                "url": f"{cpcfinder_origin(source_url)}/student/{student_id}",
            },
        })
    return sorted(result, key=lambda item: (item["cpcfinder"].get("rank") or 10**9, item["name"]))


def parse_cpcfinder_api(document: str, source_url: str, min_year: int = 2020) -> list[dict]:
    payload = json.loads(document)
    rows = payload.get("data", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("CPC Finder API response does not contain an award list")

    honors = []
    for row in rows:
        date = str(row.get("date", ""))[:10]
        medal = result_medal(row.get("medal"), row.get("rank") or row.get("officialRank"), row.get("medalType"))
        if not re.fullmatch(r"20\d{2}-\d{2}-\d{2}", date):
            continue
        if int(date[:4]) < min_year or medal not in MEDAL_POINTS:
            continue
        official_rank = row.get("officialRank") or row.get("rank")
        official_total = row.get("totalOfficialTeams") or row.get("totalTeams")
        if row.get("official") is False:
            official_rank = row.get("rank")
            official_total = row.get("totalTeams")
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
            "official": row.get("official"),
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


def contest_member_map(
    document: str,
    relevant_awards: set[str],
    school_names: set[str] | None = None,
) -> dict[str, list[dict]]:
    payload = json.loads(document)
    rows = payload.get("data", payload) if isinstance(payload, dict) else payload
    result: dict[str, list[dict]] = {}
    for row in rows if isinstance(rows, list) else []:
        award_id = str(row.get("awardId") or "")
        if award_id not in relevant_awards:
            continue
        if school_names is not None and normalize_space(str(row.get("schoolName") or "")) not in school_names:
            continue
        members = []
        for member in row.get("members") or []:
            if not member.get("name"):
                continue
            members.append(
                {
                    "name": normalize_space(str(member["name"])),
                    **({"school": school_group(row["schoolName"])} if school_group(row.get("schoolName", "")) else {}),
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
        return contest_id, contest_member_map(fetch_text(url), awards_by_contest[contest_id], SCHOOL_ALIASES)

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
        record["schoolVerified"] = True
        enriched += 1
    return enriched, sorted(failed)


def fetch_cpcfinder_student_results(
    members: list[dict], source_url: str, workers: int = 6, cache_dir: Path | None = None,
) -> tuple[list[dict], dict[str, set[str]], list[str]]:
    origin = cpcfinder_origin(source_url)

    def fetch(member: dict) -> tuple[str, list[dict]]:
        student_id = member["externalId"]
        url = f"{origin}/api/student/{student_id}/awards"
        cached = cache_dir / f"{student_id}.json" if cache_dir else None
        if cached and cached.exists():
            document = cached.read_text(encoding="utf-8")
        else:
            document = fetch_text(url)
            if cached:
                write_json_atomic(cached, json.loads(document))
        return student_id, parse_cpcfinder_api(document, url)

    records: list[dict] = []
    iron_awards: dict[str, set[str]] = {}
    failed: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(fetch, member): member["externalId"] for member in members}
        for future in concurrent.futures.as_completed(futures):
            student_id = futures[future]
            try:
                _, rows = future.result()
                iron_awards[student_id] = {str(row["cpcfinderAwardId"]) for row in rows if row["medal"] == "铁牌" and row.get("official") is not False}
                for row in rows:
                    row["requiresSchoolVerification"] = True
                records.extend(rows)
            except Exception as exc:
                failed.append(student_id)
                print(f"warning: student {student_id} results fetch failed: {exc}", file=sys.stderr)
    return deduplicate(records), iron_awards, failed


def apply_iron_counts(members: list[dict], records: list[dict], iron_awards: dict[str, set[str]], failed_contests: list[int]) -> None:
    verified = {str(row.get("cpcfinderAwardId")) for row in records if row.get("schoolVerified")}
    uncertain = {
        str(row.get("cpcfinderAwardId")) for row in records
        if row.get("cpcfinderContestId") in failed_contests and not row.get("schoolVerified")
    }
    for member in members:
        awards = iron_awards.get(member["externalId"])
        if awards is not None and not awards.intersection(uncertain):
            member.setdefault("cpcfinder", {})["ironCount"] = len(awards.intersection(verified))
            member["cpcfinder"]["ironExcludesUnofficial"] = True


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
        medal = result_medal(medal, overall_rank or rank)
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
    used_ids: set[str] = set()
    for incoming in records:
        key = record_identity(incoming)
        current = merged.get(key)
        if not current:
            row_id = incoming.get("id") or record_key(incoming)
            if row_id in used_ids:
                row_id = f"{row_id}-{incoming.get('cpcfinderAwardId', key)}"
            used_ids.add(row_id)
            merged[key] = {**incoming, "id": row_id}
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
        if incoming.get("source") and incoming["source"].get("name") != "CPC Finder":
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
    enrichments = {record_identity(item): item for item in [*existing, *supplements]}
    supplemental_by_key = {record_key(item): item for item in supplements if item.get("cpcfinderAwardId") is None}
    supplement_keys = {record_identity(item) for item in supplements}
    merged = []
    for record in fetched:
        enrichment = enrichments.get(record_identity(record)) or supplemental_by_key.get(record_key(record))
        if enrichment:
            # CPC Finder rosters are bound to a stable awardId. A higher-priority
            # result mirror must not replace that roster merely because it is the
            # preferred source for rank data. Only an explicit manual correction
            # may override an exact public roster.
            manual_roster = bool(enrichment.get("manual") or enrichment.get("memberRosterManual"))
            verified_supplement = record_identity(enrichment) in supplement_keys
            if enrichment.get("members") and (manual_roster or not record.get("members")):
                record["members"] = enrichment["members"]
                details_by_name = {item["name"]: item for item in [*enrichment.get("memberDetails", []), *record.get("memberDetails", [])]}
                record["memberDetails"] = [details_by_name.get(name, {"name": name}) for name in enrichment["members"]]
                if enrichment.get("memberSource"):
                    record["memberSource"] = enrichment["memberSource"]
            for field in ("coach", "source"):
                if enrichment.get(field) and (manual_roster or verified_supplement):
                    record[field] = enrichment[field]
            if manual_roster or verified_supplement:
                record["sources"] = merge_sources(record, enrichment)
        record["sources"] = merge_sources(record)
        merged.append(record)
    fetched_keys = {record_identity(item) for item in fetched}
    fetched_team_keys = {record_key(item) for item in fetched}
    merged.extend(item for item in supplements if record_identity(item) not in fetched_keys and record_key(item) not in fetched_team_keys)
    return deduplicate(merged)


def medal_summary(honors: list[dict]) -> list[dict]:
    years: dict[str, dict] = {}
    fields = {"金牌": "gold", "银牌": "silver", "铜牌": "bronze", "铁牌": "iron"}
    for record in honors:
        year = record["date"][:4]
        item = years.setdefault(year, {"year": year, "gold": 0, "silver": 0, "bronze": 0, "iron": 0})
        if record["medal"] in fields:
            item[fields[record["medal"]]] += 1
    return [years[key] for key in sorted(years)]


def fetch_text(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "DLUTCPCDataSync/0.1 (+https://wannafly.cn)"},
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


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
    parser.add_argument("--skip-students", action="store_true", help="skip the CPC Finder school-wide student directory")
    parser.add_argument("--students-file", type=Path, help="use a saved CPC Finder student API response")
    parser.add_argument("--student-results-cache", type=Path, help="optional cache directory for saved per-student result responses")
    parser.add_argument("--workers", type=int, default=6, help="concurrent CPC Finder contest requests")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    site = json.loads(args.site_data.read_text(encoding="utf-8"))
    document = args.html_file.read_text(encoding="utf-8") if args.html_file else fetch_text(args.source_url)
    fetched = parse_cpcfinder(document, args.source_url)
    for record in fetched:
        record["schoolVerified"] = True
    public_members = site.get("publicMembers", [])
    old_member_stats = {member["externalId"]: member.get("cpcfinder", {}) for member in public_members}
    if not args.skip_students and (not args.html_file or args.students_file):
        try:
            student_document = (
                args.students_file.read_text(encoding="utf-8")
                if args.students_file
                else fetch_text(student_list_api_url(args.source_url))
            )
            public_members = parse_cpcfinder_students(student_document, args.source_url)
            for member in public_members:
                previous = old_member_stats.get(member["externalId"], {})
                if "ironCount" in previous:
                    member["cpcfinder"]["ironCount"] = previous["ironCount"]
                    member["cpcfinder"]["ironExcludesUnofficial"] = previous.get("ironExcludesUnofficial", False)
        except Exception as exc:
            print(f"warning: student directory fetch failed, preserving existing data: {exc}", file=sys.stderr)
    iron_awards: dict[str, set[str]] = {}
    failed_students: list[str] = []
    if not args.html_file and not args.skip_students and not args.skip_members:
        student_records, iron_awards, failed_students = fetch_cpcfinder_student_results(
            public_members, args.source_url, args.workers, args.student_results_cache,
        )
        fetched = deduplicate([*fetched, *student_records])
    enriched = 0
    failed: list[int] = []
    if not args.html_file and not args.skip_members:
        enriched, failed = enrich_cpcfinder_members(fetched, args.source_url, args.workers)
    apply_iron_counts(public_members, fetched, iron_awards, failed)
    fetched = [record for record in fetched if not record.get("requiresSchoolVerification") or record.get("schoolVerified")]
    for record in fetched:
        record.pop("requiresSchoolVerification", None)
    supplements = load_supplements(args.supplement)
    honors = merge_with_existing(fetched, site.get("honors", []), supplements)
    site["honors"] = honors
    site["publicMembers"] = public_members
    site["medalSummary"] = medal_summary(honors)
    site["meta"]["updatedAt"] = dt.date.today().isoformat()
    site.pop("ratingGroups", None)

    missing = sum(not item.get("members") for item in honors)
    print(
        f"records={len(honors)} rosters={len(honors) - missing} missing={missing} "
        f"enriched={enriched} public_members={len(public_members)} supplements={len(supplements)} "
        f"iron_records={sum(row['medal'] == '铁牌' for row in honors)} "
        f"failed_contests={len(failed)} failed_students={len(failed_students)}"
    )
    if not args.dry_run:
        write_json_atomic(args.site_data, site)
        Database(args.database).initialize(site)


if __name__ == "__main__":
    main()
