"""High-level skill management."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from moonshine.moonshine_constants import MoonshinePaths
from moonshine.storage.skill_store import SkillDefinition, SkillStore
from moonshine.utils import shorten


NEURAL_NETWORK_AGENT_SLUG = "neural-network-functions-researcher"
NEURAL_NETWORK_SKILL_SUFFIX = "-neural-network-functions"


class SkillManager(object):
    """Wrap the markdown-backed skill store with prompt-oriented helpers."""

    def __init__(self, paths: MoonshinePaths):
        self.paths = paths
        self.store = SkillStore(paths)
        from moonshine.skills.skill_manage import SkillFileManager

        self.file_manager = SkillFileManager(paths)

    def list_skills(self) -> Dict[str, List[Dict[str, str]]]:
        """Return the lightweight skill registry."""
        return self.store.list_skills()

    def list_skill_definitions(self) -> Dict[str, List[SkillDefinition]]:
        """Return full skill definitions."""
        return self.store.list_skill_definitions()

    def get_skill(self, slug: str) -> Optional[SkillDefinition]:
        """Return a skill definition by slug."""
        return self.store.get_skill(slug)

    def record_load(self, slug: str, session_id: str = "", project_slug: str = "") -> None:
        """Record on-demand skill loading for traceability."""
        self.file_manager.record_load(slug=slug, session_id=session_id, project_slug=project_slug)

    def create_skill(
        self,
        *,
        slug: str,
        title: str,
        description: str,
        body: str = "",
        category: str = "installed",
        tags: Optional[List[str]] = None,
        overwrite: bool = False,
        summary: object = None,
        execution_steps: object = None,
        tool_calls: object = None,
        file_references: object = None,
        compatibility: str = "",
        allowed_tools: object = None,
        purpose: object = None,
        when_to_use: object = None,
        inputs: object = None,
        workflow: object = None,
        checklist: object = None,
        output_contract: object = None,
        examples: object = None,
        notes: object = None,
    ) -> Dict[str, object]:
        """Create an installed skill file."""
        return self.file_manager.create_skill(
            slug=slug,
            title=title,
            description=description,
            body=body,
            category=category,
            tags=tags,
            overwrite=overwrite,
            summary=summary,
            execution_steps=execution_steps,
            tool_calls=tool_calls,
            file_references=file_references,
            compatibility=compatibility,
            allowed_tools=allowed_tools,
            purpose=purpose,
            when_to_use=when_to_use,
            inputs=inputs,
            workflow=workflow,
            checklist=checklist,
            output_contract=output_contract,
            examples=examples,
            notes=notes,
        )

    def patch_skill(self, *, slug: str, old_text: str, new_text: str, replace_all: bool = False) -> Dict[str, object]:
        """Patch an installed skill file."""
        return self.file_manager.patch_skill(
            slug=slug,
            old_text=old_text,
            new_text=new_text,
            replace_all=replace_all,
        )

    def edit_skill(
        self,
        *,
        slug: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        body: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        summary: object = None,
        execution_steps: object = None,
        tool_calls: object = None,
        file_references: object = None,
        compatibility: Optional[str] = None,
        allowed_tools: object = None,
        purpose: object = None,
        when_to_use: object = None,
        inputs: object = None,
        workflow: object = None,
        checklist: object = None,
        output_contract: object = None,
        examples: object = None,
        notes: object = None,
    ) -> Dict[str, object]:
        """Edit an installed skill file."""
        return self.file_manager.edit_skill(
            slug=slug,
            title=title,
            description=description,
            body=body,
            category=category,
            tags=tags,
            summary=summary,
            execution_steps=execution_steps,
            tool_calls=tool_calls,
            file_references=file_references,
            compatibility=compatibility,
            allowed_tools=allowed_tools,
            purpose=purpose,
            when_to_use=when_to_use,
            inputs=inputs,
            workflow=workflow,
            checklist=checklist,
            output_contract=output_contract,
            examples=examples,
            notes=notes,
        )

    def delete_skill(self, *, slug: str) -> Dict[str, object]:
        """Delete an installed skill."""
        return self.file_manager.delete_skill(slug=slug)

    def write_skill_file(self, *, slug: str, relative_path: str, content: str) -> Dict[str, object]:
        """Write an auxiliary file into a skill directory."""
        return self.file_manager.write_skill_file(slug=slug, relative_path=relative_path, content=content)

    def delete_skill_file(self, *, slug: str, relative_path: str) -> Dict[str, object]:
        """Delete an auxiliary file from a skill directory."""
        return self.file_manager.delete_skill_file(slug=slug, relative_path=relative_path)

    def _agent_filtered_items(
        self,
        items: Sequence[SkillDefinition],
        *,
        all_items: Sequence[SkillDefinition],
        agent_slug: str = "",
    ) -> List[SkillDefinition]:
        """Return skills appropriate for the active agent profile."""
        resolved_agent = str(agent_slug or "").strip()
        domain_suffix = NEURAL_NETWORK_SKILL_SUFFIX
        domain_base_slugs = {
            item.slug[: -len(domain_suffix)]
            for item in all_items
            if item.slug.endswith(domain_suffix)
        }
        if resolved_agent == NEURAL_NETWORK_AGENT_SLUG:
            return [
                item
                for item in items
                if item.slug.endswith(domain_suffix) or item.slug not in domain_base_slugs
            ]
        return [item for item in items if not item.slug.endswith(domain_suffix)]

    def list_exposed_skill_definitions(
        self,
        *,
        include: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
        agent_slug: str = "",
    ) -> List[SkillDefinition]:
        """Return skill definitions visible to the active agent and exposure settings."""
        collected = self.store.list_skill_definitions()
        all_items = [
            item
            for item in (collected["builtin"] + collected["installed"])
            if str(item.category).strip().lower() != "internal"
        ]
        include_set = {str(item).strip() for item in list(include or []) if str(item).strip()}
        exclude_set = {str(item).strip() for item in list(exclude or []) if str(item).strip()}
        items = [
            item
            for item in all_items
            if (not include_set or item.slug in include_set)
            and item.slug not in exclude_set
        ]
        return self._agent_filtered_items(items, all_items=all_items, agent_slug=agent_slug)

    def is_skill_exposed(
        self,
        slug: str,
        *,
        include: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
        agent_slug: str = "",
    ) -> bool:
        """Return whether a skill may be surfaced or loaded for the active agent."""
        target = str(slug or "").strip()
        return any(
            item.slug == target
            for item in self.list_exposed_skill_definitions(
                include=include,
                exclude=exclude,
                agent_slug=agent_slug,
            )
        )

    def build_prompt_index(
        self,
        limit: int = 64,
        *,
        include: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
        agent_slug: str = "",
    ) -> str:
        """Build a compact skill summary for the system prompt."""
        items = self.list_exposed_skill_definitions(
            include=include,
            exclude=exclude,
            agent_slug=agent_slug,
        )
        if not items:
            return ""
        effective_limit = max(0, int(limit or 0))
        shown_items = items if effective_limit <= 0 else items[:effective_limit]
        lines = ["Available skills (short descriptions and usage guidance; load and use a matching skill for nontrivial matching tasks):"]
        for item in shown_items:
            usage = self._usage_hint(item)
            suffix = " Usage: %s" % usage if usage else ""
            lines.append("- %s: %s%s" % (item.slug, item.description or item.title, suffix))
        if effective_limit > 0 and len(items) > effective_limit:
            lines.append("- ... plus %s more skills" % (len(items) - effective_limit))
        lines.append("If a current nontrivial task matches a skill's guidance, call load_skill_definition for that skill before relying on its detailed workflow.")
        return "\n".join(lines)

    def _usage_hint(self, definition: SkillDefinition) -> str:
        """Extract a compact usage hint from skill sections or body text."""
        explicit_hint = str(getattr(definition, "usage_hint", "") or "").strip()
        if explicit_hint:
            lines = []
            for raw_line in explicit_hint.splitlines():
                line = raw_line.strip().lstrip("-0123456789. ")
                if line:
                    lines.append(line)
            if lines:
                return " ".join(lines)
            return explicit_hint
        sections = dict(definition.sections or {})
        for key in ("When To Use", "Execution Steps", "Workflow", "Purpose"):
            text = str(sections.get(key) or "").strip()
            if text:
                for raw_line in text.splitlines():
                    line = raw_line.strip().lstrip("-0123456789. ")
                    if line:
                        return shorten(line, 180)
                return shorten(text, 180)
        for raw_line in str(definition.body or "").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#"):
                return shorten(line, 180)
        return ""
