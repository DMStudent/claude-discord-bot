from __future__ import annotations

import logging
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
        # _accumulated: used for streaming display, may be reset on overflow
        self._accumulated = ""
        # _full_text: complete text from all text_deltas, never reset
        self._full_text = ""
        self._last_edit_time = 0.0
        self._flood_strikes = 0
        self._edit_disabled = False
        self._tool_status_msg: Optional[discord.Message] = None
        self._finalized = False

    async def on_event(self, event: ClaudeEvent) -> None:
        if self._finalized:
            return

        if event.text_delta:
            text = strip_ansi(event.text_delta)
            if text:
                self._accumulated += text
                self._full_text += text
                await self._maybe_edit()

        elif event.tool_use and event.tool_use.get("name"):
            await self._show_tool_status(event.tool_use)

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
                    logger.warning("Progressive edits disabled due to rate limiting")
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

    async def _show_tool_status(self, tool_use: dict) -> None:
        status_text = format_tool_status(tool_use.get("name", ""), tool_use.get("input"))
        try:
            if self._tool_status_msg:
                await self._tool_status_msg.edit(content=status_text)
            else:
                self._tool_status_msg = await self._channel.send(status_text)
        except discord.HTTPException:
            pass

    async def _finalize(self, event: ClaudeEvent) -> None:
        self._finalized = True

        if self._tool_status_msg:
            try:
                await self._tool_status_msg.delete()
            except discord.HTTPException:
                pass

        # Pick the most complete text: prefer full streamed text over result
        # event.result may only contain the last turn in multi-tool responses
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

        # Append cost to the last chunk if it fits
        if cost_suffix and chunks:
            if len(chunks[-1]) + len(cost_suffix) <= SAFE_LIMIT:
                chunks[-1] += cost_suffix
            else:
                chunks.append(cost_suffix)

        if self._messages:
            # Re-edit all existing messages with correct content, send new ones if needed
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
            # Delete extra messages if final text is shorter than what was sent
            for i in range(len(chunks), len(self._messages)):
                try:
                    await self._messages[i].delete()
                except discord.HTTPException:
                    pass
        else:
            # No messages sent during streaming, send everything now
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
