from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path


class AdminAuth:
    def __init__(self, root: Path) -> None:
        self.username = os.environ.get("ADMIN_USERNAME", "admin")
        password = os.environ.get("ADMIN_PASSWORD", "")
        path = Path(os.environ.get("ADMIN_PASSWORD_FILE", root / "runtime/admin_password"))
        if not password and path.is_file():
            password = path.read_text(encoding="utf-8").strip()
        self.enabled = len(password) >= 12
        self.salt = secrets.token_bytes(16)
        self.password_hash = self._hash(password)
        self.sessions: dict[str, dict] = {}
        self.failures: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def _hash(self, password: str) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode(), self.salt, 100_000)

    def login(self, username: str, password: str, address: str) -> tuple[str | None, bool]:
        now = time.time()
        with self.lock:
            self.failures = {ip: [stamp for stamp in stamps if stamp > now - 600]
                             for ip, stamps in self.failures.items() if any(stamp > now - 600 for stamp in stamps)}
            if len(self.failures.get(address, [])) >= 10:
                return None, True
            valid = self.enabled and hmac.compare_digest(username.encode(), self.username.encode())
            valid = hmac.compare_digest(self._hash(password), self.password_hash) and valid
            if not valid:
                self.failures.setdefault(address, []).append(now)
                return None, False
            self.failures.pop(address, None)
            self._prune(now)
            token = secrets.token_urlsafe(32)
            self.sessions[token] = {"username": self.username, "csrf": secrets.token_urlsafe(32), "expires": now + 8 * 3600}
            return token, False

    def _prune(self, now: float) -> None:
        self.sessions = {token: session for token, session in self.sessions.items() if session["expires"] > now}

    @staticmethod
    def cookie_token(cookie_header: str) -> str:
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except Exception:
            return ""
        return cookie["dlut_admin"].value if "dlut_admin" in cookie else ""

    def session(self, cookie_header: str) -> dict | None:
        with self.lock:
            self._prune(time.time())
            session = self.sessions.get(self.cookie_token(cookie_header))
            return dict(session) if session else None

    def logout(self, cookie_header: str) -> None:
        with self.lock:
            self.sessions.pop(self.cookie_token(cookie_header), None)
