"""Retire the temporary 2026 Leshan results in favor of CPC Finder.

Run with --database PATH to preview, then add --apply --backup PATH.
Raw source evidence is retained; only CPC Finder links remain on public results.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sqlite3
from pathlib import Path

from database import Database
from official_imports import _merge_duplicate_honor


# Explicit source identities keep this maintenance operation confined to four teams.
TEAMS = (
    ("K05", "37967", "这么六"),
    ("A03", "38076", "猫猫虫小队"),
    ("M24", "38052", "如果只转身后退就能回到那个夏天"),
    ("N16", "38198", "k神岁岁闪耀"),
)


def retire_results(database: Database, connection: sqlite3.Connection) -> list[dict]:
    payload = {item["id"]: item for item in database._honors_payload(connection)}
    plan = []
    for team_id, award_id, team in TEAMS:
        source = connection.execute(
            "SELECT honor_id,record_json FROM public_result_sources WHERE provider='cpcfinder' AND external_id=?",
            (award_id,),
        ).fetchone()
        if not source or source["honor_id"] not in payload:
            raise ValueError(f"CPC Finder result missing: {team}")
        record = json.loads(source["record_json"])
        target = payload[source["honor_id"]]
        old = connection.execute(
            "SELECT id FROM honors WHERE external_provider='rankland' AND external_contest_id='ccpc2026leshan' AND external_team_id=?",
            (team_id,),
        ).fetchone()
        if (str(record.get("cpcfinderContestId")) != "88" or record.get("team") != team
                or record.get("date") != "2026-11-08" or record.get("series") != "CCPC"
                or record.get("official") is not True):
            raise ValueError(f"Unexpected CPC Finder identity: {team}")
        identities = {detail.get("externalId") for detail in record.get("memberDetails", [])}
        if len(identities) != 3 or None in identities:
            raise ValueError(f"Incomplete CPC Finder roster: {team}")
        for result in (target, payload[old["id"]] if old else target):
            if (result["team"] != team or result["date"] != record["date"]
                    or result["series"] != "CCPC" or result["official"] is not True
                    or result["medal"] != record["medal"]
                    or result["rank"].split("/")[0].strip() != record["rank"].split("/")[0].strip()
                    or {detail.get("externalId") for detail in result["memberDetails"]} != identities):
                raise ValueError(f"Result or roster mismatch: {team}")
        plan.append((old["id"] if old else None, target["id"], record))

    report = []
    for old_id, target_id, record in plan:
        if old_id and old_id != target_id:
            for table in ("public_result_sources", "public_result_diffs"):
                connection.execute(f"UPDATE {table} SET honor_id=? WHERE honor_id=?", (target_id, old_id))
            _merge_duplicate_honor(connection, old_id, target_id)
            connection.execute("DELETE FROM metadata WHERE key=?", (f"public_result_guard:{old_id}",))
        # Keep each surviving local ID and curated roster; replace the temporary
        # source identity and rankings with the now complete CPC Finder result.
        source_id = database._source(connection, record["source"])
        connection.execute(
            "UPDATE honors SET external_provider='cpcfinder',external_award_id=?,external_contest_id='88',"
            "external_team_id=?,rank=?,overall_rank=?,location='乐山',primary_source_id=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (str(record["cpcfinderAwardId"]), str(record["cpcfinderTeamId"]), record["rank"], record["overallRank"], source_id, target_id),
        )
        connection.execute(
            "DELETE FROM honor_sources WHERE honor_id=? AND source_id IN "
            "(SELECT id FROM sources WHERE url LIKE 'https://rl.algoux.cn/ranklist/ccpc2026leshan%' "
            "OR url='https://rank.iccpc.cn/r/6503f86d-8281-4d33-9da0-b41e6fff4726')",
            (target_id,),
        )
        for source in database._honor_sources(record):
            connection.execute(
                "INSERT OR IGNORE INTO honor_sources(honor_id,source_id,role) VALUES (?,?,'result')",
                (target_id, database._source(connection, source)),
            )
        # The user's choice of CPC Finder also resolves these obsolete rank-only diffs.
        connection.execute(
            "UPDATE public_result_diffs SET status='applied',reviewer='leshan-cpcfinder-20261114',reviewed_at=CURRENT_TIMESTAMP "
            "WHERE honor_id=? AND provider='cpcfinder' AND external_id=? AND status='pending' "
            "AND fields_json IN ('[\"rank\", \"overallRank\"]','[\"rank\"]','[\"overallRank\"]')",
            (target_id, str(record["cpcfinderAwardId"])),
        )
        connection.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES (?,'1')", (f"public_result_guard:{target_id}",))
        report.append({"team": record["team"], "removed": old_id if old_id != target_id else None,
                       "retained": target_id, "rank": record["rank"]})
    connection.execute(
        "DELETE FROM public_result_sources WHERE provider IN ('rankland','iccpc') AND external_id LIKE 'ccpc2026leshan:%'"
    )
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Foreign key validation failed")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("runtime/dlut_cpc.sqlite3"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    if not args.database.is_file():
        parser.error("Database does not exist")
    if args.apply and (not args.backup or args.backup.exists()):
        parser.error("--apply requires a new --backup path")
    database = Database(args.database)
    with (database.path.parent / "public_sync.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with database.connect() as connection:
            if args.apply:
                args.backup.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(args.backup) as backup:
                    connection.backup(backup)
            connection.execute("BEGIN IMMEDIATE")
            report = retire_results(database, connection)
            if not args.apply:
                connection.rollback()
            print(json.dumps({"applied": args.apply, "results": report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
