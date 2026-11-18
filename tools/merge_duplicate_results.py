"""Preview or merge selected duplicate results, keeping their source mappings."""
from __future__ import annotations

import argparse
import fcntl
import json
import sqlite3
from pathlib import Path

from database import Database
from tools.public_updates import merge_duplicate_result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("runtime/dlut_cpc.sqlite3"))
    parser.add_argument("--pair", nargs=2, action="append", required=True, metavar=("REMOVE", "KEEP"))
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
            report = [merge_duplicate_result(database, connection, source, target) for source, target in args.pair]
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Foreign key validation failed")
            if not args.apply:
                connection.rollback()
            print(json.dumps({"applied": args.apply, "results": report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
