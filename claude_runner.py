from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    SDKSessionInfo,
    list_sessions,
    rename_session,
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
    task_id: str = ""
    task_description: str = ""
    task_status: str = ""
    task_summary: str = ""
    task_last_tool: str = ""


@dataclass
class ClaudeResult:
    text: str = ""
    is_error: bool = False
    cost_usd: float = 0.0
    session_id: str = ""


class ClaudeGateway:
    """Manages a persistent ClaudeSDKClient per channel."""

    def __init__(self, config: BotConfig, effort_override: str = ""):
        self.config = config
        self._effort_override = effort_override
        self._client: Optional[ClaudeSDKClient] = None
        self._connected = False
        self.session_id: Optional[str] = None
        # Track pending tool_use input across streaming events
        self._pending_tool_name: str = ""
        self._pending_tool_id: str = ""
        self._pending_input_json: str = ""

    def _build_options(self) -> ClaudeAgentOptions:
        opts: dict = {
            "cwd": self.config.claude_working_dir or ".",
            "include_partial_messages": True,
            "cli_path": self.config.claude_binary,
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

        effort = self._effort_override or self.config.claude_effort
        if effort:
            opts["effort"] = effort
        if self.config.claude_fallback_model:
            opts["fallback_model"] = self.config.claude_fallback_model
        if self.config.claude_add_dirs:
            opts["add_dirs"] = self.config.claude_add_dirs
        if self.config.claude_mcp_config:
            cfg = self.config.claude_mcp_config.strip()
            try:
                if cfg.startswith("{"):
                    opts["mcp_servers"] = json.loads(cfg)
                else:
                    opts["mcp_servers"] = cfg
            except json.JSONDecodeError:
                logger.warning("Invalid CLAUDE_MCP_CONFIG JSON, ignoring")
        if self.config.claude_plugins:
            opts["plugins"] = [{"type": "local", "path": p} for p in self.config.claude_plugins]
        if self.config.claude_task_budget_tokens > 0:
            opts["task_budget"] = {"total": self.config.claude_task_budget_tokens}

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

    @staticmethod
    def _get(obj, key, default=None):
        """Get a value from a dict or object attribute."""
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    def _adapt(self, msg) -> Optional[ClaudeEvent]:
        """Convert SDK message types to ClaudeEvent for stream_consumer compatibility."""
        g = self._get
        t = type(msg).__name__

        if t == "TaskStartedMessage":
            return ClaudeEvent(
                type="task", subtype="started",
                task_id=g(msg, "task_id", ""),
                task_description=g(msg, "description", ""),
            )

        if t == "TaskProgressMessage":
            return ClaudeEvent(
                type="task", subtype="progress",
                task_id=g(msg, "task_id", ""),
                task_description=g(msg, "description", ""),
                task_last_tool=g(msg, "last_tool_name", "") or "",
            )

        if t == "TaskNotificationMessage":
            return ClaudeEvent(
                type="task", subtype="notification",
                task_id=g(msg, "task_id", ""),
                task_status=g(msg, "status", ""),
                task_summary=g(msg, "summary", "") or "",
            )

        if t == "SystemMessage":
            return ClaudeEvent(type="system", subtype=g(msg, "subtype", ""))

        if t == "StreamEvent":
            evt = msg.event
            evt_type = g(evt, "type", "")

            if evt_type == "content_block_delta":
                delta = g(evt, "delta")
                if delta:
                    delta_type = g(delta, "type", "")
                    if delta_type == "text_delta":
                        return ClaudeEvent(type="stream_event", text_delta=g(delta, "text", ""))
                    elif delta_type == "thinking_delta":
                        return ClaudeEvent(type="stream_event", thinking_delta=g(delta, "thinking", ""))
                    elif delta_type == "input_json_delta":
                        self._pending_input_json += g(delta, "partial_json", "")

            elif evt_type == "content_block_start":
                block = g(evt, "content_block")
                if block and g(block, "type", "") == "tool_use":
                    self._pending_tool_name = g(block, "name", "")
                    self._pending_tool_id = g(block, "id", "")
                    self._pending_input_json = ""
                    return ClaudeEvent(
                        type="stream_event",
                        tool_use={"name": self._pending_tool_name, "id": self._pending_tool_id},
                    )

            elif evt_type == "content_block_stop":
                if self._pending_tool_name:
                    tool_input = {}
                    if self._pending_input_json:
                        try:
                            tool_input = json.loads(self._pending_input_json)
                        except json.JSONDecodeError:
                            pass
                    event = ClaudeEvent(
                        type="tool_use_complete",
                        tool_use={
                            "name": self._pending_tool_name,
                            "id": self._pending_tool_id,
                            "input": tool_input,
                        },
                    )
                    self._pending_tool_name = ""
                    self._pending_tool_id = ""
                    self._pending_input_json = ""
                    return event

            return None

        if t == "AssistantMessage":
            texts = []
            tool = {}
            for block in g(msg, "content", []) or []:
                btype = g(block, "type", "")
                if btype == "text":
                    texts.append(g(block, "text", ""))
                elif btype == "tool_use":
                    tool = {
                        "name": g(block, "name", ""),
                        "id": g(block, "id", ""),
                        "input": g(block, "input", {}),
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
                result=g(msg, "result", "") or "",
                is_error=g(msg, "is_error", False),
                cost_usd=g(msg, "total_cost_usd", 0.0) or 0.0,
                session_id=g(msg, "session_id", "") or "",
            )

        return None

    async def set_model(self, model: str) -> None:
        if self._client and self._connected:
            await self._client.set_model(model)

    async def rename_current_session(self, name: str) -> None:
        if self.session_id:
            await asyncio.to_thread(
                rename_session, self.session_id, name, self.config.claude_working_dir
            )

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
