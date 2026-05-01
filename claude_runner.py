from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SDKSessionInfo,
    StreamEvent,
    SystemMessage,
    list_sessions,
)

from config import BotConfig

logger = logging.getLogger(__name__)


@dataclass
class ClaudeEvent:
    type: str = ""
    subtype: str = ""
    text_delta: str = ""
    thinking_delta: str = ""
    tool_use: dict = field(default_factory=dict)
    result: str = ""
    is_error: bool = False
    cost_usd: float = 0.0
    session_id: str = ""


@dataclass
class ClaudeResult:
    text: str = ""
    is_error: bool = False
    cost_usd: float = 0.0
    session_id: str = ""


class ClaudeGateway:
    """Manages a persistent ClaudeSDKClient per channel."""

    def __init__(self, config: BotConfig):
        self.config = config
        self._client: Optional[ClaudeSDKClient] = None
        self._connected = False
        self.session_id: Optional[str] = None

    def _build_options(self) -> ClaudeAgentOptions:
        opts: dict = {
            "cwd": self.config.claude_working_dir or ".",
            "include_partial_messages": True,
        }
        if self.config.claude_model:
            opts["model"] = self.config.claude_model
        if self.config.claude_permission_mode:
            opts["permission_mode"] = self.config.claude_permission_mode
        if self.config.claude_max_budget_usd > 0:
            opts["max_budget_usd"] = self.config.claude_max_budget_usd
        if self.config.claude_allowed_tools:
            opts["allowed_tools"] = [t.strip() for t in self.config.claude_allowed_tools.split(",") if t.strip()]
        if self.config.claude_system_prompt:
            opts["system_prompt"] = self.config.claude_system_prompt
        return ClaudeAgentOptions(**opts)

    async def connect(self, initial_prompt: Optional[str] = None, resume_session_id: Optional[str] = None) -> None:
        if self._connected:
            await self.disconnect()
        opts = self._build_options()
        if resume_session_id:
            opts.resume = resume_session_id
        self._client = ClaudeSDKClient(options=opts)
        await self._client.connect(initial_prompt)
        self._connected = True
        logger.info("SDK client connected (resume=%s)", resume_session_id or "new")

    async def query(
        self,
        prompt: str,
        on_event: Optional[Callable[[ClaudeEvent], Awaitable[None]]] = None,
    ) -> ClaudeResult:
        if not self._connected or not self._client:
            await self.connect(prompt)
            return await self._receive(on_event)
        else:
            await self._client.query(prompt)
            return await self._receive(on_event)

    async def _receive(
        self,
        on_event: Optional[Callable[[ClaudeEvent], Awaitable[None]]] = None,
    ) -> ClaudeResult:
        result = ClaudeResult()
        assert self._client is not None

        async for msg in self._client.receive_response():
            event = self._adapt(msg)
            if not event:
                continue

            if on_event:
                await on_event(event)

            if event.type == "result":
                result.text = event.result
                result.is_error = event.is_error
                result.cost_usd = event.cost_usd
                result.session_id = event.session_id
                if event.session_id:
                    self.session_id = event.session_id

        return result

    def _adapt(self, msg) -> Optional[ClaudeEvent]:
        """Convert SDK message types to ClaudeEvent for stream_consumer compatibility."""
        t = type(msg).__name__

        if t == "SystemMessage":
            return ClaudeEvent(type="system", subtype=getattr(msg, "subtype", ""))

        if t == "StreamEvent":
            evt = msg.event
            evt_type = getattr(evt, "type", "")

            if evt_type == "content_block_delta":
                delta = getattr(evt, "delta", None)
                if delta:
                    delta_type = getattr(delta, "type", "")
                    if delta_type == "text_delta":
                        return ClaudeEvent(type="stream_event", text_delta=getattr(delta, "text", ""))
                    elif delta_type == "thinking_delta":
                        return ClaudeEvent(type="stream_event", thinking_delta=getattr(delta, "thinking", ""))

            elif evt_type == "content_block_start":
                block = getattr(evt, "content_block", None)
                if block and getattr(block, "type", "") == "tool_use":
                    return ClaudeEvent(
                        type="stream_event",
                        tool_use={"name": getattr(block, "name", ""), "id": getattr(block, "id", "")},
                    )

            return None

        if t == "AssistantMessage":
            texts = []
            tool = {}
            for block in getattr(msg, "content", []) or []:
                btype = getattr(block, "type", "")
                if btype == "text":
                    texts.append(getattr(block, "text", ""))
                elif btype == "tool_use":
                    tool = {
                        "name": getattr(block, "name", ""),
                        "id": getattr(block, "id", ""),
                        "input": getattr(block, "input", {}),
                    }
            return ClaudeEvent(
                type="assistant",
                result="\n".join(texts) if texts else "",
                tool_use=tool,
            )

        if t == "ResultMessage":
            return ClaudeEvent(
                type="result",
                subtype="result",
                result=getattr(msg, "result", "") or "",
                is_error=getattr(msg, "is_error", False),
                cost_usd=getattr(msg, "total_cost_usd", 0.0) or 0.0,
                session_id=getattr(msg, "session_id", "") or "",
            )

        return None

    async def interrupt(self) -> None:
        if self._client and self._connected:
            try:
                await self._client.interrupt()
            except Exception as e:
                logger.warning("Interrupt failed: %s", e)

    async def disconnect(self) -> None:
        if self._client and self._connected:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._connected = False
            self._client = None
            logger.info("SDK client disconnected")

    async def resume(self, session_id: str, prompt: Optional[str] = None) -> None:
        await self.disconnect()
        await self.connect(initial_prompt=prompt, resume_session_id=session_id)

    @staticmethod
    def list_recent_sessions(working_dir: str, limit: int = 10) -> list[SDKSessionInfo]:
        return list_sessions(working_dir, limit=limit)

    @property
    def connected(self) -> bool:
        return self._connected
