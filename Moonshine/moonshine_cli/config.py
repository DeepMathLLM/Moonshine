"""Configuration and runtime layout management for Moonshine."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from moonshine.moonshine_constants import (
    DEFAULT_AGENT_RULES_MD,
    DEFAULT_CONFIG,
    DEFAULT_PROJECT_AGENTS_TEMPLATE,
    DEFAULT_PROJECT_RULES_TEMPLATE,
    MoonshinePaths,
    default_config,
    general_specs,
    packaged_builtin_agents_dir,
    packaged_builtin_skills_dir,
    packaged_mcp_servers_dir,
    packaged_tool_definitions_dir,
    project_specs,
)
from moonshine.utils import atomic_write, ensure_directory, read_json, read_text, write_json


LEGACY_RULE_MARKERS = [
    "Layer 5 (knowledge graph) and layer 6 (background evolution) are intentionally skipped in this version.",
    "This build implements memory layers 1-4: static rules, dynamic memory, session memory, and structured knowledge.",
    "If the user explicitly asks Moonshine to remember something",
]

PLACEHOLDER_PROJECT_RULE_MARKERS = [
    "This file was created automatically by Moonshine.",
    "Add project-specific constraints here.",
    "Add the current research goal here.",
    "Record the current target here when it becomes stable.",
    "Record assumptions, conventions, exclusions, and fixed notation here.",
    "Record the current branches, subgoals, or near-term priorities here.",
]

PLACEHOLDER_PROJECT_AGENT_MARKERS = [
    "Keep this file brief and local to the project.",
    "Record project-specific aims, notation, assumptions, conventions, or exclusions here when they become stable.",
    "Record project-local file conventions, preferred references, or branch-specific working habits here when they differ from Moonshine's default research process.",
]

LEGACY_REMOVED_PACKAGED_TOOL_DEFINITIONS = [
    "remember_text.md",
    "record_branch_update.md",
    "record_candidate_problem.md",
    "record_counterexample.md",
    "record_novelty_note.md",
    "record_problem_review.md",
    "record_selected_plan.md",
    "record_special_case_check.md",
    "record_toy_example.md",
]

def _looks_like_legacy_rules(text: str) -> bool:
    """Return whether a rules/agents file still contains the older default boilerplate."""
    stripped = (text or "").strip()
    return bool(stripped) and any(marker in stripped for marker in LEGACY_RULE_MARKERS)


def _looks_like_placeholder_project_rules(text: str) -> bool:
    """Return whether a project rules file still contains only placeholder boilerplate."""
    stripped = (text or "").strip()
    return bool(stripped) and any(marker in stripped for marker in PLACEHOLDER_PROJECT_RULE_MARKERS)


def _looks_like_placeholder_project_agents(text: str) -> bool:
    """Return whether a project AGENTS file still contains only placeholder boilerplate."""
    stripped = (text or "").strip()
    return bool(stripped) and any(marker in stripped for marker in PLACEHOLDER_PROJECT_AGENT_MARKERS)


def _config_value(section: str, key: str) -> Any:
    """Return a scalar default from the single DEFAULT_CONFIG source."""
    return DEFAULT_CONFIG[section][key]


def _optional_float(value: Any) -> Optional[float]:
    """Coerce nullable numeric defaults for provider settings."""
    if value is None:
        return None
    return float(value)


@dataclass
class ProviderConfig:
    """Provider configuration."""

    type: str = str(_config_value("provider", "type"))
    model: str = str(_config_value("provider", "model"))
    base_url: str = str(_config_value("provider", "base_url"))
    api_key_env: str = str(_config_value("provider", "api_key_env"))
    api_version: str = str(_config_value("provider", "api_version"))
    timeout_seconds: int = int(_config_value("provider", "timeout_seconds"))
    temperature: Optional[float] = _optional_float(_config_value("provider", "temperature"))
    stream: bool = bool(_config_value("provider", "stream"))
    max_retries: int = int(_config_value("provider", "max_retries"))
    retry_backoff_seconds: float = float(_config_value("provider", "retry_backoff_seconds"))
    max_context_tokens: int = int(_config_value("provider", "max_context_tokens"))


@dataclass
class VerificationProviderConfig:
    """Dedicated verification-provider configuration."""

    inherit_from_main: bool = bool(DEFAULT_CONFIG["verification_provider"]["inherit_from_main"])
    type: str = str(DEFAULT_CONFIG["verification_provider"]["type"])
    model: str = str(DEFAULT_CONFIG["verification_provider"]["model"])
    base_url: str = str(DEFAULT_CONFIG["verification_provider"]["base_url"])
    api_key_env: str = str(DEFAULT_CONFIG["verification_provider"]["api_key_env"])
    api_version: str = str(DEFAULT_CONFIG["verification_provider"]["api_version"])
    timeout_seconds: int = int(DEFAULT_CONFIG["verification_provider"]["timeout_seconds"])
    temperature: Optional[float] = _optional_float(DEFAULT_CONFIG["verification_provider"]["temperature"])
    stream: bool = bool(DEFAULT_CONFIG["verification_provider"]["stream"])
    max_retries: int = int(DEFAULT_CONFIG["verification_provider"]["max_retries"])
    retry_backoff_seconds: float = float(DEFAULT_CONFIG["verification_provider"]["retry_backoff_seconds"])
    max_context_tokens: int = int(DEFAULT_CONFIG["verification_provider"]["max_context_tokens"])


@dataclass
class AgentConfig:
    """Conversation-loop configuration."""

    max_tool_rounds: int = int(_config_value("agent", "max_tool_rounds"))
    max_model_rounds: int = int(_config_value("agent", "max_model_rounds"))
    max_empty_response_retries: int = int(_config_value("agent", "max_empty_response_retries"))
    max_tool_validation_retries: int = int(_config_value("agent", "max_tool_validation_retries"))
    max_consecutive_errors: int = int(_config_value("agent", "max_consecutive_errors"))
    max_tool_calls_per_round: int = int(_config_value("agent", "max_tool_calls_per_round"))
    research_max_iterations: int = int(_config_value("agent", "research_max_iterations"))
    verification_dimension_review_count: int = int(_config_value("agent", "verification_dimension_review_count"))
    emit_status_events: bool = bool(_config_value("agent", "emit_status_events"))


@dataclass
class ExposureConfig:
    """Tool and skill exposure controls."""

    tools_include: List[str] = field(default_factory=lambda: list(DEFAULT_CONFIG.get("exposure", {}).get("tools_include", [])))
    tools_exclude: List[str] = field(default_factory=lambda: list(DEFAULT_CONFIG.get("exposure", {}).get("tools_exclude", [])))
    skills_include: List[str] = field(default_factory=lambda: list(DEFAULT_CONFIG.get("exposure", {}).get("skills_include", [])))
    skills_exclude: List[str] = field(default_factory=lambda: list(DEFAULT_CONFIG.get("exposure", {}).get("skills_exclude", [])))


@dataclass
class MemoryConfig:
    """Memory system configuration."""

    auto_extract: bool = bool(_config_value("memory", "auto_extract"))
    max_index_lines: int = int(_config_value("memory", "max_index_lines"))
    session_search_limit: int = int(_config_value("memory", "session_search_limit"))
    knowledge_search_limit: int = int(_config_value("memory", "knowledge_search_limit"))
    knowledge_vector_enabled: bool = bool(_config_value("memory", "knowledge_vector_enabled"))
    knowledge_vector_backend: str = str(_config_value("memory", "knowledge_vector_backend"))
    knowledge_vector_weight: float = float(_config_value("memory", "knowledge_vector_weight"))
    knowledge_fts_weight: float = float(_config_value("memory", "knowledge_fts_weight"))
    knowledge_lexical_weight: float = float(_config_value("memory", "knowledge_lexical_weight"))
    knowledge_embedding_provider: str = str(_config_value("memory", "knowledge_embedding_provider"))
    knowledge_embedding_model: str = str(_config_value("memory", "knowledge_embedding_model"))
    knowledge_embedding_base_url: str = str(_config_value("memory", "knowledge_embedding_base_url"))
    knowledge_embedding_api_key_env: str = str(_config_value("memory", "knowledge_embedding_api_key_env"))
    knowledge_embedding_timeout_seconds: int = int(_config_value("memory", "knowledge_embedding_timeout_seconds"))
    knowledge_embedding_dimension: int = int(_config_value("memory", "knowledge_embedding_dimension"))
    review_stale_days: int = int(_config_value("memory", "review_stale_days"))
    recent_message_limit: int = int(_config_value("memory", "recent_message_limit"))
    auto_extract_min_messages: int = int(_config_value("memory", "auto_extract_min_messages"))
    auto_extract_min_minutes: int = int(_config_value("memory", "auto_extract_min_minutes"))
    auto_extract_background: bool = bool(_config_value("memory", "auto_extract_background"))
    auto_extract_max_workers: int = int(_config_value("memory", "auto_extract_max_workers"))


@dataclass
class ContextConfig:
    """Context loading and compression configuration."""

    system_prompt_token_budget: int = int(_config_value("context", "system_prompt_token_budget"))
    claude_md_token_budget: int = int(_config_value("context", "claude_md_token_budget"))
    config_token_budget: int = int(_config_value("context", "config_token_budget"))
    memory_index_lines: int = int(_config_value("context", "memory_index_lines"))
    memory_index_token_budget: int = int(_config_value("context", "memory_index_token_budget"))
    project_context_token_budget: int = int(_config_value("context", "project_context_token_budget"))
    project_rules_token_budget: int = int(_config_value("context", "project_rules_token_budget"))
    retrieval_summary_token_budget: int = int(_config_value("context", "retrieval_summary_token_budget"))
    history_compression_token_budget: int = int(_config_value("context", "history_compression_token_budget"))
    history_compression_chunk_count: int = int(_config_value("context", "history_compression_chunk_count"))
    history_compression_chunk_token_budget: int = int(_config_value("context", "history_compression_chunk_token_budget"))
    compression_threshold_tokens: int = int(_config_value("context", "compression_threshold_tokens"))
    recent_raw_message_count: int = int(_config_value("context", "recent_raw_message_count"))
    compression_min_recent_messages: int = int(_config_value("context", "compression_min_recent_messages"))
    warning_ratio: float = float(_config_value("context", "warning_ratio"))
    pressure_warning_ratio: float = float(_config_value("context", "pressure_warning_ratio"))
    pressure_critical_ratio: float = float(_config_value("context", "pressure_critical_ratio"))
    tail_token_budget_ratio: float = float(_config_value("context", "tail_token_budget_ratio"))
    protect_first_message_count: int = int(_config_value("context", "protect_first_message_count"))
    tool_output_prune_char_threshold: int = int(_config_value("context", "tool_output_prune_char_threshold"))
    tool_call_argument_prune_char_threshold: int = int(_config_value("context", "tool_call_argument_prune_char_threshold"))
    overflow_retry_limit: int = int(_config_value("context", "overflow_retry_limit"))


@dataclass
class AppConfig:
    """Top-level application config."""

    default_mode: str = str(DEFAULT_CONFIG["default_mode"])
    default_project: str = str(DEFAULT_CONFIG["default_project"])
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    verification_provider: VerificationProviderConfig = field(default_factory=VerificationProviderConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    exposure: ExposureConfig = field(default_factory=ExposureConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    context: ContextConfig = field(default_factory=ContextConfig)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "AppConfig":
        """Build a config from a raw dictionary."""
        context_payload = dict(payload.get("context", {}))
        if "warning_ratio" not in context_payload and "compression_trigger_ratio" in context_payload:
            context_payload["warning_ratio"] = context_payload["compression_trigger_ratio"]
        return cls(
            default_mode=payload.get("default_mode", DEFAULT_CONFIG["default_mode"]),
            default_project=payload.get("default_project", DEFAULT_CONFIG["default_project"]),
            provider=ProviderConfig(**dict(payload.get("provider", {}))),
            verification_provider=VerificationProviderConfig(**dict(payload.get("verification_provider", {}))),
            agent=AgentConfig(**dict(payload.get("agent", {}))),
            exposure=ExposureConfig(**dict(payload.get("exposure", {}))),
            memory=MemoryConfig(**dict(payload.get("memory", {}))),
            context=ContextConfig(**context_payload),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the config."""
        return asdict(self)


def resolve_home(home: Optional[str] = None) -> Path:
    """Resolve the Moonshine runtime home."""
    if home:
        return Path(home).expanduser().resolve()
    return (Path.home() / ".moonshine").resolve()


def render_core_config_yaml(config: AppConfig) -> str:
    """Render a stable YAML summary used by context loading."""
    lines = [
        "default_mode: %s" % config.default_mode,
        "default_project: %s" % config.default_project,
        "provider:",
        "  type: %s" % config.provider.type,
        "  model: %s" % config.provider.model,
        "  base_url: %s" % config.provider.base_url,
        "  api_key_env: %s" % config.provider.api_key_env,
        "  api_version: %s" % config.provider.api_version,
        "  timeout_seconds: %s" % config.provider.timeout_seconds,
        "  temperature: %s" % config.provider.temperature,
        "  stream: %s" % str(config.provider.stream).lower(),
        "  max_context_tokens: %s" % config.provider.max_context_tokens,
        "verification_provider:",
        "  inherit_from_main: %s" % str(config.verification_provider.inherit_from_main).lower(),
        "  type: %s" % config.verification_provider.type,
        "  model: %s" % config.verification_provider.model,
        "  base_url: %s" % config.verification_provider.base_url,
        "  api_key_env: %s" % config.verification_provider.api_key_env,
        "  api_version: %s" % config.verification_provider.api_version,
        "  timeout_seconds: %s" % config.verification_provider.timeout_seconds,
        "  temperature: %s" % config.verification_provider.temperature,
        "  stream: %s" % str(config.verification_provider.stream).lower(),
        "  max_context_tokens: %s" % config.verification_provider.max_context_tokens,
        "agent:",
        "  max_model_rounds: %s" % config.agent.max_model_rounds,
        "  max_tool_rounds: %s" % config.agent.max_tool_rounds,
        "  max_tool_calls_per_round: %s" % config.agent.max_tool_calls_per_round,
        "  research_max_iterations: %s" % config.agent.research_max_iterations,
        "exposure:",
        "  tools_include: %s" % json.dumps(config.exposure.tools_include, ensure_ascii=False),
        "  tools_exclude: %s" % json.dumps(config.exposure.tools_exclude, ensure_ascii=False),
        "  skills_include: %s" % json.dumps(config.exposure.skills_include, ensure_ascii=False),
        "  skills_exclude: %s" % json.dumps(config.exposure.skills_exclude, ensure_ascii=False),
        "memory:",
        "  auto_extract: %s" % str(config.memory.auto_extract).lower(),
        "  auto_extract_min_messages: %s" % config.memory.auto_extract_min_messages,
        "  auto_extract_min_minutes: %s" % config.memory.auto_extract_min_minutes,
        "  auto_extract_background: %s" % str(config.memory.auto_extract_background).lower(),
        "  auto_extract_max_workers: %s" % config.memory.auto_extract_max_workers,
        "  session_search_limit: %s" % config.memory.session_search_limit,
        "  knowledge_search_limit: %s" % config.memory.knowledge_search_limit,
        "  knowledge_vector_enabled: %s" % str(config.memory.knowledge_vector_enabled).lower(),
        "  knowledge_vector_backend: %s" % config.memory.knowledge_vector_backend,
        "  knowledge_embedding_provider: %s" % config.memory.knowledge_embedding_provider,
        "  knowledge_embedding_model: %s" % config.memory.knowledge_embedding_model,
        "context:",
        "  system_prompt_token_budget: %s" % config.context.system_prompt_token_budget,
        "  claude_md_token_budget: %s" % config.context.claude_md_token_budget,
        "  config_token_budget: %s" % config.context.config_token_budget,
        "  memory_index_lines: %s" % config.context.memory_index_lines,
        "  memory_index_token_budget: %s" % config.context.memory_index_token_budget,
        "  project_context_token_budget: %s" % config.context.project_context_token_budget,
        "  project_rules_token_budget: %s" % config.context.project_rules_token_budget,
        "  retrieval_summary_token_budget: %s" % config.context.retrieval_summary_token_budget,
        "  history_compression_token_budget: %s" % config.context.history_compression_token_budget,
        "  history_compression_chunk_token_budget: %s" % config.context.history_compression_chunk_token_budget,
        "  compression_threshold_tokens: %s" % config.context.compression_threshold_tokens,
        "  recent_raw_message_count: %s" % config.context.recent_raw_message_count,
        "  compression_min_recent_messages: %s" % config.context.compression_min_recent_messages,
        "  compression_trigger_ratio: %s" % config.context.warning_ratio,
        "  pressure_warning_ratio: %s" % config.context.pressure_warning_ratio,
        "  pressure_critical_ratio: %s" % config.context.pressure_critical_ratio,
        "  tail_token_budget_ratio: %s" % config.context.tail_token_budget_ratio,
        "  protect_first_message_count: %s" % config.context.protect_first_message_count,
        "  tool_output_prune_char_threshold: %s" % config.context.tool_output_prune_char_threshold,
        "  tool_call_argument_prune_char_threshold: %s" % config.context.tool_call_argument_prune_char_threshold,
        "  overflow_retry_limit: %s" % config.context.overflow_retry_limit,
    ]
    return "\n".join(lines).rstrip() + "\n"


def _global_agents_template(paths: MoonshinePaths) -> str:
    """Return the global AGENTS.md content used to seed project instructions."""
    global_agents = read_text(paths.global_agents_file).strip()
    if global_agents:
        return global_agents
    global_rules = read_text(paths.global_rules_file).strip()
    if global_rules:
        return global_rules
    legacy = read_text(paths.legacy_global_rules_file).strip()
    if legacy:
        return legacy
    return DEFAULT_AGENT_RULES_MD.strip()


def ensure_project_layout(paths: MoonshinePaths, project_slug: str) -> None:
    """Ensure the on-disk structure for a project exists."""
    ensure_directory(paths.project_dir(project_slug))
    ensure_directory(paths.project_workspace_dir(project_slug))
    ensure_directory(paths.project_dir(project_slug) / "memory")
    ensure_directory(paths.project_references_dir(project_slug))
    ensure_directory(paths.project_reference_papers_dir(project_slug))
    ensure_directory(paths.project_reference_notes_dir(project_slug))
    ensure_directory(paths.project_reference_surveys_dir(project_slug))
    paths.project_reference_index_file(project_slug).touch(exist_ok=True)
    paths.project_research_log_file(project_slug).touch(exist_ok=True)
    paths.project_research_log_summaries_file(project_slug).touch(exist_ok=True)
    if not paths.project_problem_draft_file(project_slug).exists():
        atomic_write(
            paths.project_problem_draft_file(project_slug),
            "# Current Problem Draft\n\n"
            "Use this file as the formal current problem statement for the project.\n",
        )
    if not paths.project_blueprint_file(project_slug).exists():
        atomic_write(
            paths.project_blueprint_file(project_slug),
            "# Proof Blueprint Draft\n\n"
            "Moonshine updates this file when the main agent emits a `## Blueprint Draft` section.\n",
        )
    if not paths.project_blueprint_verified_file(project_slug).exists():
        atomic_write(
            paths.project_blueprint_verified_file(project_slug),
            "# Verified Proof Blueprint\n\n"
            "This file is published only after final verification succeeds.\n",
        )

    rules_path = paths.project_rules_file(project_slug)
    if (not rules_path.exists()) or _looks_like_placeholder_project_rules(read_text(rules_path)):
        atomic_write(rules_path, DEFAULT_PROJECT_RULES_TEMPLATE.format(project_slug=project_slug).strip() + "\n")

    agents_path = paths.project_agents_file(project_slug)
    if (
        not agents_path.exists()
        or not read_text(agents_path).strip()
        or _looks_like_placeholder_project_agents(read_text(agents_path))
        or _looks_like_legacy_rules(read_text(agents_path))
    ):
        atomic_write(agents_path, DEFAULT_PROJECT_AGENTS_TEMPLATE.format(project_slug=project_slug).strip() + "\n")


def ensure_runtime_home(paths: MoonshinePaths, default_project: str = "general") -> None:
    """Create the runtime home and baseline files."""
    for directory in [
        paths.home,
        paths.config_dir,
        paths.memory_dir,
        paths.knowledge_dir,
        paths.knowledge_entries_dir,
        paths.knowledge_vector_dir,
        paths.databases_dir,
        paths.sessions_dir,
        paths.agents_dir,
        paths.builtin_agents_dir,
        paths.installed_agents_dir,
        paths.skills_dir,
        paths.builtin_skills_dir,
        paths.installed_skills_dir,
        paths.tools_dir,
        paths.tool_definitions_dir,
        paths.mcp_dir,
        paths.mcp_servers_dir,
        paths.projects_dir,
    ]:
        ensure_directory(directory)

    if not paths.config_file.exists():
        write_json(paths.config_file, default_config())
    config = AppConfig.from_dict(read_json(paths.config_file, default_config()))
    atomic_write(paths.core_config_file, render_core_config_yaml(config))
    if not paths.global_rules_file.exists():
        legacy = read_text(paths.legacy_global_rules_file).strip()
        atomic_write(paths.global_rules_file, (legacy or DEFAULT_AGENT_RULES_MD).strip() + "\n")
    elif _looks_like_legacy_rules(read_text(paths.global_rules_file)):
        atomic_write(paths.global_rules_file, DEFAULT_AGENT_RULES_MD.strip() + "\n")
    if (
        not paths.global_agents_file.exists()
        or not read_text(paths.global_agents_file).strip()
        or _looks_like_legacy_rules(read_text(paths.global_agents_file))
    ):
        atomic_write(paths.global_agents_file, _global_agents_template(paths).strip() + "\n")
    if not paths.memory_index_file.exists():
        atomic_write(paths.memory_index_file, "# Moonshine Memory Index\n\n")
    if not paths.knowledge_index_file.exists():
        atomic_write(paths.knowledge_index_file, "# Moonshine Knowledge Index\n\n")
    if not paths.skills_registry_file.exists():
        write_json(paths.skills_registry_file, {"builtin": [], "installed": []})
    if not paths.agents_registry_file.exists():
        write_json(paths.agents_registry_file, {"builtin": [], "installed": []})

    for spec in general_specs():
        file_path = paths.home / spec["relative_path"]
        if not file_path.exists():
            atomic_write(file_path, spec["header"].strip() + "\n\n")

    packaged_agents_dir = packaged_builtin_agents_dir()
    if packaged_agents_dir.exists():
        for agent_file in packaged_agents_dir.rglob("AGENT.md"):
            relative_path = agent_file.relative_to(packaged_agents_dir)
            target = paths.builtin_agents_dir / relative_path
            ensure_directory(target.parent)
            rendered = read_text(agent_file).rstrip() + "\n"
            if not target.exists() or read_text(target).rstrip() != rendered.rstrip():
                atomic_write(target, rendered)

    packaged_skills_dir = packaged_builtin_skills_dir()
    if packaged_skills_dir.exists():
        packaged_skill_relpaths = {
            skill_file.relative_to(packaged_skills_dir)
            for skill_file in packaged_skills_dir.rglob("SKILL.md")
        }
        for runtime_skill in list(paths.builtin_skills_dir.rglob("SKILL.md")):
            relative_path = runtime_skill.relative_to(paths.builtin_skills_dir)
            if relative_path not in packaged_skill_relpaths:
                runtime_skill.unlink()
                try:
                    runtime_skill.parent.rmdir()
                except OSError:
                    pass
        for skill_file in packaged_skills_dir.rglob("SKILL.md"):
            relative_path = skill_file.relative_to(packaged_skills_dir)
            target = paths.builtin_skills_dir / relative_path
            ensure_directory(target.parent)
            rendered = read_text(skill_file).rstrip() + "\n"
            if not target.exists() or read_text(target).rstrip() != rendered.rstrip():
                atomic_write(target, rendered)

    packaged_tools_dir = packaged_tool_definitions_dir()
    if packaged_tools_dir.exists():
        for definition_file in packaged_tools_dir.rglob("*.md"):
            relative_path = definition_file.relative_to(packaged_tools_dir)
            target = paths.tool_definitions_dir / relative_path
            ensure_directory(target.parent)
            rendered = read_text(definition_file).rstrip() + "\n"
            if not target.exists() or read_text(target).rstrip() != rendered.rstrip():
                atomic_write(target, rendered)
    for relative_name in LEGACY_REMOVED_PACKAGED_TOOL_DEFINITIONS:
        legacy_target = paths.tool_definitions_dir / relative_name
        if legacy_target.exists():
            legacy_target.unlink()

    packaged_mcp_dir = packaged_mcp_servers_dir()
    if packaged_mcp_dir.exists():
        for server_file in packaged_mcp_dir.rglob("*.md"):
            relative_path = server_file.relative_to(packaged_mcp_dir)
            target = paths.mcp_servers_dir / relative_path
            ensure_directory(target.parent)
            if not target.exists():
                atomic_write(target, read_text(server_file).rstrip() + "\n")

    ensure_project_layout(paths, default_project)


def load_config(paths: MoonshinePaths) -> AppConfig:
    """Load the application config from disk."""
    return AppConfig.from_dict(read_json(paths.config_file, default_config()))


def save_config(paths: MoonshinePaths, config: AppConfig) -> None:
    """Persist the application config."""
    write_json(paths.config_file, config.to_dict())
    atomic_write(paths.core_config_file, render_core_config_yaml(config))
