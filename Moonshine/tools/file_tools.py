"""File-oriented tools for Moonshine."""

from __future__ import annotations

from pathlib import Path

from moonshine.tools.path_utils import strip_active_project_prefix
from moonshine.utils import read_text


GLOBAL_RUNTIME_PREFIXES = ("knowledge/", "sessions/")


def read_runtime_file(runtime: dict, relative_path: str) -> dict:
    """Read a file beneath the active project, or Moonshine home without a project."""
    paths = runtime["paths"]
    project_slug = str(runtime.get("project_slug") or "").strip()
    raw_input = str(relative_path or "").strip()
    normalized_input = raw_input.replace("\\", "/")
    while normalized_input.startswith("./"):
        normalized_input = normalized_input[2:]
    root = paths.home if normalized_input.lower().startswith(GLOBAL_RUNTIME_PREFIXES) else (paths.project_dir(project_slug) if project_slug else paths.home)
    raw_path = strip_active_project_prefix(paths, project_slug, root, raw_input)
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        target = candidate.resolve()
    else:
        target = (root / candidate).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise ValueError("path must stay inside the active project or Moonshine home")
    return {"path": str(target), "root": str(resolved_root), "relative_path": str(target.relative_to(resolved_root)), "content": read_text(target)}
