"""Formal JSON schemas for lifecycle-driven memory extraction."""

from __future__ import annotations

from moonshine.structured_tasks import register_structured_task


SKILL_NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
STATUS_ENUM = ["candidate", "partial", "verified", "retracted"]
ALLOWED_DYNAMIC_ALIASES = [
    "user-profile",
    "user-preferences",
    "feedback-corrections",
    "feedback-success",
    "reference-papers",
    "reference-theorems",
    "reference-resources",
    "project-context",
    "project-lemmas",
]
KNOWN_EXTRACTION_SKILLS = [
    "extract-user-memory",
    "extract-reference-memory",
    "extract-project-memory",
    "extract-project-claims",
    "extract-conclusion-memory",
]


TRIGGER_DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "run": {"type": "boolean"},
        "skills": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": KNOWN_EXTRACTION_SKILLS,
            },
        },
        "reason": {"type": "string"},
        "notes": {"type": "string"},
    },
    "required": ["run", "skills", "reason"],
}


DYNAMIC_ENTRY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "alias": {"type": "string", "enum": ALLOWED_DYNAMIC_ALIASES},
        "slug": {"type": "string", "pattern": SKILL_NAME_PATTERN},
        "title": {"type": "string", "minLength": 1},
        "summary": {"type": "string", "minLength": 1},
        "body": {"type": "string", "minLength": 1},
        "project_slug": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["alias", "title", "summary", "body", "tags"],
}


KNOWLEDGE_ENTRY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": {"type": "string", "minLength": 1},
        "statement": {"type": "string", "minLength": 1},
        "proof_sketch": {"type": "string"},
        "status": {"type": "string", "enum": STATUS_ENUM},
        "project_slug": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "statement", "proof_sketch", "status", "tags"],
}


EXTRACTION_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "dynamic_entries": {"type": "array", "items": DYNAMIC_ENTRY_SCHEMA},
        "knowledge_entries": {"type": "array", "items": KNOWLEDGE_ENTRY_SCHEMA},
        "skip_reason": {"type": "string"},
    },
    "required": ["dynamic_entries", "knowledge_entries"],
}


register_structured_task(
    task_name="memory-trigger-decision",
    schema_name="memory_trigger_decision",
    schema=TRIGGER_DECISION_SCHEMA,
    description="Route one lifecycle event to the appropriate memory extraction skills.",
)

register_structured_task(
    task_name="memory-extraction-result",
    schema_name="memory_extraction_result",
    schema=EXTRACTION_RESULT_SCHEMA,
    description="Return validated dynamic-memory and knowledge-memory proposals.",
)
