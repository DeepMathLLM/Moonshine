"""File-oriented tools for Moonshine."""

from __future__ import annotations

from pathlib import Path

from moonshine.tools.path_utils import strip_active_project_prefix
from moonshine.utils import read_text


GLOBAL_RUNTIME_PREFIXES = ("inputs/", "knowledge/", "sessions/")


def _is_home_relative_runtime_path(normalized_input: str, project_slug: str) -> bool:
    lowered = str(normalized_input or "").lower()
    if lowered.startswith(GLOBAL_RUNTIME_PREFIXES):
        return True
    if not lowered.startswith("projects/"):
        return False
    slug = str(project_slug or "").strip().lower()
    if not slug:
        return True
    active_prefix = "projects/%s/" % slug
    active_marker = "projects/%s" % slug
    return not (lowered.startswith(active_prefix) or lowered == active_marker)


def read_runtime_file(runtime: dict, relative_path: str) -> dict:
    """Read a file beneath the active project, or Moonshine home without a project."""
    paths = runtime["paths"]
    project_slug = str(runtime.get("project_slug") or "").strip()
    raw_input = str(relative_path or "").strip()
    normalized_input = raw_input.replace("\\", "/")
    while normalized_input.startswith("./"):
        normalized_input = normalized_input[2:]
    root = paths.home if _is_home_relative_runtime_path(normalized_input, project_slug) else (paths.project_dir(project_slug) if project_slug else paths.home)
    raw_path = strip_active_project_prefix(paths, project_slug, root, raw_input)
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        target = candidate.resolve()
        home = paths.home.resolve()
        if target == home or home in target.parents:
            root = paths.home
    else:
        target = (root / candidate).resolve()
    resolved_root = root.resolve()
    if target != resolved_root and resolved_root not in target.parents:
        raise ValueError("path must stay inside the active project or Moonshine home")
    return {"path": str(target), "root": str(resolved_root), "relative_path": str(target.relative_to(resolved_root)), "content": read_text(target)}
