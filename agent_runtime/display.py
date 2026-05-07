"""Simple CLI display helpers."""

from __future__ import annotations


def render_banner(mode: str, project_slug: str, session_id: str, agent_slug: str = "") -> str:
    """Render a small shell banner."""
    if str(agent_slug or "").strip():
        return "Moonshine shell | mode=%s | project=%s | agent=%s | session=%s" % (mode, project_slug, agent_slug, session_id)
    return "Moonshine shell | mode=%s | project=%s | session=%s" % (mode, project_slug, session_id)


def render_tool_result(name: str, result: object) -> str:
    """Render a concise tool result summary."""
    return "[tool:%s] %s" % (name, result)
