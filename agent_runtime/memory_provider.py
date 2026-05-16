"""Memory provider abstractions for Moonshine."""

from __future__ import annotations

from abc import ABCMeta, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ContextBundle:
    """Aggregated context for response generation."""

    static_rules: str = ""
    core_config: str = ""
    user_profile: str = ""
    user_preferences: str = ""
    project_rules: str = ""
    memory_index: str = ""
    project_context_summary: str = ""
    retrieval_summary: str = ""
    dynamic_hits: List[object] = field(default_factory=list)
    session_hits: List[Dict[str, object]] = field(default_factory=list)
    knowledge_hits: List[Dict[str, object]] = field(default_factory=list)
    graph_hits: List[Dict[str, object]] = field(default_factory=list)
    recent_messages: List[Dict[str, object]] = field(default_factory=list)
    token_estimate: int = 0

    def _render_dynamic_hits(self) -> str:
        """Render dynamic memory hits with provenance."""
        lines = []
        for item in self.dynamic_hits:
            suffix = []
            if getattr(item, "source", ""):
                suffix.append("source=%s" % item.source)
            if getattr(item, "source_session_id", ""):
                suffix.append("session=%s" % item.source_session_id)
            meta = (" [%s]" % ", ".join(suffix)) if suffix else ""
            summary = item.summary or item.body
            if getattr(item, "source_excerpt", ""):
                summary = "%s | evidence: %s" % (summary, item.source_excerpt)
            lines.append("- %s%s: %s" % (item.title, meta, summary))
        return "\n".join(lines)

    def to_prompt_text(self) -> str:
        """Render context for prompts."""
        blocks: List[str] = []
        if self.static_rules.strip():
            blocks.append("Working rules:\n%s" % self.static_rules.strip())
        if self.user_profile.strip():
            blocks.append("User profile:\n%s" % self.user_profile.strip())
        if self.user_preferences.strip():
            blocks.append("User preferences:\n%s" % self.user_preferences.strip())
        if self.memory_index.strip():
            blocks.append("Standing memory index:\n%s" % self.memory_index.strip())
        if self.project_context_summary.strip():
            blocks.append("Project background:\n%s" % self.project_context_summary.strip())
        if self.project_rules.strip():
            blocks.append("Project rules:\n%s" % self.project_rules.strip())
        if self.retrieval_summary.strip():
            blocks.append("Recalled history summary:\n%s" % self.retrieval_summary.strip())
        if self.dynamic_hits:
            blocks.append("Relevant dynamic memory:\n%s" % self._render_dynamic_hits())
        if self.session_hits:
            blocks.append(
                "Relevant session history:\n%s"
                % "\n".join("- [%s] %s" % (item["role"], item["content"]) for item in self.session_hits)
            )
        if self.knowledge_hits:
            blocks.append(
                "Relevant knowledge:\n%s"
                % "\n".join("- %s: %s" % (item["title"], item["statement"]) for item in self.knowledge_hits)
            )
        if self.graph_hits:
            blocks.append(
                "Relevant graph memory:\n%s"
                % "\n".join("- %s" % (item.get("text") or item.get("title") or "") for item in self.graph_hits)
            )
        if self.recent_messages:
            blocks.append(
                "Recent messages:\n%s"
                % "\n".join("- [%s] %s" % (item["role"], item["content"]) for item in self.recent_messages)
            )
        if not blocks:
            return ""
        return (
            "<memory-context>\n"
            "Recalled context for continuity:\n\n"
            + "\n\n".join(blocks).strip()
            + "\n</memory-context>"
        )

    def to_display_text(self) -> str:
        """Render a human-readable context summary."""
        lines = [
            "Moonshine context summary",
            "",
            "Token estimate: %s" % self.token_estimate,
            "CLAUDE.md loaded: %s" % ("yes" if self.static_rules.strip() else "no"),
            "User profile loaded: %s" % ("yes" if self.user_profile.strip() else "no"),
            "User preferences loaded: %s" % ("yes" if self.user_preferences.strip() else "no"),
            "config.yaml loaded: %s" % ("yes" if self.core_config.strip() else "no"),
            "MEMORY.md loaded: %s" % ("yes" if self.memory_index.strip() else "no"),
            "Project context summary loaded: %s" % ("yes" if self.project_context_summary.strip() else "no"),
            "Project rules loaded: %s" % ("yes" if self.project_rules.strip() else "no"),
            "Dynamic memory hits: %s" % len(self.dynamic_hits),
            "Session hits: %s" % len(self.session_hits),
            "Knowledge hits: %s" % len(self.knowledge_hits),
        ]
        if self.dynamic_hits:
            lines.append("")
            lines.append("Dynamic memory")
            lines.extend(
                "- %s (%s) [%s]"
                % (item.title, item.slug, getattr(item, "source_session_id", "") or getattr(item, "source", ""))
                for item in self.dynamic_hits
            )
        if self.knowledge_hits:
            lines.append("")
            lines.append("Knowledge")
            lines.extend("- %s" % item["title"] for item in self.knowledge_hits)
        return "\n".join(lines)


class MemoryProvider(object, metaclass=ABCMeta):
    """Abstract provider for retrieval context."""

    @abstractmethod
    def prepare_context(self, query: str, project_slug: str, session_id: str) -> ContextBundle:
        """Prepare a retrieval bundle for a user query."""
        raise NotImplementedError
