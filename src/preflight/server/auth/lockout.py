from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class _Entry:
    failures: int = 0
    locked_until: datetime | None = None


class Lockout:
    """Counts failed logins per (username, source address), so a stranger cannot lock the owner out."""

    def __init__(self, max_failures: int, minutes: int):
        self.max_failures = max_failures
        self.duration = timedelta(minutes=minutes)
        self._entries: dict[tuple[str, str], _Entry] = {}

    def locked(self, username: str, source: str, now: datetime) -> bool:
        entry = self._entries.get((username, source))
        return bool(entry and entry.locked_until and entry.locked_until > now)

    def record_failure(self, username: str, source: str, now: datetime) -> None:
        entry = self._entries.setdefault((username, source), _Entry())
        entry.failures += 1
        if entry.failures >= self.max_failures:
            entry.failures, entry.locked_until = 0, now + self.duration

    def clear(self, username: str, source: str) -> None:
        self._entries.pop((username, source), None)
