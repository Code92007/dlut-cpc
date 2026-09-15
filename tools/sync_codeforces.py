#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from database import Database  # noqa: E402
from tools.sync_public_data import write_json_atomic  # noqa: E402


def validate_response(payload: dict, handles: list[str]) -> list[dict]:
    if payload.get("status") != "OK":
        raise ValueError(str(payload.get("comment") or "Codeforces API failed"))
    users = payload.get("result")
    if not isinstance(users, list) or len(users) != len(handles):
        raise ValueError("Codeforces API returned an incomplete account list")
    by_handle = {}
    for user in users:
        handle = user.get("handle", "")
        rating = user.get("rating")
        maximum = user.get("maxRating")
        if not isinstance(handle, str) or not handle or any(value is not None and type(value) is not int for value in (rating, maximum)):
            raise ValueError("Codeforces API returned an invalid account")
        by_handle[handle.casefold()] = {"handle": handle, "rating": rating, "maxRating": maximum}
    if set(by_handle) != {handle.casefold() for handle in handles}:
        raise ValueError("Codeforces API account identities do not match the requested handles")
    return [by_handle[handle.casefold()] for handle in handles]


def fetch_ratings(handles: list[str]) -> list[dict]:
    query = urllib.parse.urlencode({"handles": ";".join(handles), "checkHistoricHandles": "false"})
    request = urllib.request.Request(
        "https://codeforces.com/api/user.info?" + query,
        headers={"User-Agent": "DLUT-CPC/0.1 (member rating sync)"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except (ValueError, UnicodeError):
            raise ValueError(f"Codeforces API HTTP {exc.code}") from exc
    return validate_response(payload, handles)


def sync_ratings(database: Database, seed: dict, *, response: dict | None = None) -> tuple[list[dict], list[str]]:
    handles = sorted({account["handle"] for member in database.payload(seed)["members"]
                      for account in member.get("accounts", {}).get("codeforces", [])}, key=str.casefold)
    if not handles:
        return [], []
    errors = []
    if response is not None:
        updates = validate_response(response, handles)
    else:
        try:
            updates = fetch_ratings(handles)
        except ValueError as exc:
            if "not found" not in str(exc).casefold():
                raise
            # Isolate obsolete handles; retain their last known rating rather than inventing one.
            updates = []
            for handle in handles:
                time.sleep(2.1)
                try:
                    updates.extend(fetch_ratings([handle]))
                except (OSError, ValueError) as error:
                    errors.append(f"{handle}: {error}")
    timestamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    database.update_account_ratings(updates, timestamp)
    by_handle = {update["handle"].casefold(): update for update in updates}
    for binding in seed.get("accountBindings", []):
        for account in binding.get("accounts", {}).get("codeforces", []):
            update = by_handle.get(account["handle"].casefold())
            if update:
                account.update(rating=update["rating"], maxRating=update["maxRating"], ratingUpdatedAt=timestamp)
    return updates, errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh all recorded Codeforces account ratings")
    parser.add_argument("--database", type=Path, default=Path(os.environ.get("DATABASE_PATH", ROOT / "runtime/dlut_cpc.sqlite3")))
    parser.add_argument("--site-data", type=Path, default=Path(os.environ.get("SITE_DATA_PATH", ROOT / "data/site.json")))
    parser.add_argument("--response", type=Path, action="append", help="import downloaded official user.info responses (repeatable)")
    parser.add_argument("--database-only", action="store_true", help="do not update the version-controlled snapshot")
    args = parser.parse_args()
    seed = json.loads(args.site_data.read_text(encoding="utf-8"))
    database = Database(args.database)
    database.initialize(seed)
    response = None
    if args.response:
        users = []
        for path in args.response:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("status") != "OK":
                parser.exit(1, f"Failed API response: {payload.get('comment')}\n")
            users.extend(payload.get("result", []))
        response = {"status": "OK", "result": users}
    try:
        updates, errors = sync_ratings(database, seed, response=response)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Rating sync failed; existing ratings retained: {exc}\n")
    if not args.database_only:
        write_json_atomic(args.site_data, seed)
    print(f"Updated {len(updates)} Codeforces accounts")
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
