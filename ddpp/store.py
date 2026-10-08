"""Thread-safe, in-memory secret lifecycle and watch events."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import secrets
import threading
import time


@dataclass
class Watcher:
    event: threading.Event = field(default_factory=threading.Event)
    notice: str | None = None


@dataclass
class Entry:
    password: str
    expires_at: float
    reads_remaining: int
    watchers: list[Watcher] = field(default_factory=list)


def _timestamp(now: float) -> str:
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class PasswordStore:
    def __init__(self, clock=None):
        self.clock = clock or time.time
        self._entries: dict[str, Entry] = {}
        self._lock = threading.Lock()

    def _expire_locked(self, token: str, now: float):
        entry = self._entries.get(token)
        if entry is not None and now >= entry.expires_at:
            del self._entries[token]
            for watcher in entry.watchers:
                watcher.notice = f"220 EXPIRED {token} {_timestamp(now)}"
                watcher.event.set()
            return None
        return entry

    def store(self, ttl: int, reads: int, password: str) -> str:
        with self._lock:
            token = secrets.token_hex(16)
            while token in self._entries:
                token = secrets.token_hex(16)
            self._entries[token] = Entry(password, self.clock() + ttl, reads)
            return token

    def retrieve(self, token: str) -> str | None:
        with self._lock:
            now = self.clock()
            entry = self._expire_locked(token, now)
            if entry is None:
                return None
            password = entry.password
            entry.reads_remaining -= 1
            if entry.reads_remaining == 0:
                del self._entries[token]
                for watcher in entry.watchers:
                    watcher.notice = f"210 BURNED {token} {_timestamp(now)}"
                    watcher.event.set()
            return password

    def info(self, token: str) -> tuple[int, int] | None:
        with self._lock:
            now = self.clock()
            entry = self._expire_locked(token, now)
            if entry is None:
                return None
            return entry.reads_remaining, max(0, int(entry.expires_at - now))

    def watch(self, token: str, watcher: Watcher) -> None:
        with self._lock:
            entry = self._expire_locked(token, self.clock())
            if entry is not None:
                entry.watchers.append(watcher)

    def unwatch(self, token: str, watcher: Watcher) -> None:
        with self._lock:
            entry = self._entries.get(token)
            if entry is not None:
                try:
                    entry.watchers.remove(watcher)
                except ValueError:
                    pass

    def sweep(self) -> int:
        with self._lock:
            now = self.clock()
            expired = [token for token, entry in self._entries.items() if now >= entry.expires_at]
            for token in expired:
                self._expire_locked(token, now)
            return len(expired)
