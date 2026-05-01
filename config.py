from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class BotConfig:
    # Discord
    discord_token: str = ""
    allowed_user_ids: set[str] = field(default_factory=set)
    allowed_role_ids: set[int] = field(default_factory=set)
    allowed_channels: set[str] = field(default_factory=set)
    require_mention: bool = True
    reactions_enabled: bool = True

    # Claude Code
    claude_binary: str = "claude"
    claude_working_dir: str = ""
    claude_model: str = ""
    claude_permission_mode: str = "default"
    claude_max_budget_usd: float = 0.0
    claude_allowed_tools: str = ""
    claude_system_prompt: str = ""

    # Session
    session_timeout_minutes: int = 60
    session_max_concurrent: int = 5

    # Streaming
    edit_interval: float = 0.6
    buffer_threshold: int = 8
    cursor: str = " |"

    @classmethod
    def from_env(cls) -> BotConfig:
        from dotenv import load_dotenv
        load_dotenv()

        def _parse_set(env_key: str) -> set[str]:
            raw = os.getenv(env_key, "").strip()
            if not raw:
                return set()
            return {x.strip() for x in raw.split(",") if x.strip()}

        def _parse_int_set(env_key: str) -> set[int]:
            raw = os.getenv(env_key, "").strip()
            if not raw:
                return set()
            return {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}

        def _bool(env_key: str, default: bool) -> bool:
            raw = os.getenv(env_key, "").strip().lower()
            if not raw:
                return default
            return raw in ("true", "1", "yes", "on")

        return cls(
            discord_token=os.getenv("DISCORD_TOKEN", ""),
            allowed_user_ids=_parse_set("DISCORD_ALLOWED_USERS"),
            allowed_role_ids=_parse_int_set("DISCORD_ALLOWED_ROLES"),
            allowed_channels=_parse_set("DISCORD_ALLOWED_CHANNELS"),
            require_mention=_bool("DISCORD_REQUIRE_MENTION", True),
            reactions_enabled=_bool("DISCORD_REACTIONS", True),
            claude_binary=os.getenv("CLAUDE_BINARY", "claude"),
            claude_working_dir=os.getenv("CLAUDE_WORKING_DIR", os.getcwd()),
            claude_model=os.getenv("CLAUDE_MODEL", ""),
            claude_permission_mode=os.getenv("CLAUDE_PERMISSION_MODE", "default"),
            claude_max_budget_usd=float(os.getenv("CLAUDE_MAX_BUDGET_USD", "0")),
            claude_allowed_tools=os.getenv("CLAUDE_ALLOWED_TOOLS", ""),
            claude_system_prompt=os.getenv("CLAUDE_SYSTEM_PROMPT", ""),
            session_timeout_minutes=int(os.getenv("SESSION_TIMEOUT_MINUTES", "60")),
            session_max_concurrent=int(os.getenv("SESSION_MAX_CONCURRENT", "5")),
            edit_interval=float(os.getenv("STREAM_EDIT_INTERVAL", "1.5")),
            buffer_threshold=int(os.getenv("STREAM_BUFFER_THRESHOLD", "40")),
            cursor=os.getenv("STREAM_CURSOR", " |"),
        )
