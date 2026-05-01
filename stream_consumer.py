from __future__ import annotations

import logging
import os
import time
from typing import Optional

import discord

from claude_runner import ClaudeEvent
from config import BotConfig
from message_formatter import (
    SAFE_LIMIT,
    format_tool_status,
    strip_ansi,
    truncate_message,
)

logger = logging.getLogger(__name__)

MAX_FLOOD_STRIKES = 3
PROGRESS_LIMIT = 1800


class DiscordStreamConsumer:
    def __init__(
        self,
        channel: discord.abc.Messageable,
        reference: Optional[discord.MessageReference] = None,
        config: Optional[BotConfig] = None,
        interaction: Optional[discord.Interaction] = None,
    ):
        self._channel = channel
        self._reference = reference
        self._config = config or BotConfig()
        self._interaction = interaction
        self._messages: list[discord.Message] = []
        self._current_message: Optional[discord.Message] = None
        self._accumulated = ""
        self._full_text = ""
        self._last_edit_time = 0.0
        self._flood_strikes = 0
        self._edit_disabled = False
        self._finalized = False
        # Progress log: accumulates tool calls and intermediate text
        self._progress_lines: list[str] = []
        self._progress_msg: Optional[discord.Message] = None
        self._last_progress_edit_time = 0.0
        self._in_tool_phase = False
        # Thinking
        self._thinking_accumulated = ""
        self._thinking_message: Optional[discord.Message] = None
        self._last_thinking_edit_time = 0.0
        # Task progress
        self._task_status_msg: Optional[discord.Message] = None
        # File tracking
        self._modified_files: list[str] = []

    async def on_event(self, event: ClaudeEvent) -> None:
        if self._finalized:
            return

        if event.text_delta:
            text = strip_ansi(event.text_delta)
            if text:
                if self._in_tool_phase:
                    self._accumulated = text
                    self._in_tool_phase = False
                else:
                    self._accumulated += text
                self._full_text += text
                await self._maybe_edit()

        elif event.thinking_delta and self._config.show_thinking:
            text = strip_ansi(event.thinking_delta)
            if text:
                self._thinking_accumulated += text
                await self._show_thinking()

        elif event.tool_use and event.tool_use.get("name") and event.type != "tool_use_complete":
            # content_block_start: just mark tool phase, don't display yet
            if self._accumulated.strip() and self._current_message:
                short = self._accumulated.strip()
                if len(short) > 100:
                    short = short[:97] + "..."
                self._progress_lines.append(f"💬 {short}")
                try:
                    await self._current_message.delete()
                except discord.HTTPException:
                    pass
                self._current_message = None
                self._messages = [m for m in self._messages if m != self._current_message]
                self._accumulated = ""
            self._in_tool_phase = True

        elif event.type == "tool_use_complete" and event.tool_use:
            # Full input available — display once with parameters
            status = format_tool_status(event.tool_use.get("name", ""), event.tool_use.get("input"))
            self._progress_lines.append(status)
            self._in_tool_phase = True
            await self._update_progress()

            if self._config.send_file_outputs:
                name = event.tool_use.get("name", "")
                inp = event.tool_use.get("input", {})
                if name in ("Write", "Edit") and isinstance(inp, dict) and inp.get("file_path"):
                    path = inp["file_path"]
                    if path not in self._modified_files:
                        self._modified_files.append(path)

        elif event.type == "task":
            await self._show_task_progress(event)

        elif event.type == "result":
            await self._finalize(event)

    async def _maybe_edit(self) -> None:
        if self._edit_disabled:
            return

        now = time.monotonic()
        interval = self._config.edit_interval * (2 ** self._flood_strikes)

        if not self._current_message:
            if len(self._accumulated) < self._config.buffer_threshold:
                return
            await self._send_new_message(self._accumulated + self._config.cursor)
            self._last_edit_time = now
            return

        if now - self._last_edit_time < interval:
            return

        display_text = self._accumulated + self._config.cursor
        if len(display_text) > SAFE_LIMIT:
            await self._handle_overflow()
            return

        await self._edit_message(display_text)
        self._last_edit_time = now

    async def _send_new_message(self, text: str) -> None:
        try:
            if self._interaction and not self._messages:
                msg = await self._interaction.followup.send(text[:SAFE_LIMIT], wait=True)
                self._interaction = None
            else:
                ref = self._reference if not self._messages else None
                msg = await self._channel.send(text[:SAFE_LIMIT], reference=ref)
            self._current_message = msg
            self._messages.append(msg)
        except discord.HTTPException as e:
            logger.warning("Failed to send message: %s", e)

    async def _edit_message(self, text: str) -> None:
        if not self._current_message:
            return
        try:
            await self._current_message.edit(content=text[:SAFE_LIMIT])
        except discord.HTTPException as e:
            if e.status == 429:
                self._flood_strikes += 1
                logger.warning("Rate limited (strike %d/%d)", self._flood_strikes, MAX_FLOOD_STRIKES)
                if self._flood_strikes >= MAX_FLOOD_STRIKES:
                    self._edit_disabled = True
            else:
                logger.warning("Failed to edit message: %s", e)

    async def _handle_overflow(self) -> None:
        if self._current_message:
            chunks = truncate_message(self._accumulated)
            try:
                await self._current_message.edit(content=chunks[0][:SAFE_LIMIT])
            except discord.HTTPException:
                pass
            for chunk in chunks[1:]:
                await self._send_new_message(chunk + self._config.cursor)
            self._accumulated = chunks[-1] if len(chunks) > 1 else ""

    async def _update_progress(self) -> None:
        now = time.monotonic()
        if now - self._last_progress_edit_time < self._config.edit_interval:
            return
        text = "\n".join(self._progress_lines)
        if len(text) > PROGRESS_LIMIT:
            # Keep header + last N lines that fit
            lines = self._progress_lines
            while len("\n".join(lines)) > PROGRESS_LIMIT and len(lines) > 3:
                lines = lines[1:]
            text = "...\n" + "\n".join(lines)
        try:
            if self._progress_msg:
                await self._progress_msg.edit(content=text[:SAFE_LIMIT])
            else:
                self._progress_msg = await self._channel.send(text[:SAFE_LIMIT])
        except discord.HTTPException:
            pass
        self._last_progress_edit_time = now

    async def _show_thinking(self) -> None:
        now = time.monotonic()
        if now - self._last_thinking_edit_time < self._config.edit_interval * 2:
            return
        display = self._thinking_accumulated
        if len(display) > SAFE_LIMIT - 10:
            display = display[:SAFE_LIMIT - 13] + "..."
        display = f"||{display}||"
        try:
            if self._thinking_message:
                await self._thinking_message.edit(content=display)
            else:
                self._thinking_message = await self._channel.send(display)
        except discord.HTTPException:
            pass
        self._last_thinking_edit_time = now

    async def _show_task_progress(self, event: ClaudeEvent) -> None:
        if event.subtype == "started":
            self._progress_lines.append(f"⚙️ **Task started:** {event.task_description}")
        elif event.subtype == "progress":
            tool_info = f" (using {event.task_last_tool})" if event.task_last_tool else ""
            self._progress_lines.append(f"⚙️ **Task:** {event.task_description}{tool_info}")
        elif event.subtype == "notification":
            emoji = {"completed": "✅", "failed": "❌", "stopped": "⛔"}.get(event.task_status, "❓")
            self._progress_lines.append(f"{emoji} **Task {event.task_status}:** {event.task_summary or event.task_description}")
        else:
            return
        await self._update_progress()

    async def _finalize(self, event: ClaudeEvent) -> None:
        self._finalized = True

        # Keep progress log visible (don't delete)
        if self._progress_msg and self._progress_lines:
            text = "\n".join(self._progress_lines)
            if len(text) > PROGRESS_LIMIT:
                lines = self._progress_lines
                while len("\n".join(lines)) > PROGRESS_LIMIT and len(lines) > 3:
                    lines = lines[1:]
                text = "...\n" + "\n".join(lines)
            try:
                await self._progress_msg.edit(content=text[:SAFE_LIMIT])
            except discord.HTTPException:
                pass

        if self._task_status_msg:
            try:
                await self._task_status_msg.delete()
            except discord.HTTPException:
                pass

        if self._thinking_message and self._thinking_accumulated:
            thinking = self._thinking_accumulated
            if len(thinking) > SAFE_LIMIT - 10:
                thinking = thinking[:SAFE_LIMIT - 13] + "..."
            try:
                await self._thinking_message.edit(content=f"||{thinking}||")
            except discord.HTTPException:
                pass

        candidates = [self._full_text, event.result or "", self._accumulated]
        final_text = max(candidates, key=len)
        final_text = strip_ansi(final_text)

        logger.info("Finalize: full_text=%d, event.result=%d, accumulated=%d -> final=%d chars",
                     len(self._full_text), len(event.result or ""),
                     len(self._accumulated), len(final_text))

        if not final_text:
            final_text = "(no output)"

        cost_suffix = ""
        if event.cost_usd > 0:
            cost_suffix = f"\n-# cost: ${event.cost_usd:.4f}"

        chunks = truncate_message(final_text)

        if cost_suffix and chunks:
            if len(chunks[-1]) + len(cost_suffix) <= SAFE_LIMIT:
                chunks[-1] += cost_suffix
            else:
                chunks.append(cost_suffix)

        if self._messages:
            for i, chunk in enumerate(chunks):
                if i < len(self._messages):
                    try:
                        await self._messages[i].edit(content=chunk[:SAFE_LIMIT])
                    except discord.HTTPException as e:
                        logger.warning("Failed to edit message %d in finalize: %s", i, e)
                else:
                    try:
                        await self._channel.send(chunk[:SAFE_LIMIT])
                    except discord.HTTPException as e:
                        logger.warning("Failed to send chunk %d in finalize: %s", i, e)
            for i in range(len(chunks), len(self._messages)):
                try:
                    await self._messages[i].delete()
                except discord.HTTPException:
                    pass
        else:
            for i, chunk in enumerate(chunks):
                try:
                    if self._interaction and i == 0:
                        await self._interaction.followup.send(chunk[:SAFE_LIMIT])
                        self._interaction = None
                    else:
                        ref = self._reference if i == 0 else None
                        await self._channel.send(chunk[:SAFE_LIMIT], reference=ref)
                except discord.HTTPException as e:
                    logger.warning("Failed to send chunk %d in finalize: %s", i, e)

        if self._config.send_file_outputs and self._modified_files:
            await self._send_file_outputs()

    async def _send_file_outputs(self) -> None:
        max_size = self._config.max_attachment_size_mb * 1024 * 1024
        files = []
        for path in self._modified_files[:5]:
            if os.path.exists(path) and os.path.getsize(path) <= max_size:
                files.append(discord.File(path))
        if files:
            try:
                await self._channel.send(files=files)
            except discord.HTTPException as e:
                logger.warning("Failed to send file outputs: %s", e)
