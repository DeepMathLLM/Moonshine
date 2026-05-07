"""On-demand context retrieval tools for Moonshine."""

from __future__ import annotations

from typing import List, Optional


def query_memory(
    runtime: dict,
    query: str,
    project_slug: str = "",
    all_projects: bool = False,
    types: Optional[List[str]] = None,
    channels: Optional[List[str]] = None,
    channel_mode: str = "search",
    limit_per_channel: int = 3,
    prefer_raw: bool = False,
) -> dict:
    """Retrieve and summarize relevant memory sources for the current turn."""
    manager = runtime["context_manager"]
    research_mode = str(runtime.get("mode", "") or "").strip().lower() == "research"
    return manager.query_memory(
        query=query,
        project_slug=(project_slug or str(runtime.get("project_slug", "") or "general")) if not all_projects else None,
        session_id=str(runtime.get("session_id", "") or ""),
        all_projects=bool(all_projects),
        types=list(types or []),
        channels=list(channels or []),
        channel_mode=str(channel_mode or "search"),
        limit_per_channel=int(limit_per_channel or 3),
        prefer_raw=bool(prefer_raw or research_mode),
    )
