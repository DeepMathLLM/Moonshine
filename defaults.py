"""Compatibility exports for Moonshine constants."""

from moonshine.moonshine_constants import (
    BUILTIN_SKILLS,
    DEFAULT_AGENT_RULES_MD,
    DEFAULT_CONFIG,
    DEFAULT_PROJECT_RULES_TEMPLATE,
    GENERAL_MEMORY_SPECS,
    PROJECT_MEMORY_SPECS,
    alias_from_relative_path,
    default_config,
    general_specs,
    project_specs,
    resolve_memory_spec,
)

DEFAULT_CLAUDE_MD = DEFAULT_AGENT_RULES_MD

__all__ = [
    "BUILTIN_SKILLS",
    "DEFAULT_AGENT_RULES_MD",
    "DEFAULT_CLAUDE_MD",
    "DEFAULT_CONFIG",
    "DEFAULT_PROJECT_RULES_TEMPLATE",
    "GENERAL_MEMORY_SPECS",
    "PROJECT_MEMORY_SPECS",
    "alias_from_relative_path",
    "default_config",
    "general_specs",
    "project_specs",
    "resolve_memory_spec",
]
