from __future__ import annotations

import re

DISCORD_MAX_LENGTH = 2000
SAFE_LIMIT = 1900

_ANSI_RE = re.compile(r"\x1B\[[0-9;]*[a-zA-Z]|\x1B\][^\x07]*\x07")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def truncate_message(text: str, limit: int = SAFE_LIMIT) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    in_code_block = False
    code_fence = ""

    while remaining:
        if len(remaining) <= limit:
            chunk = remaining
            remaining = ""
        else:
            split_at = remaining.rfind("\n", 0, limit)
            if split_at < limit // 2:
                split_at = limit
            chunk = remaining[:split_at]
            remaining = remaining[split_at:].lstrip("\n")

        if in_code_block:
            chunk = code_fence + "\n" + chunk

        fences = re.findall(r"^(`{3,})(\w*)", chunk, re.MULTILINE)
        open_count = 0
        last_fence = ""
        for fence_ticks, fence_lang in fences:
            if open_count == 0:
                open_count += 1
                last_fence = fence_ticks
                code_fence = fence_ticks + fence_lang
            else:
                open_count -= 1

        if open_count > 0:
            chunk += "\n" + last_fence
            in_code_block = True
        else:
            in_code_block = False
            code_fence = ""

        chunks.append(chunk)

    return chunks if chunks else [text[:limit]]


_TOOL_LABELS = {
    "Bash": ("Running", "command"),
    "Read": ("Reading", "file_path"),
    "Write": ("Writing", "file_path"),
    "Edit": ("Editing", "file_path"),
    "Glob": ("Searching files", "pattern"),
    "Grep": ("Searching for", "pattern"),
    "Agent": ("Delegating to", "description"),
    "WebSearch": ("Searching web", "query"),
    "WebFetch": ("Fetching", "url"),
}


def format_tool_status(tool_name: str, tool_input: dict | None = None) -> str:
    label, key = _TOOL_LABELS.get(tool_name, ("Using", ""))
    detail = ""
    if tool_input and key:
        detail = tool_input.get(key, "")
    if detail:
        if len(detail) > 80:
            detail = detail[:77] + "..."
        return f"*{label}:* `{detail}`"
    return f"*{label} {tool_name}*"
