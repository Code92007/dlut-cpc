#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sqlite3
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from database import Database


ROOT = Path(__file__).resolve().parent
WEB_ROOT = ROOT / "web"
DATA_PATH = Path(os.environ.get("SITE_DATA_PATH", ROOT / "data" / "site.json"))
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", ROOT / "runtime" / "dlut_cpc.sqlite3"))
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
SPA_ROUTES = {"/", "/home", "/honor", "/rating", "/training"}


def load_seed_data() -> dict:
    with DATA_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)
    required = {"meta", "honors", "training"}
    missing = sorted(required.difference(data))
    if missing:
        raise ValueError(f"site data missing keys: {', '.join(missing)}")
    return data


def load_site_data() -> dict:
    seed = load_seed_data()
    database = Database(DATABASE_PATH)
    if not DATABASE_PATH.exists():
        database.initialize(seed)
    return database.payload(seed)


class SiteHandler(BaseHTTPRequestHandler):
    server_version = "DLUTCPC/0.1"

    def do_HEAD(self) -> None:  # noqa: N802
        self._head_only = True
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/healthz":
            self._send_json({"ok": True})
            return
        if path == "/api/site":
            try:
                self._send_json(load_site_data())
            except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
                self._send_json({"error": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        if path in SPA_ROUTES:
            self._send_file(WEB_ROOT / "index.html", cache=False)
            return

        asset = (WEB_ROOT / path.lstrip("/")).resolve()
        try:
            asset.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if asset.is_file():
            self._send_file(asset, cache=True)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)

    def _send_file(self, path: Path, *, cache: bool) -> None:
        body = path.read_bytes()
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if media_type.startswith("text/") or media_type in {"application/javascript", "image/svg+xml"}:
            media_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", media_type)
        cache_asset = cache and path.suffix not in {".js", ".css"}
        self.send_header("Cache-Control", "public, max-age=3600" if cache_asset else "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not getattr(self, "_head_only", False):
            self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="DLUT CPC team website")
    parser.add_argument("--check", action="store_true", help="validate data and exit")
    args = parser.parse_args()
    seed = load_seed_data()
    Database(DATABASE_PATH).initialize(seed)
    data = load_site_data()
    if args.check:
        print(
            f"ok: {len(data['honors'])} honors, {len(data['members'])} members, "
            f"{data['meta']['memberCoverage']}% roster coverage"
        )
        return
    server = ThreadingHTTPServer((HOST, PORT), SiteHandler)
    print(f"DLUT CPC listening on http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
