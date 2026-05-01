from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from claude_runner import ClaudeGateway
from config import BotConfig
from security import SecurityChecker, ThreadParticipationTracker
from session_manager import SessionManager
from stream_consumer import DiscordStreamConsumer

logger = logging.getLogger(__name__)

# Discord slash command name rules: lowercase, 1-32 chars, only a-z 0-9 - _
_DISCORD_NAME_RE = re.compile(r"[^a-z0-9\-]")
_BOT_ONLY_COMMANDS = {"reset", "stop", "status", "sessions", "resume"}


def _to_discord_name(cc_name: str) -> str:
    name = cc_name.lower().replace(":", "-").replace("_", "-")
    name = _DISCORD_NAME_RE.sub("", name)
    return name[:32]


def _build_allowed_mentions() -> discord.AllowedMentions:
    return discord.AllowedMentions(
        everyone=False,
        roles=False,
        users=True,
        replied_user=True,
    )


async def _probe_slash_commands(claude_binary: str, working_dir: str) -> list[str]:
    """Run claude once to extract slash_commands from the init event."""
    cmd = [claude_binary, "-p", "--output-format", "stream-json", "--verbose", "hi"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=working_dir,
        )
        assert proc.stdout
        commands_list: list[str] = []
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=30)
            if not line:
                break
            try:
                data = json.loads(line.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if data.get("type") == "system" and data.get("subtype") == "init":
                commands_list = data.get("slash_commands", [])
                break
        proc.kill()
        await proc.wait()
        return commands_list
    except Exception as e:
        logger.warning("Failed to probe Claude Code slash commands: %s", e)
        return []


class ClaudeCodeBot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.session_mgr = SessionManager(config)
        self.security = SecurityChecker(config)
        self._threads = ThreadParticipationTracker()

        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        intents.guild_messages = True

        self._bot = commands.Bot(
            command_prefix="!",
            intents=intents,
            allowed_mentions=_build_allowed_mentions(),
        )

        self._setup_events()
        self._setup_bot_commands()

    def _setup_events(self):
        bot = self._bot

        @bot.event
        async def on_ready():
            logger.info("Connected as %s (id=%s)", bot.user, bot.user.id if bot.user else "?")
            self.session_mgr.start_cleanup_loop()

            # Probe Claude Code for slash commands and register them dynamically
            cc_commands = await _probe_slash_commands(
                self.config.claude_binary, self.config.claude_working_dir
            )
            registered = self._register_cc_commands(cc_commands)
            logger.info("Registered %d Claude Code slash commands", registered)

            try:
                synced = await bot.tree.sync()
                logger.info("Synced %d Discord slash command(s) total", len(synced))
            except Exception as e:
                logger.warning("Slash command sync failed: %s", e)

        @bot.event
        async def on_message(message: discord.Message):
            if not bot.user:
                return
            if message.author == bot.user:
                return
            if message.author.bot:
                return
            if self.security.dedup.is_duplicate(str(message.id)):
                return

            is_dm = isinstance(message.channel, discord.DMChannel)
            channel_id = str(message.channel.id)

            if not is_dm:
                if not self.security.is_channel_allowed(channel_id):
                    return

                mentioned = bot.user in message.mentions
                in_tracked = channel_id in self._threads

                if self.config.require_mention and not mentioned and not in_tracked:
                    return

            if not self.security.is_user_allowed(str(message.author.id), message.author):
                return

            if not self.security.rate_limiter.check(str(message.author.id)):
                await message.reply("Rate limited. Please wait a moment.", delete_after=5)
                return

            await self._handle_message(message)

    def _setup_bot_commands(self):
        """Register bot-specific commands (not forwarded to Claude Code)."""
        tree = self._bot.tree

        @tree.command(name="reset", description="Reset Claude Code session for this channel")
        async def cmd_reset(interaction: discord.Interaction):
            if not self._check_auth(interaction):
                await interaction.response.send_message("Not authorized.", ephemeral=True)
                return
            await self.session_mgr.reset(str(interaction.channel_id))
            await interaction.response.send_message("Session reset.")

        @tree.command(name="stop", description="Stop the running Claude Code process")
        async def cmd_stop(interaction: discord.Interaction):
            if not self._check_auth(interaction):
                await interaction.response.send_message("Not authorized.", ephemeral=True)
                return
            stopped = await self.session_mgr.stop(str(interaction.channel_id))
            if stopped:
                await interaction.response.send_message("Stopped.")
            else:
                await interaction.response.send_message("Nothing running.", ephemeral=True)

        @tree.command(name="status", description="Show Claude Code session status")
        async def cmd_status(interaction: discord.Interaction):
            status = self.session_mgr.get_status(str(interaction.channel_id))
            if not status["active"]:
                await interaction.response.send_message("No active session.", ephemeral=True)
                return
            lines = [
                f"**Connected:** {'yes' if status['connected'] else 'no'}",
                f"**Messages:** {status['messages']}",
                f"**Cost:** ${status['cost_usd']}",
                f"**Running:** {'yes' if status['running'] else 'no'}",
                f"**Idle:** {status['idle_minutes']} min",
            ]
            await interaction.response.send_message("\n".join(lines), ephemeral=True)

        @tree.command(name="sessions", description="List recent Claude Code sessions")
        @app_commands.describe(limit="Number of sessions to show (default 10)")
        async def cmd_sessions(interaction: discord.Interaction, limit: int = 10):
            if not self._check_auth(interaction):
                await interaction.response.send_message("Not authorized.", ephemeral=True)
                return
            try:
                import datetime
                sessions = self.session_mgr.list_recent_sessions(limit=limit)
                if not sessions:
                    await interaction.response.send_message("No sessions found.", ephemeral=True)
                    return
                lines = ["**Recent sessions** (use `/resume <id>` to restore):\n```"]
                for s in sessions:
                    ts = datetime.datetime.fromtimestamp(s.last_modified / 1000).strftime("%m-%d %H:%M") if s.last_modified else "?"
                    title = s.custom_title or s.first_prompt or s.summary or "(untitled)"
                    if len(title) > 50:
                        title = title[:47] + "..."
                    lines.append(f"{s.session_id[:8]}  {ts}  {title}")
                lines.append("```")
                await interaction.response.send_message("\n".join(lines), ephemeral=True)
            except Exception as e:
                logger.exception("Failed to list sessions")
                await interaction.response.send_message(f"Error: {e}", ephemeral=True)

        @tree.command(name="resume", description="Resume a previous Claude Code session")
        @app_commands.describe(session_id="Session ID (first 8 chars or full UUID)")
        async def cmd_resume(interaction: discord.Interaction, session_id: str):
            if not self._check_auth(interaction):
                await interaction.response.send_message("Not authorized.", ephemeral=True)
                return
            try:
                full_id = session_id.strip()
                if len(full_id) < 36:
                    sessions = self.session_mgr.list_recent_sessions(limit=50)
                    match = [s for s in sessions if s.session_id.startswith(full_id)]
                    if not match:
                        await interaction.response.send_message(f"No session found matching `{full_id}`", ephemeral=True)
                        return
                    if len(match) > 1:
                        await interaction.response.send_message(f"Ambiguous: {len(match)} sessions match `{full_id}`. Use more characters.", ephemeral=True)
                        return
                    full_id = match[0].session_id

                await interaction.response.defer()
                await self.session_mgr.resume(str(interaction.channel_id), full_id)
                await interaction.followup.send(f"Resumed session `{full_id[:8]}...`")
            except Exception as e:
                logger.exception("Failed to resume session")
                if interaction.response.is_done():
                    await interaction.followup.send(f"Error: {e}")
                else:
                    await interaction.response.send_message(f"Error: {e}", ephemeral=True)

    def _register_cc_commands(self, cc_commands: list[str]) -> int:
        """Dynamically register Discord slash commands for all Claude Code commands."""
        tree = self._bot.tree
        count = 0

        for cc_name in cc_commands:
            discord_name = _to_discord_name(cc_name)
            if not discord_name or discord_name in _BOT_ONLY_COMMANDS:
                continue
            # Discord limits to 100 slash commands per bot
            if count >= 95:
                logger.warning("Approaching Discord slash command limit, skipping remaining")
                break
            try:
                self._add_cc_command(tree, discord_name, cc_name)
                count += 1
            except Exception as e:
                logger.warning("Failed to register /%s: %s", discord_name, e)

        return count

    def _add_cc_command(self, tree: app_commands.CommandTree, discord_name: str, cc_name: str):
        cc_cmd = f"/{cc_name}"
        bot_ref = self

        def make_callback(cmd: str):
            @app_commands.describe(args="Optional arguments")
            async def handler(interaction: discord.Interaction, args: str = ""):
                if not bot_ref._check_auth(interaction):
                    await interaction.response.send_message("Not authorized.", ephemeral=True)
                    return
                await interaction.response.defer()
                prompt = f"{cmd} {args}".strip()
                await bot_ref._run_claude(interaction.channel, prompt, interaction=interaction)
            return handler

        tree.command(name=discord_name, description=f"Claude Code: /{cc_name}")(make_callback(cc_cmd))

    def _check_auth(self, interaction: discord.Interaction) -> bool:
        return self.security.is_user_allowed(str(interaction.user.id), interaction.user)

    async def _handle_message(self, message: discord.Message) -> None:
        prompt = message.content
        if self._bot.user and self._bot.user.mentioned_in(message):
            prompt = prompt.replace(f"<@{self._bot.user.id}>", "").strip()
            prompt = prompt.replace(f"<@!{self._bot.user.id}>", "").strip()

        if not prompt:
            return

        reference = None
        try:
            reference = message.to_reference(fail_if_not_exists=False)
        except Exception:
            pass

        await self._run_claude(message.channel, prompt, reference=reference, message=message)

    async def _run_claude(
        self,
        channel: discord.abc.Messageable,
        prompt: str,
        reference: Optional[discord.MessageReference] = None,
        message: Optional[discord.Message] = None,
        interaction: Optional[discord.Interaction] = None,
    ) -> None:
        channel_id = str(getattr(channel, "id", "dm"))
        session = await self.session_mgr.get_or_create(channel_id)

        if session.running:
            text = "Still processing a previous message. Use `/stop` to cancel."
            if interaction:
                await interaction.followup.send(text, ephemeral=True)
            elif message:
                await message.reply(text, delete_after=10)
            return

        if self.session_mgr.active_count() >= self.config.session_max_concurrent:
            text = "Too many concurrent sessions. Please wait."
            if interaction:
                await interaction.followup.send(text, ephemeral=True)
            elif message:
                await message.reply(text, delete_after=10)
            return

        if message and self.config.reactions_enabled:
            try:
                await message.add_reaction("\U0001f440")
            except discord.HTTPException:
                pass

        session.running = True
        session.message_count += 1

        consumer = DiscordStreamConsumer(
            channel=channel,
            reference=reference,
            config=self.config,
            interaction=interaction,
        )

        is_error = False
        try:
            if interaction:
                result = await session.gateway.query(
                    prompt=prompt,
                    on_event=consumer.on_event,
                )
            else:
                async with channel.typing():
                    result = await session.gateway.query(
                        prompt=prompt,
                        on_event=consumer.on_event,
                    )
            session.total_cost_usd += result.cost_usd
            is_error = result.is_error

            if not consumer._current_message and not consumer._finalized:
                text = result.text or "(no output)"
                if len(text) > 1900:
                    text = text[:1900] + "..."
                if interaction:
                    await interaction.followup.send(text)
                else:
                    await channel.send(text, reference=reference)

        except Exception as e:
            logger.exception("Error running claude for channel %s", channel_id)
            is_error = True
            error_text = f"Error: {e}"
            if interaction:
                await interaction.followup.send(error_text)
            else:
                try:
                    await channel.send(error_text)
                except discord.HTTPException:
                    pass
        finally:
            session.running = False
            session.runner = None

        if not is_error:
            self._threads.mark(channel_id)

        if message and self.config.reactions_enabled:
            try:
                await message.remove_reaction("\U0001f440", self._bot.user)
                emoji = "❌" if is_error else "✅"
                await message.add_reaction(emoji)
            except discord.HTTPException:
                pass

    async def start(self):
        if not self.config.discord_token:
            raise ValueError("DISCORD_TOKEN not set")
        await self._bot.start(self.config.discord_token)

    async def close(self):
        await self._bot.close()
