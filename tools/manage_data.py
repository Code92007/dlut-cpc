#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import Database  # noqa: E402


DEFAULT_DATABASE = ROOT / "runtime" / "dlut_cpc.sqlite3"
DEFAULT_SITE_DATA = ROOT / "data" / "site.json"


def load_seed(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def source_from_args(args: argparse.Namespace) -> dict:
    return {"name": args.source_name, "url": args.source_url, "kind": "manual", "priority": 100}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the DLUT CPC member and honor database")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--site-data", type=Path, default=DEFAULT_SITE_DATA)
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("list-members", help="list all members and their active years")
    listing.add_argument("--query", default="")

    add_member = commands.add_parser("add-member", help="add a manually maintained member")
    add_member.add_argument("--name", required=True)
    add_member.add_argument("--entry-year", type=int)
    add_member.add_argument("--graduation-year", type=int)
    add_member.add_argument("--status", choices=("current", "alumni", "unknown"), default="alumni")
    add_member.add_argument("--notes", default="")
    add_member.add_argument("--match-existing", action="store_true")
    add_member.add_argument("--source-name", default="人工录入")
    add_member.add_argument("--source-url", default="")

    handle = commands.add_parser("set-handle", help="set a verified platform account for a member")
    handle.add_argument("--member-id", type=int, required=True)
    handle.add_argument("--platform", default="codeforces")
    handle.add_argument("--handle", required=True)
    handle.add_argument("--rating", type=int)
    handle.add_argument("--unverified", action="store_true")
    handle.add_argument("--source-name", default="人工确认")
    handle.add_argument("--source-url", default="")

    add_honor = commands.add_parser("add-honor", help="add an older or otherwise missing honor")
    add_honor.add_argument("--event", required=True)
    add_honor.add_argument("--series", default="其他")
    add_honor.add_argument("--date", required=True)
    add_honor.add_argument("--location", default="")
    add_honor.add_argument("--team", required=True)
    add_honor.add_argument("--medal", choices=("金牌", "银牌", "铜牌", "冠军", "亚军", "季军"), required=True)
    add_honor.add_argument("--rank", default="")
    add_honor.add_argument("--overall-rank", default="")
    add_honor.add_argument("--member-id", type=int, action="append", default=[])
    add_honor.add_argument("--source-name", default="人工录入")
    add_honor.add_argument("--source-url", default="")

    link = commands.add_parser("link-member", help="attach an existing member to an honor")
    link.add_argument("--honor-id", required=True)
    link.add_argument("--member-id", type=int, required=True)
    link.add_argument("--source-name", default="人工确认")
    link.add_argument("--source-url", default="")

    commands.add_parser("list-missing", help="list honors without a member roster")

    export = commands.add_parser("export", help="export the merged database payload as JSON")
    export.add_argument("--output", type=Path, required=True)

    backup = commands.add_parser("backup", help="create a consistent SQLite backup")
    backup.add_argument("--output", type=Path, required=True)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    seed = load_seed(args.site_data)
    database = Database(args.database)
    database.initialize(seed)

    if args.command == "list-members":
        query = args.query.casefold().strip()
        for member in database.payload(seed)["members"]:
            haystack = " ".join([member["name"], *member["teams"]]).casefold()
            if query and query not in haystack:
                continue
            years = f"{member['firstYear'] or '-'}..{member['lastYear'] or '-'}"
            print(f"{member['id']:>4}  {member['name']:<12}  {years:<11}  {member['honorCount']} honors")
        return

    if args.command == "add-member":
        member_id = database.add_manual_member(
            args.name,
            entry_year=args.entry_year,
            graduation_year=args.graduation_year,
            status=args.status,
            notes=args.notes,
            source=source_from_args(args),
            match_existing=args.match_existing,
        )
        print(f"member_id={member_id}")
        return

    if args.command == "set-handle":
        database.set_handle(
            args.member_id,
            args.platform,
            args.handle,
            rating=args.rating,
            verified=not args.unverified,
            source=source_from_args(args),
        )
        print(f"updated member_id={args.member_id} platform={args.platform}")
        return

    if args.command == "add-honor":
        source = source_from_args(args)
        honor_id = database.add_manual_honor(
            {
                "event": args.event,
                "series": args.series,
                "date": args.date,
                "location": args.location,
                "team": args.team,
                "medal": args.medal,
                "rank": args.rank,
                "overallRank": args.overall_rank,
                "source": source,
            }
        )
        for member_id in args.member_id:
            database.link_member(honor_id, member_id, source=source)
        print(f"honor_id={honor_id}")
        return

    if args.command == "link-member":
        database.link_member(args.honor_id, args.member_id, source=source_from_args(args))
        print(f"linked honor_id={args.honor_id} member_id={args.member_id}")
        return

    if args.command == "list-missing":
        rows = database.missing_honors()
        for row in rows:
            print(f"{row['id']}  {row['date']}  {row['team']}  {row['event']}")
        print(f"missing={len(rows)}")
        return

    if args.command == "export":
        database.export_json(seed, args.output)
        print(args.output)
        return

    if args.command == "backup":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(args.database)) as source, sqlite3.connect(str(args.output)) as target:
            source.backup(target)
        print(args.output)


if __name__ == "__main__":
    main()
