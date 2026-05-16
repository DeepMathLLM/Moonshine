"""Skill registry and provenance tracking for Moonshine."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from moonshine.moonshine_constants import MoonshinePaths
from moonshine.skills.skill_document import extract_skill_section_items, extract_skill_sections, parse_skill_document
from moonshine.utils import append_jsonl, read_json, utc_now, write_json


@dataclass
class SkillDefinition:
    """Markdown-backed skill metadata."""

    slug: str
    title: str
    description: str
    path: str
    source: str
    category: str = ""
    body: str = ""
    tags: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    compatibility: str = ""
    sections: Dict[str, str] = field(default_factory=dict)
    tool_calls: List[str] = field(default_factory=list)
    file_references: List[str] = field(default_factory=list)
    output_contract: str = ""
    usage_hint: str = ""

    def to_registry_item(self) -> Dict[str, str]:
        """Serialize the skill for CLI and registry output."""
        return {
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "path": self.path,
            "source": self.source,
            "category": self.category,
            "tags": ", ".join(self.tags),
            "allowed_tools": " ".join(self.allowed_tools),
            "compatibility": self.compatibility,
        }


class SkillStore(object):
    """Track builtin and installed skills from markdown skill files."""

    def __init__(self, paths: MoonshinePaths):
        self.paths = paths
        self._ensure_registry()

    def _read_registry_overrides(self) -> Dict[str, Dict[str, object]]:
        """Return provenance overrides from the lightweight registry file."""
        registry = read_json(self.paths.skills_registry_file, default={"builtin": [], "installed": []})
        overrides: Dict[str, Dict[str, object]] = {}
        for section in ("builtin", "installed"):
            for item in registry.get(section, []):
                slug = str(item.get("slug", "")).strip()
                if slug:
                    overrides[slug] = dict(item)
        return overrides

    def _scan_skill_directory(self, base_dir: Path, source: str) -> List[SkillDefinition]:
        """Scan a directory tree for SKILL.md files."""
        skills: List[SkillDefinition] = []
        if not base_dir.exists():
            return skills

        for skill_file in sorted(base_dir.rglob("SKILL.md")):
            metadata, body = parse_skill_document(skill_file.read_text(encoding="utf-8"))
            nested_metadata = dict(metadata.get("metadata") or {})
            slug = str(metadata.get("name") or metadata.get("slug") or nested_metadata.get("slug") or skill_file.parent.name).strip()
            title = str(nested_metadata.get("title") or metadata.get("title") or slug.replace("-", " ").title()).strip()
            description = str(metadata.get("description") or "").strip()
            category = str(nested_metadata.get("category") or metadata.get("category") or source).strip()
            tags = [
                item.strip()
                for item in str(nested_metadata.get("tags") or metadata.get("tags", "") or "").replace("[", "").replace("]", "").replace('"', "").replace("'", "").split(",")
                if item.strip()
            ]
            allowed_tools = [
                item.strip()
                for item in str(metadata.get("allowed-tools", "") or "").split()
                if item.strip()
            ]
            sections = extract_skill_sections(body)
            skills.append(
                SkillDefinition(
                    slug=slug,
                    title=title,
                    description=description,
                    path=str(skill_file),
                    source=source,
                    category=category,
                    body=body,
                    tags=tags,
                    allowed_tools=allowed_tools,
                    compatibility=str(metadata.get("compatibility", "") or "").strip(),
                    sections=sections,
                    tool_calls=extract_skill_section_items(body, "Tool Calls"),
                    file_references=extract_skill_section_items(body, "File References"),
                    output_contract=sections.get("Output Contract", ""),
                    usage_hint=str(
                        metadata.get("usage-hint")
                        or metadata.get("tool-usage-hint")
                        or nested_metadata.get("usage-hint")
                        or nested_metadata.get("tool-usage-hint")
                        or sections.get("Usage Hint", "")
                        or sections.get("Tool Usage Hint", "")
                        or ""
                    ).strip(),
                )
            )
        return skills

    def _collect_skills(self) -> Dict[str, List[SkillDefinition]]:
        """Collect builtin and installed skills from markdown files."""
        overrides = self._read_registry_overrides()
        builtin = self._scan_skill_directory(self.paths.builtin_skills_dir, "builtin")
        installed = self._scan_skill_directory(self.paths.installed_skills_dir, "installed")

        for item in builtin + installed:
            override = overrides.get(item.slug, {})
            if override.get("source"):
                item.source = str(override["source"])
            if override.get("description") and not item.description:
                item.description = str(override["description"])
            if override.get("title") and not item.title:
                item.title = str(override["title"])
        return {"builtin": builtin, "installed": installed}

    def _ensure_registry(self) -> None:
        """Refresh the lightweight skill registry from markdown skill files."""
        collected = self._collect_skills()
        write_json(
            self.paths.skills_registry_file,
            {
                "builtin": [item.to_registry_item() for item in collected["builtin"]],
                "installed": [item.to_registry_item() for item in collected["installed"]],
            },
        )

    def list_skills(self) -> Dict[str, List[Dict[str, str]]]:
        """Return the markdown-backed skill registry."""
        self._ensure_registry()
        return read_json(self.paths.skills_registry_file, default={"builtin": [], "installed": []})

    def list_skill_definitions(self) -> Dict[str, List[SkillDefinition]]:
        """Return skill definitions with full metadata."""
        return self._collect_skills()

    def get_skill(self, slug: str) -> Optional[SkillDefinition]:
        """Return a skill definition by slug."""
        for section in self._collect_skills().values():
            for item in section:
                if item.slug == slug:
                    return item
        return None

    def build_prompt_index(self, limit: int = 8) -> str:
        """Build a compact skill index for the system prompt."""
        collected = self._collect_skills()
        items = collected["builtin"] + collected["installed"]
        if not items:
            return ""
        lines = ["Available skills:"]
        for item in items[:limit]:
            lines.append("- %s: %s" % (item.slug, item.description or item.title))
        return "\n".join(lines)

    def register_installed_skill(self, slug: str, title: str, description: str, relative_path: str, source: str = "manual") -> None:
        """Register a custom installed skill in the lightweight registry."""
        registry = read_json(self.paths.skills_registry_file, default={"builtin": [], "installed": []})
        registry.setdefault("installed", [])
        registry["installed"] = [item for item in registry["installed"] if item.get("slug") != slug]
        registry["installed"].append(
            {
                "slug": slug,
                "title": title,
                "description": description,
                "path": relative_path,
                "source": source,
                "category": "installed",
            }
        )
        write_json(self.paths.skills_registry_file, registry)
        append_jsonl(
            self.paths.skills_audit_log,
            {
                "event": "register_installed_skill",
                "slug": slug,
                "title": title,
                "source": source,
                "created_at": utc_now(),
            },
        )
