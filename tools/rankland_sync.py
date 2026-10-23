from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import re

from contest_names import EXCLUDED, SITES
from database import Database
from tools.import_rankland import ORIGIN, parse_initial_state, parse_ranklist
from tools.sync_public_data import fetch_text


def eligible_ranklist(entry: dict) -> bool:
    key = str(entry.get("uniqueKey", ""))
    identity = re.fullmatch(r"(?:icpc|ccpc)((?:19|20)\d{2})([a-zA-Z0-9_-]+)", key)
    if not identity or EXCLUDED.search(str(entry.get("name", "")) + " " + key):
        return False
    suffix = identity[2].replace("_", "").replace("-", "").lower()
    return suffix in SITES or suffix in {"final", "ecfinal", "cnfinal"}


def fetch_rankland_updates(database: Database, *, workers: int = 4) -> tuple[list[dict], list[tuple[str, str]], dict]:
    state = parse_initial_state(fetch_text(f"{ORIGIN}/search"))
    entries = state["ranklistIndex"]["ranks"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("RankLand returned an empty or invalid catalog")
    with database.connect() as connection:
        versions = {row[0]: row[1] for row in connection.execute("SELECT contest_key,file_id FROM rankland_sync_catalog")}
        initial_scan = not versions
        for row in connection.execute("SELECT record_json FROM honor_source_records WHERE provider='rankland'"):
            record = json.loads(row[0])
            key, file_id = record.get("externalContestId"), record.get("archive", {}).get("fileId")
            if key and file_id and key not in versions:
                versions[key] = str(file_id)
    candidates, checked = [], []
    for entry in entries:
        if not eligible_ranklist(entry):
            continue
        key, file_id = entry["uniqueKey"], str(entry.get("fileID") or "")
        if not file_id:
            raise ValueError(f"RankLand catalog has no content version: {key}")
        bootstrap_history = initial_scan and key not in versions and int(re.search(r"\d{4}", key)[0]) < dt.datetime.now(dt.timezone.utc).year
        if versions.get(key) == file_id or bootstrap_history:
            checked.append((key, file_id))
        else:
            candidates.append(entry)

    def fetch(entry: dict) -> tuple[list[dict], dict]:
        document = fetch_text(f"{ORIGIN}/ranklist/{entry['uniqueKey']}")
        srk = parse_initial_state(document)["ranklistData"]["srk"]
        contest = srk["contest"]
        start = dt.datetime.fromisoformat(contest["startAt"].replace("Z", "+00:00"))
        duration, unit = contest["duration"]
        seconds = {"ms": .001, "s": 1, "min": 60, "h": 3600, "d": 86400}[unit] * duration
        if start + dt.timedelta(seconds=seconds) > dt.datetime.now(dt.timezone.utc):
            return [], {"excluded": "unfinished-contest"}
        return parse_ranklist(document, before_date=None, include_participation=True)

    records, failed, skipped = [], [], []
    unchanged_keys = {key for key, _ in checked}
    with database.connect() as connection:
        seen = set()
        for row in connection.execute("SELECT record_json FROM public_result_sources WHERE provider='rankland'"):
            record = json.loads(row[0])
            identity = record.get("externalAwardId")
            if record.get("externalContestId") in unchanged_keys and identity not in seen:
                seen.add(identity)
                records.append(record)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(workers, 4))) as pool:
        futures = {pool.submit(fetch, entry): entry for entry in candidates}
        for future in concurrent.futures.as_completed(futures):
            entry = futures[future]
            try:
                rows, audit = future.result()
                if audit.get("excluded"):
                    skipped.append({"key": entry["uniqueKey"], "reason": audit["excluded"]})
                    if audit["excluded"] == "unfinished-contest":
                        continue
                records.extend(rows)
                checked.append((entry["uniqueKey"], str(entry["fileID"])))
            except Exception as exc:
                failed.append({"key": entry["uniqueKey"], "error": str(exc)})
    return records, checked, {"catalogSize": len(entries), "fetchedContests": len(candidates), "records": len(records),
                              "failedContests": failed, "skippedContests": skipped}


def save_rankland_versions(database: Database, checked: list[tuple[str, str]]) -> None:
    with database.connect() as connection:
        connection.executemany(
            "INSERT INTO rankland_sync_catalog(contest_key,file_id) VALUES (?,?) "
            "ON CONFLICT(contest_key) DO UPDATE SET file_id=excluded.file_id,checked_at=CURRENT_TIMESTAMP", checked,
        )
