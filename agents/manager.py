"""Markdown-backed agent profile management."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from moonshine.markdown_metadata import load_markdown_metadata
from moonshine.moonshine_constants import MoonshinePaths
from moonshine.utils import append_jsonl, read_json, utc_now, write_json


@dataclass
class AgentDefinition:
    """Markdown-backed agent profile metadata."""

    slug: str
    title: str
    description: str
    path: str
    source: str
    category: str = ""
    body: str = ""
    tags: List[str] = field(default_factory=list)
    is_default: bool = False

    def runtime_body(self) -> str:
        """Return the prompt-facing body, preferring an invisible marked block."""
        body = str(self.body or "").strip()
        if not body:
            return ""
        match = re.search(
            r"(?ms)<!--\s*moonshine:prompt-begin\s*-->\s*(.*?)\s*<!--\s*moonshine:prompt-end\s*-->",
            body,
        )
        if match:
            return str(match.group(1) or "").strip()
        match = re.search(r"(?ms)^## Runtime Prompt\s*\n(.*?)(?=^##\s+|\Z)", body)
        if not match:
            return body
        return str(match.group(1) or "").strip()

    def to_registry_item(self) -> Dict[str, object]:
        """Serialize the agent profile for registry output."""
        return {
            "slug": self.slug,
            "title": self.title,
            "description": self.description,
            "path": self.path,
            "source": self.source,
            "category": self.category,
            "tags": list(self.tags),
            "default": self.is_default,
        }


class AgentManager(object):
    """Track builtin and installed agent profiles from markdown files."""

    def __init__(self, paths: MoonshinePaths):
        self.paths = paths
        self._ensure_registry()

    def _read_registry_overrides(self) -> Dict[str, Dict[str, object]]:
        """Return agent override data from the lightweight registry file."""
        registry = read_json(self.paths.agents_registry_file, default={"builtin": [], "installed": []})
        overrides: Dict[str, Dict[str, object]] = {}
        for section in ("builtin", "installed"):
            for item in registry.get(section, []):
                slug = str(item.get("slug", "")).strip()
                if slug:
                    overrides[slug] = dict(item)
        return overrides

    def _scan_agent_directory(self, base_dir: Path, source: str) -> List[AgentDefinition]:
        """Scan a directory tree for AGENT.md files."""
        definitions: List[AgentDefinition] = []
        if not base_dir.exists():
            return definitions

        for agent_file in sorted(base_dir.rglob("AGENT.md")):
            metadata, body = load_markdown_metadata(agent_file)
            slug = str(metadata.get("slug") or agent_file.parent.name).strip()
            title = str(metadata.get("title") or slug.replace("-", " ").title()).strip()
            description = str(metadata.get("description") or "").strip()
            category = str(metadata.get("category") or source).strip()
            tags = [str(item).strip() for item in list(metadata.get("tags") or []) if str(item).strip()]
            definitions.append(
                AgentDefinition(
                    slug=slug,
                    title=title,
                    description=description,
                    path=str(agent_file),
                    source=source,
                    category=category,
                    body=body,
                    tags=tags,
                    is_default=bool(metadata.get("default", False)),
                )
            )
        return definitions

    def _collect_agents(self) -> Dict[str, List[AgentDefinition]]:
        """Collect builtin and installed agent profiles."""
        overrides = self._read_registry_overrides()
        builtin = self._scan_agent_directory(self.paths.builtin_agents_dir, "builtin")
        installed = self._scan_agent_directory(self.paths.installed_agents_dir, "installed")

        for item in builtin + installed:
            override = overrides.get(item.slug, {})
            if override.get("source"):
                item.source = str(override["source"])
            if override.get("description") and not item.description:
                item.description = str(override["description"])
            if override.get("title") and not item.title:
                item.title = str(override["title"])
            if "default" in override:
                item.is_default = bool(override["default"])
        return {"builtin": builtin, "installed": installed}

    def _ensure_registry(self) -> None:
        """Refresh the lightweight agent registry from markdown files."""
        collected = self._collect_agents()
        write_json(
            self.paths.agents_registry_file,
            {
                "builtin": [item.to_registry_item() for item in collected["builtin"]],
                "installed": [item.to_registry_item() for item in collected["installed"]],
            },
        )

    @property
    def default_slug(self) -> str:
        """Return the active default agent slug."""
        items = self.list_agent_definitions()
        for item in items:
            if item.is_default:
                return item.slug
        return items[0].slug if items else "moonshine-core"

    def list_agents(self) -> Dict[str, List[Dict[str, object]]]:
        """Return the lightweight agent registry."""
        self._ensure_registry()
        return read_json(self.paths.agents_registry_file, default={"builtin": [], "installed": []})

    def list_agent_definitions(self) -> List[AgentDefinition]:
        """Return all agent definitions."""
        collected = self._collect_agents()
        return collected["builtin"] + collected["installed"]

    def get_agent(self, slug: str = "") -> Optional[AgentDefinition]:
        """Return an agent profile by slug, or the default profile."""
        target_slug = (slug or self.default_slug).strip()
        for item in self.list_agent_definitions():
            if item.slug == target_slug:
                return item
        return None

    def build_prompt_summary(self, slug: str = "") -> str:
        """Build the compact agent summary injected into the system prompt."""
        definition = self.get_agent(slug)
        if definition is None:
            return ""
        lines = [
            "Active agent profile:",
            "- %s (%s): %s" % (definition.slug, definition.title, definition.description or definition.title),
            "Load the full profile with load_agent_definition if the summary is not enough for the current turn.",
        ]
        if definition.tags:
            lines.append("Tags: %s" % ", ".join(definition.tags))
        return "\n".join(lines)

    def register_installed_agent(self, slug: str, title: str, description: str, relative_path: str, source: str = "manual") -> None:
        """Register a custom installed agent in the lightweight registry."""
        registry = read_json(self.paths.agents_registry_file, default={"builtin": [], "installed": []})
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
                "default": False,
            }
        )
        write_json(self.paths.agents_registry_file, registry)
        append_jsonl(
            self.paths.agents_audit_log,
            {
                "event": "register_installed_agent",
                "slug": slug,
                "title": title,
                "source": source,
                "created_at": utc_now(),
            },
        )
