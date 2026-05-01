from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from claude_runner import ClaudeGateway
from config import BotConfig

logger = logging.getLogger(__name__)


@dataclass
class Session:
    channel_id: str
    gateway: ClaudeGateway
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    message_count: int = 0
    total_cost_usd: float = 0.0
    running: bool = False
    custom_name: str = ""
    model_override: str = ""
    effort_override: str = ""
    message_queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=10))


class SessionManager:
    def __init__(self, config: BotConfig):
        self._config = config
        self._sessions: dict[str, Session] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None

    def start_cleanup_loop(self):
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def _cleanup_loop(self):
        while True:
            await asyncio.sleep(300)
            try:
                count = await self.cleanup_expired()
                if count:
                    logger.info("Cleaned up %d expired session(s)", count)
            except Exception:
                logger.exception("Session cleanup error")

    async def get_or_create(self, channel_id: str) -> Session:
        async with self._lock:
            session = self._sessions.get(channel_id)
            if session:
                session.last_activity = time.time()
                return session
            gateway = ClaudeGateway(self._config)
            session = Session(channel_id=channel_id, gateway=gateway)
            self._sessions[channel_id] = session
            logger.info("Created session for channel %s", channel_id)
            return session

    async def reset(self, channel_id: str) -> None:
        async with self._lock:
            old = self._sessions.get(channel_id)
            if old:
                if old.running:
                    await old.gateway.interrupt()
                await old.gateway.disconnect()
            gateway = ClaudeGateway(self._config)
            self._sessions[channel_id] = Session(channel_id=channel_id, gateway=gateway)
            logger.info("Reset session for channel %s", channel_id)

    async def stop(self, channel_id: str) -> bool:
        async with self._lock:
            session = self._sessions.get(channel_id)
            if not session or not session.running:
                return False
            await session.gateway.interrupt()
            session.running = False
            return True

    async def cleanup_expired(self) -> int:
        timeout = self._config.session_timeout_minutes * 60
        now = time.time()
        expired: list[str] = []
        async with self._lock:
            for cid, session in self._sessions.items():
                if not session.running and (now - session.last_activity) > timeout:
                    expired.append(cid)
            for cid in expired:
                session = self._sessions.pop(cid)
                await session.gateway.disconnect()
        return len(expired)

    def get_status(self, channel_id: str) -> dict:
        session = self._sessions.get(channel_id)
        if not session:
            return {"active": False}
        return {
            "active": True,
            "connected": session.gateway.connected,
            "name": session.custom_name or "(unnamed)",
            "model": session.model_override or self._config.claude_model or "(default)",
            "effort": session.effort_override or self._config.claude_effort or "(default)",
            "messages": session.message_count,
            "cost_usd": round(session.total_cost_usd, 4),
            "running": session.running,
            "idle_minutes": round((time.time() - session.last_activity) / 60, 1),
            "queued": session.message_queue.qsize(),
        }

    async def resume(self, channel_id: str, session_id: str) -> None:
        async with self._lock:
            old = self._sessions.get(channel_id)
            if old:
                if old.running:
                    await old.gateway.interrupt()
                await old.gateway.disconnect()
            gateway = ClaudeGateway(self._config)
            session = Session(channel_id=channel_id, gateway=gateway)
            self._sessions[channel_id] = session
        await gateway.connect(resume_session_id=session_id)
        logger.info("Resumed session %s for channel %s", session_id[:8], channel_id)

    def list_recent_sessions(self, limit: int = 10) -> list:
        return ClaudeGateway.list_recent_sessions(self._config.claude_working_dir, limit=limit)

    def active_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.running)

    def get_session(self, channel_id: str) -> Optional[Session]:
        return self._sessions.get(channel_id)
