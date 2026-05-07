"""Research-mode policy and automatic project resolution."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from moonshine.json_schema import validate_json_schema
from moonshine.providers import OfflineProvider
from moonshine.structured_tasks import register_structured_task
from moonshine.utils import overlap_score, read_text, shorten, slugify, utc_now


PENDING_RESEARCH_PROJECT = "__research_project_pending__"


RESEARCH_PROJECT_RESOLUTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "slug": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
        "tags": {"type": "array", "items": {"type": "string"}},
        "candidate_existing_projects": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "slug": {"type": "string", "minLength": 1},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["slug", "reason", "confidence"],
            },
        },
        "recommended_action": {"type": "string", "enum": ["create_new", "reuse_existing", "ask_user"]},
        "selected_project_slug": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": [
        "title",
        "slug",
        "summary",
        "tags",
        "candidate_existing_projects",
        "recommended_action",
        "selected_project_slug",
        "confidence",
    ],
}


register_structured_task(
    task_name="research-project-resolution",
    schema_name="research_project_resolution",
    schema=RESEARCH_PROJECT_RESOLUTION_SCHEMA,
    description="Generate or select a project slug for a research-mode conversation.",
)


@dataclass
class ProjectCandidate:
    """Existing project candidate suggested for reuse."""

    slug: str
    reason: str
    confidence: float


@dataclass
class ResearchProjectResolution:
    """Structured result for the first-turn research project resolver."""

    title: str
    slug: str
    summary: str
    tags: List[str] = field(default_factory=list)
    candidate_existing_projects: List[ProjectCandidate] = field(default_factory=list)
    recommended_action: str = "create_new"
    selected_project_slug: str = ""
    confidence: float = 0.0
    source: str = "heuristic"

    def to_dict(self) -> Dict[str, object]:
        return {
            "title": self.title,
            "slug": self.slug,
            "summary": self.summary,
            "tags": list(self.tags),
            "candidate_existing_projects": [candidate.__dict__ for candidate in self.candidate_existing_projects],
            "recommended_action": self.recommended_action,
            "selected_project_slug": self.selected_project_slug,
            "confidence": self.confidence,
            "source": self.source,
        }


def normalize_project_slug(value: str) -> str:
    """Normalize a generated project slug for filesystem storage."""
    text = slugify(value or "research-project", prefix="research")
    text = text.replace("-", "_")
    text = re.sub(r"_+", "_", text).strip("_")
    return (text[:80].strip("_") or "research_project")


def build_research_mode_policy(project_slug: str) -> str:
    """Return the system-prompt policy injected for mode=research."""
    return (
        "You are working inside project `%s` and carrying the mathematics forward directly.\n"
        "- Act like a mathematician pursuing a complete line of research: choose the next move yourself, work from evidence, and keep branches coherent across turns.\n"
        "- Treat the current multi-turn conversation as the live research context.\n"
        "- Use `workspace/problem.md` for the current problem statement when needed; use `read_runtime_file` or MCP filesystem tools to obtain resolved paths and file contents.\n"
        "- Use `memory/research_log.md`, `memory/research_log.jsonl`, `memory/by_type/*.md`, and `memory/research_log_index.sqlite` through `query_memory` or file reads when prior project work matters.\n"
        "- State important mathematical progress, failed paths, counterexamples, and verified results clearly; the archival pass will save them into `memory/research_log.jsonl` for later retrieval.\n"
        "- Match each nontrivial research step to the available skills and tools. When a listed skill fits the step, call `load_skill_definition` before using that skill's workflow unless the full definition is already in context.\n"
        "- Serious gate: do not start solving a selected problem until it has passed one dedicated `quality-assessor` review. If no passed review exists for the active problem, load `quality-assessor`, call `assess_problem_quality` once, and keep refining/designing the problem instead of attacking it as a theorem.\n"
        "- Use tools to read, retrieve, and verify when those actions support the research evidence; do not use tool payloads as the main home for mathematical reasoning.\n"
        "- If you judge that you have reached the final project-level proof or result, call `verify_overall` with `scope=\"final\"` before accepting it; otherwise use intermediate verification.\n"
    ) % project_slug


class ResearchProjectResolver(object):
    """Resolve a first research turn into a project slug."""

    def __init__(self, *, paths, provider=None):
        self.paths = paths
        self.provider = provider

    def list_existing_projects(self) -> List[Dict[str, object]]:
        """Return lightweight descriptions of known projects."""
        projects = []
        if not self.paths.projects_dir.exists():
            return projects
        for project_dir in sorted(self.paths.projects_dir.iterdir()):
            if not project_dir.is_dir():
                continue
            slug = project_dir.name
            problem = read_text(project_dir / "workspace" / "problem.md")
            research_log = read_text(project_dir / "memory" / "research_log.md")
            rules = read_text(project_dir / "rules.md")
            blob = "\n".join([problem, research_log, rules]).strip()
            projects.append(
                {
                    "slug": slug,
                    "summary": shorten(blob, 500) if blob else "No project summary yet.",
                    "path": str(project_dir),
                }
            )
        return projects

    def _heuristic_title(self, user_message: str) -> str:
        text = " ".join(str(user_message or "").split())
        if not text:
            return "Research Project"
        sentence = re.split(r"(?<=[.!?])\s+", text)[0]
        return shorten(sentence, 96).strip(" .") or "Research Project"

    def _similar_projects(self, user_message: str, projects: List[Dict[str, object]], limit: int = 3) -> List[ProjectCandidate]:
        ranked = []
        for item in projects:
            blob = "%s\n%s" % (item.get("slug", ""), item.get("summary", ""))
            score = overlap_score(user_message, blob)
            if score > 0:
                ranked.append((score, item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [
            ProjectCandidate(
                slug=str(item["slug"]),
                reason="Lexical overlap with existing project context.",
                confidence=min(0.95, float(score)),
            )
            for score, item in ranked[:limit]
        ]

    def _heuristic_resolution(self, user_message: str, projects: List[Dict[str, object]]) -> ResearchProjectResolution:
        title = self._heuristic_title(user_message)
        candidates = self._similar_projects(user_message, projects)
        action = "ask_user" if candidates and candidates[0].confidence >= 0.35 else "create_new"
        return ResearchProjectResolution(
            title=title,
            slug=normalize_project_slug(title),
            summary=shorten(user_message, 500) or title,
            tags=["research"],
            candidate_existing_projects=candidates,
            recommended_action=action,
            selected_project_slug=candidates[0].slug if action == "ask_user" else "",
            confidence=0.55 if action == "create_new" else candidates[0].confidence,
            source="heuristic",
        )

    def _from_payload(self, payload: Dict[str, object], projects: List[Dict[str, object]]) -> ResearchProjectResolution:
        validate_json_schema(payload, RESEARCH_PROJECT_RESOLUTION_SCHEMA)
        existing_slugs = {str(item["slug"]) for item in projects}
        candidates = []
        for item in list(payload.get("candidate_existing_projects") or []):
            slug = normalize_project_slug(str(item.get("slug", "")))
            if slug not in existing_slugs:
                continue
            candidates.append(
                ProjectCandidate(
                    slug=slug,
                    reason=str(item.get("reason", "")).strip(),
                    confidence=float(item.get("confidence", 0.0) or 0.0),
                )
            )
        selected = normalize_project_slug(str(payload.get("selected_project_slug", "") or ""))
        if selected not in existing_slugs:
            selected = ""
        action = str(payload.get("recommended_action", "create_new"))
        if candidates and action == "create_new" and candidates[0].confidence >= 0.72:
            action = "ask_user"
        if action == "reuse_existing" and not selected and candidates:
            selected = candidates[0].slug
        if action == "reuse_existing" and not selected:
            action = "create_new"
        return ResearchProjectResolution(
            title=str(payload["title"]).strip(),
            slug=normalize_project_slug(str(payload["slug"])),
            summary=str(payload["summary"]).strip(),
            tags=[str(tag).strip() for tag in list(payload.get("tags") or []) if str(tag).strip()],
            candidate_existing_projects=candidates,
            recommended_action=action,
            selected_project_slug=selected,
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            source="llm",
        )

    def resolve(self, user_message: str) -> ResearchProjectResolution:
        """Resolve a research project from the first user message."""
        projects = self.list_existing_projects()
        if self.provider is not None and not isinstance(self.provider, OfflineProvider) and hasattr(self.provider, "generate_structured"):
            try:
                prompt = (
                    "Create or select a Moonshine research project for the first user message.\n"
                    "Prefer reusing an existing project when it clearly matches. Ask the user when similarity is plausible but not certain.\n\n"
                    "First user message:\n%s\n\n"
                    "Existing projects:\n%s"
                ) % (user_message, json.dumps(projects, ensure_ascii=False, indent=2))
                payload = self.provider.generate_structured(
                    system_prompt="You assign concise, stable project titles and slugs for math research conversations.",
                    messages=[{"role": "user", "content": prompt}],
                    response_schema=RESEARCH_PROJECT_RESOLUTION_SCHEMA,
                    schema_name="research_project_resolution",
                )
                return self._from_payload(dict(payload), projects)
            except Exception:
                pass
        return self._heuristic_resolution(user_message, projects)
