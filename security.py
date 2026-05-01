from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from config import BotConfig

logger = logging.getLogger(__name__)


class MessageDeduplicator:
    def __init__(self, max_size: int = 1000, ttl: float = 300.0):
        self._seen: dict[str, float] = {}
        self._max_size = max_size
        self._ttl = ttl

    def is_duplicate(self, message_id: str) -> bool:
        now = time.monotonic()
        if len(self._seen) > self._max_size:
            cutoff = now - self._ttl
            self._seen = {k: v for k, v in self._seen.items() if v > cutoff}
        if message_id in self._seen:
            return True
        self._seen[message_id] = now
        return False


class RateLimiter:
    def __init__(self, max_per_minute: int = 10):
        self._max = max_per_minute
        self._timestamps: dict[str, list[float]] = defaultdict(list)

    def check(self, user_id: str) -> bool:
        now = time.monotonic()
        window = [t for t in self._timestamps[user_id] if now - t < 60]
        self._timestamps[user_id] = window
        if len(window) >= self._max:
            return False
        self._timestamps[user_id].append(now)
        return True


class ThreadParticipationTracker:
    _MAX_TRACKED = 500

    def __init__(self, state_dir: str = "~/.claude-discord-bot"):
        self._path = Path(state_dir).expanduser() / "threads.json"
        self._threads: set[str] = self._load()

    def _load(self) -> set[str]:
        if self._path.exists():
            try:
                return set(json.loads(self._path.read_text(encoding="utf-8")))
            except Exception:
                pass
        return set()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        items = list(self._threads)
        if len(items) > self._MAX_TRACKED:
            items = items[-self._MAX_TRACKED:]
            self._threads = set(items)
        self._path.write_text(json.dumps(items), encoding="utf-8")

    def mark(self, channel_id: str) -> None:
        if channel_id not in self._threads:
            self._threads.add(channel_id)
            self._save()

    def __contains__(self, channel_id: str) -> bool:
        return channel_id in self._threads


class SecurityChecker:
    def __init__(self, config: BotConfig):
        self.config = config
        self.dedup = MessageDeduplicator()
        self.rate_limiter = RateLimiter()

    def is_user_allowed(self, user_id: str, member: Optional[Any] = None) -> bool:
        if not self.config.allowed_user_ids and not self.config.allowed_role_ids:
            return True
        if user_id in self.config.allowed_user_ids:
            return True
        if member and self.config.allowed_role_ids:
            member_role_ids = {r.id for r in getattr(member, "roles", [])}
            if member_role_ids & self.config.allowed_role_ids:
                return True
        return False

    def is_channel_allowed(self, channel_id: str) -> bool:
        if self.config.allowed_channels:
            return channel_id in self.config.allowed_channels
        return True
