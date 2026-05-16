"""Memory tools for Moonshine."""

from __future__ import annotations


def memory_overview(runtime: dict, project_slug: str = "") -> dict:
    """Return a summary of memory files for a scope."""
    resolved_project = str(project_slug or runtime.get("project_slug") or "").strip()
    if str(runtime.get("mode") or "").strip() == "research" and resolved_project:
        paths = runtime["paths"]
        return {
            "files": [
                {
                    "alias": "research-log-jsonl",
                    "label": "Research log JSONL",
                    "path": str(paths.project_research_log_file(resolved_project)),
                },
                {
                    "alias": "research-log-md",
                    "label": "Research log markdown",
                    "path": str(paths.project_research_log_markdown_file(resolved_project)),
                },
                {
                    "alias": "research-log-index",
                    "label": "Research log SQLite index",
                    "path": str(paths.project_research_log_index_file(resolved_project)),
                },
                {
                    "alias": "research-log-by-type",
                    "label": "Research log by-type markdown views",
                    "path": str(paths.project_research_log_by_type_dir(resolved_project)),
                },
            ],
            "project_slug": resolved_project,
            "mode": "research",
        }
    files = runtime["memory_manager"].dynamic_store.list_memory_files(project_slug=project_slug or None)
    return {"files": files}
