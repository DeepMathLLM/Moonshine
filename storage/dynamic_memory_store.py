"""Dynamic memory store with markdown-backed notes and an index."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from moonshine.moonshine_constants import MoonshinePaths, alias_from_relative_path, resolve_memory_spec
from moonshine.utils import append_jsonl, atomic_write, overlap_score, read_text, shorten, utc_now


SECTION_RE = re.compile(r"(?ms)^## ([^\n]+)\n(.*?)(?=^## |\Z)")
SECTION_METADATA_RE = re.compile(r"\A<!--\s*(\{.*?\})\s*-->\s*", re.DOTALL)


@dataclass
class DynamicMemoryEntry:
    """Structured dynamic memory entry."""

    slug: str
    title: str
    summary: str
    body: str
    source: str
    created_at: str
    updated_at: str
    file_alias: str
    relative_path: str
    project_slug: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    group: str = ""
    label: str = ""
    source_session_id: str = ""
    source_message_role: str = ""
    source_excerpt: str = ""

    def to_markdown(self) -> str:
        """Render this entry as a markdown section."""
        metadata = {
            "title": self.title.strip(),
            "summary": self.summary.strip(),
            "source": self.source.strip(),
            "created_at": self.created_at.strip(),
            "updated_at": self.updated_at.strip(),
            "project_slug": (self.project_slug or "").strip(),
            "tags": list(self.tags),
            "source_session_id": self.source_session_id.strip(),
            "source_message_role": self.source_message_role.strip(),
            "source_excerpt": self.source_excerpt.strip(),
        }
        return (
            "## {slug}\n"
            "<!--\n"
            "{metadata}\n"
            "-->\n\n"
            "{body}\n"
        ).format(
            slug=self.slug.strip(),
            metadata=json.dumps(metadata, indent=2, ensure_ascii=False),
            body=self.body.strip(),
        )


class DynamicMemoryStore(object):
    """Manage layer-2 dynamic memory files."""

    def __init__(self, paths: MoonshinePaths):
        self.paths = paths

    def resolve_path(self, alias: str, project_slug: Optional[str] = None) -> Path:
        """Resolve a memory alias to a runtime path."""
        return self.paths.home / resolve_memory_spec(alias, project_slug)["relative_path"]

    def list_memory_files(self, project_slug: Optional[str] = None) -> List[Dict[str, str]]:
        """List editable memory files."""
        aliases = [
            "user-profile",
            "user-preferences",
            "feedback-corrections",
            "feedback-explicit",
            "feedback-success",
            "project-active",
            "reference-papers",
            "reference-theorems",
            "reference-resources",
        ]
        if project_slug:
            aliases.extend(["project-context", "project-lemmas"])
        items = []
        for alias in aliases:
            scope_slug = project_slug if alias.startswith("project-") else None
            spec = resolve_memory_spec(alias, scope_slug)
            items.append(
                {
                    "alias": alias,
                    "label": spec["label"],
                    "path": str(self.paths.home / spec["relative_path"]),
                }
            )
        return items

    def _split_block(self, block: str) -> Dict[str, object]:
        meta: Dict[str, str] = {}
        body_lines: List[str] = []
        in_body = False
        for line in block.splitlines():
            if not in_body and not line.strip():
                in_body = True
                continue
            if not in_body and ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip()
            else:
                in_body = True
                body_lines.append(line)
        meta["body"] = "\n".join(body_lines).strip()
        return meta

    def parse_entries(self, path: Path) -> List[DynamicMemoryEntry]:
        """Parse all entries inside a memory file."""
        relative_path = str(path.relative_to(self.paths.home)).replace("\\", "/")
        mapping = alias_from_relative_path(relative_path)
        if not mapping.get("alias"):
            return []

        entries = []
        for match in SECTION_RE.finditer(read_text(path)):
            slug = match.group(1).strip()
            block = match.group(2).strip("\n")
            comment_match = SECTION_METADATA_RE.match(block)
            if comment_match:
                try:
                    metadata = json.loads(comment_match.group(1))
                except ValueError:
                    metadata = {}
                body = block[comment_match.end() :].strip()
                meta = {
                    "Title": metadata.get("title", slug),
                    "Summary": metadata.get("summary", ""),
                    "Source": metadata.get("source", "manual"),
                    "Created": metadata.get("created_at", metadata.get("updated_at", "")),
                    "Updated": metadata.get("updated_at", ""),
                    "Project": metadata.get("project_slug", ""),
                    "Tags": ", ".join(list(metadata.get("tags") or [])),
                    "Session": metadata.get("source_session_id", ""),
                    "MessageRole": metadata.get("source_message_role", ""),
                    "Evidence": metadata.get("source_excerpt", ""),
                    "body": body,
                }
            else:
                meta = self._split_block(block)
            entries.append(
                DynamicMemoryEntry(
                    slug=slug,
                    title=str(meta.get("Title", slug)),
                    summary=str(meta.get("Summary", "")),
                    body=str(meta.get("body", "")),
                    source=str(meta.get("Source", "manual")),
                    created_at=str(meta.get("Created", meta.get("Updated", ""))),
                    updated_at=str(meta.get("Updated", "")),
                    file_alias=str(mapping["alias"]),
                    relative_path=relative_path,
                    project_slug=str(meta.get("Project", "")) or mapping.get("project_slug"),
                    tags=[item.strip() for item in str(meta.get("Tags", "")).split(",") if item.strip()],
                    group=str(mapping.get("group") or ""),
                    label=str(mapping.get("label") or ""),
                    source_session_id=str(meta.get("Session", "")),
                    source_message_role=str(meta.get("MessageRole", "")),
                    source_excerpt=str(meta.get("Evidence", "")),
                )
            )
        return entries

    def list_entries(self, project_slug: Optional[str] = None) -> List[DynamicMemoryEntry]:
        """Return all dynamic memory entries."""
        entries: List[DynamicMemoryEntry] = []
        for path in sorted(self.paths.memory_dir.rglob("*.md")):
            if path.name in {"MEMORY.md", "AGENT.md"}:
                continue
            entries.extend(self.parse_entries(path))
        for path in sorted(self.paths.projects_dir.rglob("*.md")):
            if "memory" not in path.parts:
                continue
            entries.extend(self.parse_entries(path))

        if project_slug:
            entries = [item for item in entries if item.project_slug in (None, "", project_slug)]
        entries.sort(key=lambda item: (item.updated_at, item.title), reverse=True)
        return entries

    def search(self, query: str, project_slug: Optional[str] = None, limit: int = 5) -> List[DynamicMemoryEntry]:
        """Search dynamic memory entries."""
        entries = self.list_entries(project_slug=project_slug)
        if not query.strip():
            return entries[:limit]

        ranked = []
        for entry in entries:
            blob = " ".join([entry.title, entry.summary, entry.body, " ".join(entry.tags)])
            score = overlap_score(query, blob)
            if project_slug and entry.project_slug == project_slug:
                score += 0.1
            if score > 0:
                ranked.append((score, entry))
        ranked.sort(key=lambda pair: (pair[0], pair[1].updated_at), reverse=True)
        return [pair[1] for pair in ranked[:limit]]

    def get_entry(self, slug: str) -> Optional[DynamicMemoryEntry]:
        """Return an entry by slug."""
        for entry in self.list_entries():
            if entry.slug == slug:
                return entry
        return None

    def write_entry(self, entry: DynamicMemoryEntry) -> DynamicMemoryEntry:
        """Insert or update an entry."""
        target = self.paths.home / entry.relative_path
        current = read_text(target, default="")
        existing_entry = None
        for item in self.parse_entries(target):
            if item.slug == entry.slug:
                existing_entry = item
                break
        if existing_entry is not None:
            entry.created_at = existing_entry.created_at or entry.created_at
            if not entry.source_session_id:
                entry.source_session_id = existing_entry.source_session_id
            if not entry.source_message_role:
                entry.source_message_role = existing_entry.source_message_role
            if not entry.source_excerpt:
                entry.source_excerpt = existing_entry.source_excerpt
        section = entry.to_markdown().rstrip() + "\n\n"
        pattern = re.compile(r"(?ms)^## %s\n.*?(?=^## |\Z)" % re.escape(entry.slug))
        if pattern.search(current):
            updated = pattern.sub(lambda _match: section, current)
        else:
            updated = current.rstrip() + ("\n\n" if current.strip() else "") + section
        atomic_write(target, updated.strip() + "\n")

        append_jsonl(
            self.paths.memory_audit_log,
            {
                "event": "write_entry",
                "slug": entry.slug,
                "title": entry.title,
                "file_alias": entry.file_alias,
                "relative_path": entry.relative_path,
                "project_slug": entry.project_slug,
                "source": entry.source,
                "created_at": entry.created_at,
                "updated_at": entry.updated_at,
                "source_session_id": entry.source_session_id,
                "source_message_role": entry.source_message_role,
            },
        )
        return entry

    def rebuild_index(self) -> str:
        """Rebuild MEMORY.md from the current entry set."""
        grouped: Dict[str, List[DynamicMemoryEntry]] = {}
        for entry in self.list_entries():
            grouped.setdefault(entry.group or "Other", []).append(entry)

        lines = ["# Moonshine Memory Index", ""]
        for group in ["User Profile", "Behavior Feedback", "Project Tracking", "References", "Other"]:
            if not grouped.get(group):
                continue
            lines.append("## %s" % group)
            for entry in grouped[group]:
                lines.append(
                    "- [{title}]({path}#{slug}) - {summary} [source: {source}{session}]".format(
                        title=entry.title,
                        path=entry.relative_path,
                        slug=entry.slug,
                        summary=shorten(entry.summary or entry.body, 96),
                        source=entry.source or "unknown",
                        session=(", session: %s" % entry.source_session_id) if entry.source_session_id else "",
                    )
                )
            lines.append("")

        rendered = "\n".join(lines).rstrip() + "\n"
        atomic_write(self.paths.memory_index_file, rendered)
        return rendered

    def read_file(self, alias: str, project_slug: Optional[str] = None) -> str:
        """Read a memory file as raw markdown."""
        return read_text(self.resolve_path(alias, project_slug))

    def make_entry(
        self,
        *,
        alias: str,
        slug: str,
        title: str,
        summary: str,
        body: str,
        source: str,
        project_slug: Optional[str] = None,
        tags: Optional[List[str]] = None,
        source_session_id: str = "",
        source_message_role: str = "",
        source_excerpt: str = "",
    ) -> DynamicMemoryEntry:
        """Construct a dynamic entry from a memory alias."""
        spec = resolve_memory_spec(alias, project_slug if alias.startswith("project-") else None)
        timestamp = utc_now()
        return DynamicMemoryEntry(
            slug=slug,
            title=title,
            summary=summary,
            body=body,
            source=source,
            created_at=timestamp,
            updated_at=timestamp,
            file_alias=alias,
            relative_path=spec["relative_path"],
            project_slug=project_slug,
            tags=list(tags or []),
            group=spec["group"],
            label=spec["label"],
            source_session_id=source_session_id,
            source_message_role=source_message_role,
            source_excerpt=source_excerpt,
        )
