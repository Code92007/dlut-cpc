#!/usr/bin/env python3
"""Preview or import a bundled result archive against this deployment's database."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from database import Database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=ROOT / "runtime/dlut_cpc.sqlite3")
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data/ccpc_official_honors.json")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--backup", type=Path, help="Back up the live SQLite database before preview/import; refuses to overwrite")
    args = parser.parse_args()
    if not args.database.is_file():
        raise SystemExit("Database does not exist; initialize the site first")
    if args.backup:
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        with args.backup.open("xb"):
            pass
        with sqlite3.connect(args.database) as source, sqlite3.connect(args.backup) as destination:
            source.backup(destination)
        print(f"Database backup: {args.backup}")
    batch = json.loads(args.snapshot.read_text(encoding="utf-8"))
    report = Database(args.database).merge_official_batch(batch, dry_run=args.dry_run)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "records"}, ensure_ascii=False))
    for record in report["records"]:
        print(f"{record['status']:<8} {record['date']} {record['event']} {record['team']} {record['medal']} "
              f"[{record['honorId']}] {'; '.join(record['warnings'])}")


if __name__ == "__main__":
    main()
