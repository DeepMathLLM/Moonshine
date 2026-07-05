"""File-oriented tools for Moonshine."""

from __future__ import annotations

from pathlib import Path

from moonshine.moonshine_constants import packaged_builtin_skills_dir, packaged_tool_definitions_dir
from moonshine.tools.path_utils import strip_active_project_prefix
from moonshine.utils import read_text


GLOBAL_RUNTIME_PREFIXES = ("inputs/", "knowledge/", "sessions/")


def _runtime_exposure(runtime: dict) -> dict:
    """Return exposure settings from either a runtime dict or config object."""
    raw = (runtime or {}).get("exposure") or {}
    if isinstance(raw, dict):
        return raw
    return {
        "tools_include": list(getattr(raw, "tools_include", []) or []),
        "tools_exclude": list(getattr(raw, "tools_exclude", []) or []),
        "skills_include": list(getattr(raw, "skills_include", []) or []),
        "skills_exclude": list(getattr(raw, "skills_exclude", []) or []),
    }


def _is_relative_to(path: Path, parent: Path) -> bool:
    """Return True when path is parent or under parent."""
    try:
        resolved = path.resolve()
        resolved_parent = parent.resolve()
    except OSError:
        return False
    return resolved == resolved_parent or resolved_parent in resolved.parents


def _definition_read_error(runtime: dict, target: Path) -> str:
    """Return an exposure error for direct reads of hidden tool/skill definitions."""
    paths = runtime["paths"]
    exposure = _runtime_exposure(runtime)

    if _is_relative_to(target, packaged_tool_definitions_dir()):
        return "tool definition files must be loaded through load_tool_definition"
    if _is_relative_to(target, packaged_builtin_skills_dir()) and target.name.lower() == "skill.md":
        return "skill definition files must be loaded through load_skill_definition"

    tool_definitions_dir = paths.tool_definitions_dir
    tool_manager = runtime.get("tool_manager")
    all_tool_paths = set()
    visible_tool_paths = set()
    if tool_manager is not None:
        for item in tool_manager.list_tools(mode=""):
            source_path = str(getattr(item, "source_path", "") or "")
            if source_path:
                all_tool_paths.add(str(Path(source_path).resolve()))
        for item in tool_manager.list_tools(
            mode=str(runtime.get("mode") or ""),
            include=list(exposure.get("tools_include") or []),
            exclude=list(exposure.get("tools_exclude") or []),
        ):
            source_path = str(getattr(item, "source_path", "") or "")
            if source_path:
                visible_tool_paths.add(str(Path(source_path).resolve()))
    target_text = str(target.resolve())
    if target_text in all_tool_paths and target_text not in visible_tool_paths:
        return "tool definition is not exposed; use load_tool_definition for visible tools"
    if _is_relative_to(target, tool_definitions_dir):
        if target_text not in visible_tool_paths:
            return "tool definition is not exposed; use load_tool_definition for visible tools"

    skills_dir = paths.skills_dir
    skill_manager = runtime.get("skill_manager")
    all_skill_paths = set()
    visible_skill_paths = set()
    if skill_manager is not None:
        for group in skill_manager.list_skill_definitions().values():
            for item in group:
                source_path = str(getattr(item, "path", "") or "")
                if source_path:
                    all_skill_paths.add(str(Path(source_path).resolve()))
        for item in skill_manager.list_exposed_skill_definitions(
            include=list(exposure.get("skills_include") or []),
            exclude=list(exposure.get("skills_exclude") or []),
            agent_slug=str(runtime.get("agent_slug") or ""),
        ):
            source_path = str(getattr(item, "path", "") or "")
            if source_path:
                visible_skill_paths.add(str(Path(source_path).resolve()))
    if target_text in all_skill_paths and target_text not in visible_skill_paths:
        return "skill definition is not exposed; use load_skill_definition for visible skills"
    if _is_relative_to(target, skills_dir) and target.name.lower() == "skill.md":
        if target_text not in visible_skill_paths:
            return "skill definition is not exposed; use load_skill_definition for visible skills"

    return ""


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
    definition_error = _definition_read_error(runtime, target)
    if definition_error:
        raise ValueError(definition_error)
    return {"path": str(target), "root": str(resolved_root), "relative_path": str(target.relative_to(resolved_root)), "content": read_text(target)}
