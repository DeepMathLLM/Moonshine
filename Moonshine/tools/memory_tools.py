"""Memory tools for Moonshine."""

from __future__ import annotations


def memory_overview(runtime: dict, project_slug: str = "") -> dict:
    """Return a summary of memory files for a scope."""
    files = runtime["memory_manager"].dynamic_store.list_memory_files(project_slug=project_slug or None)
    return {"files": files}
