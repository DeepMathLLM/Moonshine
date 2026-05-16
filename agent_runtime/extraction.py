"""Lifecycle-driven memory extraction with LLM-first, heuristic-fallback behavior."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from moonshine.agent_runtime.memory_schemas import (
    ALLOWED_DYNAMIC_ALIASES as SCHEMA_ALLOWED_DYNAMIC_ALIASES,
    KNOWN_EXTRACTION_SKILLS as SCHEMA_KNOWN_EXTRACTION_SKILLS,
    SKILL_NAME_PATTERN,
)
from moonshine.json_schema import format_schema_for_prompt, validate_json_schema
from moonshine.providers import OfflineProvider
from moonshine.structured_tasks import get_structured_task
from moonshine.utils import deterministic_slug, shorten


URL_RE = re.compile(r"https?://\S+")
ARXIV_RE = re.compile(r"\barXiv:\d{4}\.\d{4,5}(?:v\d+)?\b", re.IGNORECASE)
CONCLUSION_RE = re.compile(
    r"\b(lemma|theorem|proposition|corollary|claim|conclusion)\b[:\s-]*(.{12,220})",
    re.IGNORECASE,
)
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

KNOWN_EXTRACTION_SKILLS = set(SCHEMA_KNOWN_EXTRACTION_SKILLS)
ALLOWED_DYNAMIC_ALIASES = set(SCHEMA_ALLOWED_DYNAMIC_ALIASES)
SKILL_ALIAS_MAP = {
    "extract-user-memory": {"user-profile", "user-preferences", "feedback-corrections", "feedback-success"},
    "extract-reference-memory": {"reference-papers", "reference-theorems", "reference-resources"},
    "extract-project-memory": {"project-context"},
    "extract-project-claims": {"project-lemmas"},
    "extract-conclusion-memory": set(),
}
STRONG_SIGNAL_MARKERS = (
    "remember",
    "prefer",
    "correction",
    "decision",
    "next step",
    "plan",
    "goal",
    "lemma",
    "theorem",
    "proposition",
    "claim",
    "counterexample",
    "therefore",
    "we conclude",
    "hence",
    "arxiv:",
    "http://",
    "https://",
)


@dataclass
class ExtractedItems:
    """Extraction result container."""

    entries: List[Dict[str, object]] = field(default_factory=list)
    conclusions: List[Dict[str, object]] = field(default_factory=list)


@dataclass
class MemoryTriggerDecision:
    """Decision returned by the trigger evaluator."""

    run: bool = False
    skills: List[str] = field(default_factory=list)
    reason: str = ""
    notes: str = ""


def has_strong_memory_signal(*texts: str) -> bool:
    """Return True when a conversation window obviously contains durable-memory signals."""
    combined = " ".join(str(item or "") for item in texts).lower()
    if not combined.strip():
        return False
    return any(marker in combined for marker in STRONG_SIGNAL_MARKERS)


def _extract_json_object(raw: str) -> Dict[str, object]:
    """Best-effort JSON extraction from a model response."""
    text = str(raw or "").strip()
    if not text:
        return {}
    fence_match = JSON_FENCE_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            return dict(json.loads(text))
        except ValueError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return dict(json.loads(text[start : end + 1]))
        except ValueError:
            return {}
    return {}


def _safe_list(value: object) -> List[object]:
    """Normalize a value into a list."""
    if isinstance(value, list):
        return list(value)
    if value is None:
        return []
    return [value]


def _normalize_text(value: object) -> str:
    """Normalize structured values into plain text."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


class HeuristicMemoryExtractor(object):
    """Very lightweight memory extraction for stable offline behavior."""

    def _entry(
        self,
        *,
        alias: str,
        prefix: str,
        title: str,
        summary: str,
        body: str,
        source: str,
        project_slug: Optional[str],
        tags: List[str],
    ) -> Dict[str, object]:
        payload = body.strip()
        return {
            "alias": alias,
            "slug": deterministic_slug(prefix, payload, prefix=prefix),
            "title": title,
            "summary": shorten(summary or payload, 96),
            "body": payload,
            "source": source,
            "project_slug": project_slug,
            "tags": tags,
        }

    def _extract_conclusions(self, text: str, project_slug: Optional[str]) -> List[Dict[str, object]]:
        """Extract structured conclusions from a text block."""
        conclusions: List[Dict[str, object]] = []
        lowered = text.lower()
        for match in CONCLUSION_RE.finditer(text):
            kind = match.group(1).strip().title()
            statement = match.group(2).strip().rstrip(".")
            if len(statement) < 20:
                continue
            status = "verified" if any(token in lowered for token in ["proved", "therefore", "we showed", "we prove"]) else "partial"
            conclusions.append(
                {
                    "title": "%s: %s" % (kind, shorten(statement, 72)),
                    "statement": statement,
                    "proof_sketch": shorten(text, 240),
                    "status": status,
                    "project_slug": project_slug or "general",
                    "tags": [kind.lower(), "auto-extract"],
                }
            )
        if "counterexample" in lowered:
            conclusions.append(
                {
                    "title": "Counterexample",
                    "statement": shorten(text, 220),
                    "proof_sketch": "Auto-extracted counterexample note.",
                    "status": "partial",
                    "project_slug": project_slug or "general",
                    "tags": ["counterexample", "auto-extract"],
                }
            )
        return conclusions

    def extract(self, user_message: str, assistant_message: str, project_slug: Optional[str]) -> ExtractedItems:
        """Derive dynamic memory notes from a conversation turn."""
        result = ExtractedItems()
        user_text = " ".join(str(user_message or "").split())
        assistant_text = " ".join(str(assistant_message or "").split())
        user_lower = user_text.lower()
        assistant_lower = assistant_text.lower()
        combined_text = "\n".join(part for part in [user_text, assistant_text] if part).strip()

        if user_lower.startswith("remember:") or user_lower.startswith("remember "):
            payload = user_text.split(":", 1)[1].strip() if ":" in user_text else user_text[len("remember ") :].strip()
            if payload:
                result.entries.append(
                    self._entry(
                        alias="feedback-explicit",
                        prefix="explicit",
                        title="Explicit Memory Request",
                        summary=payload,
                        body=payload,
                        source="explicit",
                        project_slug=project_slug,
                        tags=["explicit", "memory"],
                    )
                )

        profile_markers = ["i work on ", "my research ", "i study ", "i am a ", "i'm a "]
        if any(marker in user_lower for marker in profile_markers):
            result.entries.append(
                self._entry(
                    alias="user-profile",
                    prefix="profile",
                    title="User Background",
                    summary=user_text,
                    body=user_text,
                    source="heuristic",
                    project_slug=None,
                    tags=["profile"],
                )
            )

        preference_markers = ["i prefer ", "please prefer ", "i usually ", "i tend to ", "prefer "]
        if any(marker in user_lower for marker in preference_markers):
            result.entries.append(
                self._entry(
                    alias="user-preferences",
                    prefix="preference",
                    title="User Preference",
                    summary=user_text,
                    body=user_text,
                    source="heuristic",
                    project_slug=None,
                    tags=["preference"],
                )
            )

        correction_markers = ["you are wrong", "that's wrong", "that is wrong", "correction:", "not correct"]
        if any(marker in user_lower for marker in correction_markers):
            result.entries.append(
                self._entry(
                    alias="feedback-corrections",
                    prefix="correction",
                    title="User Correction",
                    summary=user_text,
                    body=user_text,
                    source="heuristic",
                    project_slug=project_slug,
                    tags=["correction", "feedback"],
                )
            )

        success_markers = ["this worked", "works well", "successful", "that solved", "counterexample"]
        if any(marker in (user_lower + "\n" + assistant_lower) for marker in success_markers):
            result.entries.append(
                self._entry(
                    alias="feedback-success",
                    prefix="success",
                    title="Successful Pattern",
                    summary=combined_text,
                    body=combined_text,
                    source="heuristic",
                    project_slug=project_slug,
                    tags=["success-pattern"],
                )
            )

        reference_matches = URL_RE.findall(combined_text) + ARXIV_RE.findall(combined_text)
        for reference in reference_matches[:3]:
            alias = "reference-papers" if "arxiv" in reference.lower() else "reference-resources"
            result.entries.append(
                self._entry(
                    alias=alias,
                    prefix="reference",
                    title="Reference Note",
                    summary=reference,
                    body=combined_text,
                    source="reference",
                    project_slug=project_slug,
                    tags=["reference"],
                )
            )

        theorem_markers = ["lemma", "theorem", "proposition", "corollary", "nakayama", "krull"]
        if any(marker in combined_text.lower() for marker in theorem_markers):
            result.entries.append(
                self._entry(
                    alias="reference-theorems",
                    prefix="theorem",
                    title="Theorem or Lemma Reference",
                    summary=combined_text,
                    body=combined_text,
                    source="reference",
                    project_slug=project_slug,
                    tags=["theorem", "lemma", "reference"],
                )
            )

        if project_slug and any(marker in combined_text.lower() for marker in ["lemma", "theorem", "proposition", "counterexample", "claim"]):
            result.entries.append(
                self._entry(
                    alias="project-lemmas",
                    prefix="lemma",
                    title="Project Lemma or Claim",
                    summary=combined_text,
                    body=combined_text,
                    source="conversation",
                    project_slug=project_slug,
                    tags=["project", "lemma"],
                )
            )

        if project_slug and combined_text:
            result.entries.append(
                self._entry(
                    alias="project-context",
                    prefix="context",
                    title="Project Context Update",
                    summary=user_text or assistant_text,
                    body="User: %s\nAssistant: %s" % (user_text, shorten(assistant_text, 240)),
                    source="conversation",
                    project_slug=project_slug,
                    tags=["project", "context"],
                )
            )

        for conclusion in self._extract_conclusions(assistant_text or user_text, project_slug):
            result.conclusions.append(conclusion)

        if any(marker in combined_text.lower() for marker in ["counterexample", "therefore", "we conclude", "hence"]) and not result.conclusions:
            result.conclusions.append(
                {
                    "title": "Derived Conclusion",
                    "statement": shorten(combined_text, 220),
                    "proof_sketch": shorten(assistant_text or user_text, 240),
                    "status": "partial",
                    "project_slug": project_slug or "general",
                    "tags": ["auto-extract"],
                }
            )

        return result


class LLMSkillMemoryExtractor(object):
    """Run memory-trigger and extraction skills with a provider, then validate JSON proposals."""

    def __init__(self, provider, skill_manager):
        self.provider = provider
        self.skill_manager = skill_manager

    def can_use_llm(self) -> bool:
        """Return True when the provider and required skills are available."""
        if self.provider is None or isinstance(self.provider, OfflineProvider):
            return False
        if not hasattr(self.provider, "generate"):
            return False
        return self.skill_manager.get_skill("memory-trigger-evaluator") is not None

    def evaluate_trigger(self, payload: Dict[str, object]) -> MemoryTriggerDecision:
        """Invoke the trigger evaluator skill."""
        task = get_structured_task("memory-trigger-decision")
        result = self._invoke_skill_json(
            "memory-trigger-evaluator",
            payload=payload,
            schema=task.schema,
            schema_name=task.schema_name,
        )
        run = bool(result.get("run"))
        skills = [
            str(item).strip()
            for item in _safe_list(result.get("skills"))
            if str(item).strip() in KNOWN_EXTRACTION_SKILLS
        ]
        return MemoryTriggerDecision(
            run=run or bool(skills),
            skills=skills,
            reason=_normalize_text(result.get("reason")),
            notes=_normalize_text(result.get("notes")),
        )

    def run_extraction_skill(self, skill_slug: str, payload: Dict[str, object]) -> ExtractedItems:
        """Invoke one specialized extraction skill and validate its proposals."""
        if skill_slug not in KNOWN_EXTRACTION_SKILLS:
            return ExtractedItems()
        task = get_structured_task("memory-extraction-result")
        result = self._invoke_skill_json(
            skill_slug,
            payload=payload,
            schema=task.schema,
            schema_name=task.schema_name,
        )
        return ExtractedItems(
            entries=self._normalize_dynamic_entries(skill_slug, result.get("dynamic_entries"), payload),
            conclusions=self._normalize_knowledge_entries(result.get("knowledge_entries"), payload),
        )

    def _invoke_skill_json(
        self,
        skill_slug: str,
        *,
        payload: Dict[str, object],
        schema: Dict[str, object],
        schema_name: str,
    ) -> Dict[str, object]:
        """Run a skill against the provider and parse a JSON payload."""
        skill = self.skill_manager.get_skill(skill_slug)
        if skill is None or not self.can_use_llm():
            return {}

        prompt = (
            "You are executing the Agent Skill '%s'.\n"
            "Follow the skill document exactly.\n"
            "Return exactly one JSON object that strictly matches the supplied JSON schema.\n"
            "Do not include markdown fences or any explanatory prose.\n\n"
            "Skill document:\n\n%s\n\n"
            "JSON schema:\n%s\n\n"
            "Input payload:\n%s"
        ) % (
            skill_slug,
            skill.body,
            format_schema_for_prompt(schema),
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        try:
            if hasattr(self.provider, "generate_structured"):
                structured = dict(
                    self.provider.generate_structured(
                        system_prompt="You are Moonshine's internal memory extraction engine.",
                        messages=[{"role": "user", "content": prompt}],
                        response_schema=schema,
                        schema_name=schema_name,
                    )
                )
                validate_json_schema(structured, schema)
                return structured
            response = self.provider.generate(
                system_prompt="You are Moonshine's internal memory extraction engine.",
                messages=[{"role": "user", "content": prompt}],
                tool_schemas=[],
            )
            parsed = _extract_json_object(getattr(response, "content", ""))
            validate_json_schema(parsed, schema)
            return parsed
        except Exception:
            return {}

    def _normalize_dynamic_entries(
        self,
        skill_slug: str,
        raw_entries: object,
        payload: Dict[str, object],
    ) -> List[Dict[str, object]]:
        """Validate and normalize dynamic-memory proposals from the model."""
        normalized: List[Dict[str, object]] = []
        allowed_aliases = SKILL_ALIAS_MAP.get(skill_slug, set())
        project_slug = str(payload.get("project_slug", "") or "").strip() or None

        for item in _safe_list(raw_entries):
            if not isinstance(item, dict):
                continue
            alias = str(item.get("alias", "")).strip()
            if alias not in ALLOWED_DYNAMIC_ALIASES:
                continue
            if allowed_aliases and alias not in allowed_aliases:
                continue
            title = _normalize_text(item.get("title")) or alias.replace("-", " ").title()
            body = _normalize_text(item.get("body")) or _normalize_text(item.get("summary"))
            if not body:
                continue
            summary = _normalize_text(item.get("summary")) or shorten(body, 96)
            entry_project_slug = str(item.get("project_slug", "")).strip() or project_slug
            if alias.startswith("project-") and not entry_project_slug:
                continue
            tags = [str(tag).strip() for tag in _safe_list(item.get("tags")) if str(tag).strip()]
            normalized.append(
                {
                    "alias": alias,
                    "slug": self._normalize_slug(item.get("slug"), alias, title, body),
                    "title": title,
                    "summary": summary,
                    "body": body,
                    "source": "llm-skill:%s" % skill_slug,
                    "project_slug": entry_project_slug,
                    "tags": tags,
                }
            )
        return normalized

    def _normalize_slug(self, raw_slug: object, alias: str, title: str, body: str) -> str:
        """Normalize or synthesize a schema-compliant slug."""
        candidate = str(raw_slug or "").strip()
        if candidate and re.match(SKILL_NAME_PATTERN, candidate):
            return candidate
        return deterministic_slug(alias, title + "\n" + body, prefix=alias)

    def _normalize_knowledge_entries(self, raw_entries: object, payload: Dict[str, object]) -> List[Dict[str, object]]:
        """Validate and normalize knowledge-memory proposals from the model."""
        normalized: List[Dict[str, object]] = []
        default_project = str(payload.get("project_slug", "")).strip() or "general"
        for item in _safe_list(raw_entries):
            if not isinstance(item, dict):
                continue
            title = _normalize_text(item.get("title"))
            statement = _normalize_text(item.get("statement"))
            if not title or not statement:
                continue
            normalized.append(
                {
                    "title": title,
                    "statement": statement,
                    "proof_sketch": _normalize_text(item.get("proof_sketch")) or _normalize_text(item.get("evidence")),
                    "status": _normalize_text(item.get("status")) or "partial",
                    "project_slug": _normalize_text(item.get("project_slug")) or default_project,
                    "tags": [str(tag).strip() for tag in _safe_list(item.get("tags")) if str(tag).strip()],
                }
            )
        return normalized
