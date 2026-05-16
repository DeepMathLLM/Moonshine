"""Compatibility wrapper for portable Agent Skills templates."""

from __future__ import annotations

from moonshine.skills.skill_document import (
    BODY_SECTION_ORDER,
    SKILL_STANDARD,
    build_skill_body,
    ensure_valid_skill_document,
    normalize_skill_metadata,
    validate_skill_body,
    validate_skill_document,
)


SKILL_TEMPLATE_VERSION = SKILL_STANDARD

__all__ = [
    "BODY_SECTION_ORDER",
    "SKILL_STANDARD",
    "SKILL_TEMPLATE_VERSION",
    "build_skill_body",
    "normalize_skill_metadata",
    "validate_skill_body",
    "validate_skill_document",
    "ensure_valid_skill_document",
]

