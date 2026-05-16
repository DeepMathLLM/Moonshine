"""Shared path normalization helpers for runtime tools."""

from __future__ import annotations

from pathlib import Path


def strip_active_project_prefix(paths, project_slug: str, root: Path, path_value: object) -> str:
    """Strip legacy projects/<active-project>/ prefixes when root is that project."""
    raw = str(path_value or ".").strip() or "."
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        return raw
    slug = str(project_slug or "").strip()
    if not slug:
        return raw
    try:
        if root.resolve() != paths.project_dir(slug).resolve():
            return raw
    except Exception:
        return raw
    normalized = raw.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    prefix = "projects/%s/" % slug
    project_marker = "projects/%s" % slug
    while normalized.lower().startswith(prefix.lower()):
        normalized = normalized[len(prefix) :]
    if normalized.lower() == project_marker.lower():
        normalized = "."
    return normalized or "."
