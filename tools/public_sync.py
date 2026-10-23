from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import threading
from pathlib import Path

from database import Database, load_seed_file
from tools.public_updates import ingest_public_results
from tools.rankland_sync import fetch_rankland_updates, save_rankland_versions
from tools.sync_public_data import build_public_snapshot, write_json_atomic


def refresh_public_data(database_path: Path, seed_path: Path, *, workers: int = 4) -> dict:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with (database_path.parent / "public_sync.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "running"}
        return _refresh_public_data(database_path, seed_path, workers=workers)


def _refresh_public_data(database_path: Path, seed_path: Path, *, workers: int) -> dict:
    status_path = database_path.parent / "public_sync_status.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    except (OSError, ValueError):
        status = {}
    status.update(status="running", lastAttemptAt=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), error=None)
    write_json_atomic(status_path, status)
    try:
        database = Database(database_path)
        database.initialize()
        seed = load_seed_file(seed_path)
        cache_path = database_path.parent / "public_site.json"
        if cache_path.exists():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                for field in ("honors", "publicMembers"):
                    if field in cached:
                        seed[field] = cached[field]
            except (OSError, ValueError, TypeError) as exc:
                print(f"Public data refresh: ignoring invalid source cache: {exc}", flush=True)
        records, source_errors, summaries = [], {}, {}
        public_members, snapshot = None, None
        try:
            snapshot, summary = build_public_snapshot(seed, workers=workers)
            records.extend(snapshot["honors"])
            summaries["cpcfinder"] = summary
            if not summary["studentDirectoryFailed"]:
                public_members = snapshot["publicMembers"]
            if summary["failedContests"] or summary["failedStudents"] or summary["studentDirectoryFailed"]:
                source_errors["cpcfinder"] = "部分选手或赛事接口未成功，保留已确认记录并稍后重试"
        except Exception as exc:
            source_errors["cpcfinder"] = str(exc)
        checked = []
        try:
            rankland_records, checked, summary = fetch_rankland_updates(database, workers=workers)
            records.extend(rankland_records)
            summaries["rankland"] = summary
            if summary["failedContests"]:
                source_errors["rankland"] = "部分榜单抓取失败，将重试"
        except Exception as exc:
            source_errors["rankland"] = str(exc)
        report = ingest_public_results(database, records, public_members=public_members)
        save_rankland_versions(database, checked)
        if snapshot is not None:
            public = {field: snapshot[field] for field in ("honors", "publicMembers", "medalSummary", "meta")}
            write_json_atomic(cache_path, public)
        state = "failed" if not summaries else "partial" if source_errors else "ok"
        status.update(status=state, summary=report, sources=summaries, sourceErrors=source_errors)
        if state == "ok":
            status["lastSuccessAt"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    except Exception as exc:
        status.update(status="failed", error=str(exc))
        raise
    finally:
        status["lastFinishedAt"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        write_json_atomic(status_path, status)
    return status


def refresh_public_data_periodically(
    stop: threading.Event, database_path: Path, seed_path: Path, interval: float, retry_interval: float = 3600,
) -> None:
    status_path = database_path.parent / "public_sync_status.json"
    if status_path.exists():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
            finished = dt.datetime.fromisoformat(status["lastFinishedAt"])
            delay = interval if status["status"] == "ok" else retry_interval
            remaining = delay - (dt.datetime.now(dt.timezone.utc) - finished).total_seconds()
            if remaining > 0 and stop.wait(remaining):
                return
        except (OSError, ValueError, KeyError, TypeError) as exc:
            print(f"Public data refresh: ignoring invalid schedule state: {exc}", flush=True)
    while not stop.is_set():
        delay = interval
        try:
            status = refresh_public_data(database_path, seed_path)
            print(f"Public data refresh: {json.dumps(status, ensure_ascii=False)}", flush=True)
            if status["status"] != "ok":
                delay = retry_interval
        except Exception as exc:
            delay = retry_interval
            print(f"Public data refresh failed; existing data retained: {exc}", flush=True)
        if stop.wait(delay):
            return


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Discover new public results and queue changes for admin A/B review")
    parser.add_argument("--once", action="store_true", required=True)
    parser.add_argument("--database", type=Path, default=Path(os.environ.get("DATABASE_PATH", root / "runtime/dlut_cpc.sqlite3")))
    parser.add_argument("--site-data", type=Path, default=Path(os.environ.get("SITE_DATA_PATH", root / "data/site.json")))
    args = parser.parse_args()
    status = refresh_public_data(args.database, args.site_data)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if status["status"] not in {"ok", "running"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
