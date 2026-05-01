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
    "Bash": ("💻", "command"),
    "Read": ("📖", "file_path"),
    "Write": ("📝", "file_path"),
    "Edit": ("✏️", "file_path"),
    "Glob": ("🔍", "pattern"),
    "Grep": ("🔎", "pattern"),
    "Agent": ("🤖", "description"),
    "WebSearch": ("🌐", "query"),
    "WebFetch": ("🌐", "url"),
    "ToolSearch": ("🔧", "query"),
    "TodoWrite": ("📋", "todos"),
    "NotebookEdit": ("📓", "file_path"),
}

# Keys to try in order when extracting a detail from tool input
_DETAIL_KEYS = ["query", "command", "file_path", "pattern", "url", "description",
                "prompt", "message", "name", "path", "text", "args"]


def _parse_mcp_tool_name(raw_name: str) -> tuple[str, str]:
    """Parse MCP tool names like 'mcp__tavily__tavily_search' -> ('tavily', 'tavily_search')"""
    if raw_name.startswith("mcp__"):
        parts = raw_name.split("__", 2)
        if len(parts) == 3:
            return parts[1], parts[2]
    return "", raw_name


def _extract_detail(tool_input: dict | None, preferred_key: str = "") -> str:
    if not tool_input or not isinstance(tool_input, dict):
        return ""
    if preferred_key and preferred_key in tool_input:
        val = tool_input[preferred_key]
        if isinstance(val, str):
            return val
    for key in _DETAIL_KEYS:
        if key in tool_input:
            val = tool_input[key]
            if isinstance(val, str) and val.strip():
                return val
    # Last resort: first string value
    for val in tool_input.values():
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _truncate(text: str, limit: int = 100) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) > limit:
        return text[:limit - 3] + "..."
    return text


def format_tool_status(tool_name: str, tool_input: dict | None = None) -> str:
    # Handle MCP tools
    mcp_server, clean_name = _parse_mcp_tool_name(tool_name)

    if mcp_server:
        emoji = "🔌"
        detail = _extract_detail(tool_input)
        if detail:
            return f"{emoji} **{mcp_server}/{clean_name}**: `{_truncate(detail)}`"
        return f"{emoji} **{mcp_server}/{clean_name}**"

    # Built-in tools
    emoji, preferred_key = _TOOL_LABELS.get(clean_name, ("⚡", ""))
    detail = _extract_detail(tool_input, preferred_key)

    if detail:
        return f"{emoji} **{clean_name}**: `{_truncate(detail)}`"
    return f"{emoji} **{clean_name}**"
