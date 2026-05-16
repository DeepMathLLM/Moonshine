"""Shared constants and path helpers for Moonshine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


RESEARCH_MEMORY_CHANNELS = [
    "immediate_conclusions",
    "toy_examples",
    "counterexamples",
    "big_decisions",
    "special_case_checks",
    "novelty_notes",
    "subgoals",
    "solve_steps",
    "failed_paths",
    "verification_reports",
    "branch_states",
    "events",
]


RESEARCH_CHANNEL_ALIASES = {channel: [channel] for channel in RESEARCH_MEMORY_CHANNELS}


DEFAULT_AGENT_RULES_MD = """# Moonshine Working Rules

## Identity
- You are Moonshine, a careful mathematical and technical researcher.
- Carry conversations and projects forward directly, with durable memory and explicit evidence.

## Execution
- Let ordinary turns carry the reasoning; in research mode, use project research-memory files as evidence when prior progress matters.
- Treat skills as working methods and tools as executable actions.
- When a brief summary is not enough, load the full agent, skill, tool, or MCP definition.

## General Work
- Answer directly and keep the response proportionate to the task.
- Use `query_memory` when prior work materially affects the current turn.
- Check `search_knowledge` before re-deriving a stable stored conclusion.
- Store reusable conclusions with explicit status and supporting evidence.
- For live or recent information, prefer live-search tools when they are available.

## Research Work
- Build and stabilize the problem before committing to full solving.
- Move between `problem_design` and `problem_solving` through saved evidence and explicit review/verification discipline.
- `workspace/problem.md` is the current problem reference; `memory/research_log.md`, `memory/research_log.jsonl`, `memory/by_type/*.md`, and `memory/research_log_index.sqlite` are project research-memory sources for reading and retrieval.
- Use the research log as the retrieval source for prior failed paths, examples, counterexamples, checks, plans, and branch notes.

## Evidence
- Trust tool results, verifier payloads, saved artifacts, memory records, and workspace files over optimistic prose.
- Treat partial progress as partial progress; completion requires a real verified result or a clear unresolved checkpoint.

## Output Style
- Default to concise English.
- Preserve mathematical notation and terminology when they improve precision.
- Prefer clear structure over ornamental prose.
"""


DEFAULT_PROJECT_RULES_TEMPLATE = """# Project Rules: {project_slug}

## Active Mathematical Target
- Record the current target here when it becomes stable.

## Local Constraints
- Record assumptions, conventions, exclusions, and fixed notation here.

## Working Priorities
- Record the current branches, subgoals, or near-term priorities here.
"""


DEFAULT_PROJECT_AGENTS_TEMPLATE = """# Project AGENTS: {project_slug}

Keep this file brief and local to the project.

## Local Mathematical Direction
- Record project-specific aims, notation, assumptions, conventions, or exclusions here when they become stable.

## Local Working Preferences
- Record project-local file conventions, preferred references, or branch-specific working habits here when they differ from Moonshine's default research process.
"""


DEFAULT_CONFIG = {
    "default_mode": "chat",
    "default_project": "general",
    "provider": {
        "type": "offline",
        "model": "moonshine-basic",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "api_version": "",
        "timeout_seconds": 600,
        "temperature": 0.2,
        "stream": True,
        "max_retries": 2,
        "retry_backoff_seconds": 5.0,
        "max_context_tokens": 258000,
    },
    "verification_provider": {
        "inherit_from_main": True,
        "type": "offline",
        "model": "moonshine-basic",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "api_version": "",
        "timeout_seconds": 600,
        "temperature": 0.2,
        "stream": False,
        "max_retries": 2,
        "retry_backoff_seconds": 5.0,
        "max_context_tokens": 258000,
    },
    "agent": {
        "max_tool_rounds": 8,
        "max_model_rounds": 12,
        "max_empty_response_retries": 2,
        "max_tool_validation_retries": 3,
        "max_consecutive_errors": 3,
        "max_tool_calls_per_round": 20,
        "research_max_iterations": 100,
        "verification_dimension_review_count": 1,
        "emit_status_events": True,
    },
    "exposure": {
        "tools_include": [],
        "tools_exclude": [],
        "skills_include": [],
        "skills_exclude": [],
    },
    "memory": {
        "auto_extract": True,
        "max_index_lines": 300,
        "session_search_limit": 5,
        "knowledge_search_limit": 5,
        "knowledge_vector_enabled": True,
        "knowledge_vector_backend": "auto",
        "knowledge_vector_weight": 0.55,
        "knowledge_fts_weight": 0.35,
        "knowledge_lexical_weight": 0.10,
        "knowledge_embedding_provider": "hashing",
        "knowledge_embedding_model": "text-embedding-3-small",
        "knowledge_embedding_base_url": "https://api.openai.com/v1",
        "knowledge_embedding_api_key_env": "OPENAI_API_KEY",
        "knowledge_embedding_timeout_seconds": 60,
        "knowledge_embedding_dimension": 384,
        "review_stale_days": 14,
        "recent_message_limit": 8,
        "auto_extract_min_messages": 8,
        "auto_extract_min_minutes": 15,
        "auto_extract_background": True,
        "auto_extract_max_workers": 1,
    },
    "context": {
        "system_prompt_token_budget": 1000,
        "claude_md_token_budget": 1000,
        "config_token_budget": 300,
        "memory_index_lines": 200,
        "memory_index_token_budget": 3000,
        "project_context_token_budget": 500,
        "project_rules_token_budget": 500,
        "retrieval_summary_token_budget": 1200,
        "history_compression_token_budget": 90000,
        "history_compression_chunk_count": 60,
        "history_compression_chunk_token_budget": 1500,
        "compression_threshold_tokens": 200000,
        "recent_raw_message_count": 6,
        "compression_min_recent_messages": 3,
        "warning_ratio": 0.5,
        "pressure_warning_ratio": 0.85,
        "pressure_critical_ratio": 0.95,
        "tail_token_budget_ratio": 0.2,
        "protect_first_message_count": 1,
        "tool_output_prune_char_threshold": 240,
        "tool_call_argument_prune_char_threshold": 500,
        "overflow_retry_limit": 2,
    },
}


GENERAL_MEMORY_SPECS = [
    {
        "alias": "user-profile",
        "group": "User Profile",
        "label": "Long-term background",
        "relative_path": "memory/user/profile.md",
        "header": "# User Profile\n\nMoonshine maintains this file for durable user background facts.\n",
    },
    {
        "alias": "user-preferences",
        "group": "User Profile",
        "label": "Interaction preferences",
        "relative_path": "memory/user/preferences.md",
        "header": "# User Preferences\n\nMoonshine maintains this file for workflow and style preferences.\n",
    },
    {
        "alias": "feedback-corrections",
        "group": "Behavior Feedback",
        "label": "Corrections",
        "relative_path": "memory/feedback/corrections.md",
        "header": "# Corrections\n\nMoonshine maintains this file for user corrections and mistakes to avoid.\n",
    },
    {
        "alias": "feedback-explicit",
        "group": "Behavior Feedback",
        "label": "Explicit memory requests",
        "relative_path": "memory/feedback/explicit.md",
        "header": "# Explicit Memory Requests\n\nMoonshine maintains this file for direct remember instructions.\n",
    },
    {
        "alias": "feedback-success",
        "group": "Behavior Feedback",
        "label": "Successful patterns",
        "relative_path": "memory/feedback/success_patterns.md",
        "header": "# Successful Patterns\n\nMoonshine maintains this file for strategies that worked well.\n",
    },
    {
        "alias": "project-active",
        "group": "Project Tracking",
        "label": "Active projects",
        "relative_path": "memory/projects/active.md",
        "header": "# Active Projects\n\nMoonshine maintains this file as a lightweight cross-project index.\n",
    },
    {
        "alias": "reference-papers",
        "group": "References",
        "label": "Papers",
        "relative_path": "memory/references/papers.md",
        "header": "# Key Papers\n\nMoonshine maintains this file for important papers and bibliographic notes.\n",
    },
    {
        "alias": "reference-theorems",
        "group": "References",
        "label": "Theorems",
        "relative_path": "memory/references/theorems.md",
        "header": "# Key Theorems\n\nMoonshine maintains this file for reusable theorem summaries.\n",
    },
    {
        "alias": "reference-resources",
        "group": "References",
        "label": "External resources",
        "relative_path": "memory/references/resources.md",
        "header": "# External Resources\n\nMoonshine maintains this file for websites, tools, and datasets.\n",
    },
]


PROJECT_MEMORY_SPECS = [
    {
        "alias": "project-context",
        "group": "Project Tracking",
        "label": "Context",
        "relative_path": "projects/{project_slug}/memory/context.md",
        "header": "# Project Context\n\nMoonshine maintains this file for project background and working context.\n",
    },
    {
        "alias": "project-decisions",
        "group": "Project Tracking",
        "label": "Decisions",
        "relative_path": "projects/{project_slug}/memory/decisions.md",
        "header": "# Project Decisions\n\nMoonshine maintains this file for durable decisions and tradeoffs.\n",
    },
    {
        "alias": "project-lemmas",
        "group": "Project Tracking",
        "label": "Lemmas",
        "relative_path": "projects/{project_slug}/memory/lemmas.md",
        "header": "# Project Lemmas\n\nMoonshine maintains this file for intermediate claims and lemma candidates.\n",
    },
    {
        "alias": "project-progress",
        "group": "Project Tracking",
        "label": "Progress",
        "relative_path": "projects/{project_slug}/memory/progress.md",
        "header": "# Project Progress\n\nMoonshine maintains this file for milestones and current progress.\n",
    },
]


BUILTIN_SKILLS = [
    {
        "slug": "math-research-loop",
        "title": "Math Research Loop",
        "description": "Break research work into assumptions, lemmas, obstacles, and next actions.",
        "content": """# Skill: Math Research Loop

## Purpose
- Keep mathematical research conversations structured.
- Separate conjectures, verified statements, and next experiments.

## Checklist
- Restate the problem precisely.
- List known assumptions and notation.
- Separate proven facts from hypotheses.
- End with the next concrete research step.
""",
    },
    {
        "slug": "memory-hygiene",
        "title": "Memory Hygiene",
        "description": "Promote durable facts and avoid storing noisy, one-off details.",
        "content": """# Skill: Memory Hygiene

## Purpose
- Keep dynamic memory concise and useful.
- Prefer summaries with traceable sources.

## Checklist
- Store only information likely to matter later.
- Add project scope when relevant.
- Use the knowledge layer for stable conclusions.
- Prefer summaries over raw transcript dumps.
""",
    },
]


@dataclass(frozen=True)
class MoonshinePaths:
    """Canonical runtime layout for Moonshine."""

    home: Path

    @property
    def config_dir(self) -> Path:
        return self.home / "config"

    @property
    def config_file(self) -> Path:
        return self.config_dir / "settings.json"

    @property
    def credentials_file(self) -> Path:
        return self.config_dir / "credentials.json"

    @property
    def core_config_file(self) -> Path:
        return self.home / "config.yaml"

    @property
    def memory_dir(self) -> Path:
        return self.home / "memory"

    @property
    def global_rules_file(self) -> Path:
        return self.home / "CLAUDE.md"

    @property
    def global_agents_file(self) -> Path:
        return self.home / "AGENTS.md"

    @property
    def legacy_global_rules_file(self) -> Path:
        return self.memory_dir / "AGENT.md"

    @property
    def memory_index_file(self) -> Path:
        return self.memory_dir / "MEMORY.md"

    @property
    def memory_audit_log(self) -> Path:
        return self.memory_dir / "audit" / "events.jsonl"

    @property
    def knowledge_dir(self) -> Path:
        return self.home / "knowledge"

    @property
    def knowledge_db(self) -> Path:
        return self.knowledge_dir / "conclusions.sqlite3"

    @property
    def knowledge_entries_dir(self) -> Path:
        return self.knowledge_dir / "entries"

    @property
    def knowledge_vector_dir(self) -> Path:
        return self.knowledge_dir / "vectors"

    @property
    def knowledge_vector_sqlite_db(self) -> Path:
        return self.knowledge_vector_dir / "knowledge_vectors.sqlite3"

    @property
    def knowledge_lancedb_dir(self) -> Path:
        return self.knowledge_vector_dir / "lancedb"

    @property
    def knowledge_chromadb_dir(self) -> Path:
        return self.knowledge_vector_dir / "chromadb"

    @property
    def knowledge_index_file(self) -> Path:
        return self.knowledge_dir / "KNOWLEDGE.md"

    @property
    def knowledge_audit_log(self) -> Path:
        return self.knowledge_dir / "audit" / "events.jsonl"

    @property
    def databases_dir(self) -> Path:
        return self.home / "databases"

    @property
    def sessions_db(self) -> Path:
        return self.databases_dir / "sessions.sqlite3"

    @property
    def sessions_dir(self) -> Path:
        return self.home / "sessions"

    @property
    def skills_dir(self) -> Path:
        return self.home / "skills"

    @property
    def agents_dir(self) -> Path:
        return self.home / "agents"

    @property
    def builtin_agents_dir(self) -> Path:
        return self.agents_dir / "builtin"

    @property
    def installed_agents_dir(self) -> Path:
        return self.agents_dir / "installed"

    @property
    def agents_registry_file(self) -> Path:
        return self.agents_dir / "registry.json"

    @property
    def agents_audit_log(self) -> Path:
        return self.agents_dir / "audit" / "events.jsonl"

    @property
    def tools_dir(self) -> Path:
        return self.home / "tools"

    @property
    def tool_definitions_dir(self) -> Path:
        return self.tools_dir / "definitions"

    @property
    def mcp_dir(self) -> Path:
        return self.tools_dir / "mcp"

    @property
    def mcp_servers_dir(self) -> Path:
        return self.mcp_dir / "servers"

    @property
    def builtin_skills_dir(self) -> Path:
        return self.skills_dir / "builtin"

    @property
    def installed_skills_dir(self) -> Path:
        return self.skills_dir / "installed"

    @property
    def skills_registry_file(self) -> Path:
        return self.skills_dir / "registry.json"

    @property
    def skills_audit_log(self) -> Path:
        return self.skills_dir / "audit" / "events.jsonl"

    @property
    def projects_dir(self) -> Path:
        return self.home / "projects"

    def project_dir(self, project_slug: str) -> Path:
        return self.projects_dir / project_slug

    def project_rules_file(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "rules.md"

    def project_agents_file(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "AGENTS.md"

    def project_workspace_dir(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "workspace"

    def project_problem_draft_file(self, project_slug: str) -> Path:
        return self.project_workspace_dir(project_slug) / "problem.md"

    def project_blueprint_file(self, project_slug: str) -> Path:
        return self.project_workspace_dir(project_slug) / "blueprint.md"

    def project_scratchpad_file(self, project_slug: str) -> Path:
        return self.project_workspace_dir(project_slug) / "scratchpad.md"

    def project_blueprint_verified_file(self, project_slug: str) -> Path:
        return self.project_workspace_dir(project_slug) / "blueprint_verified.md"

    def project_references_dir(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "references"

    def project_reference_papers_dir(self, project_slug: str) -> Path:
        return self.project_references_dir(project_slug) / "papers"

    def project_reference_notes_dir(self, project_slug: str) -> Path:
        return self.project_references_dir(project_slug) / "notes"

    def project_reference_surveys_dir(self, project_slug: str) -> Path:
        return self.project_references_dir(project_slug) / "surveys"

    def project_reference_index_file(self, project_slug: str) -> Path:
        return self.project_references_dir(project_slug) / "index.jsonl"

    def project_research_workflow_file(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "memory" / "research_workflow.json"

    def project_research_runtime_state_file(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "memory" / "research_state.json"

    def project_research_ledger_file(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "memory" / "ledger.jsonl"

    def project_research_index_file(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "memory" / "research_index.sqlite"

    def project_research_log_file(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "memory" / "research_log.jsonl"

    def project_research_log_summaries_file(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "memory" / "research_log_summaries.jsonl"

    def project_research_log_markdown_file(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "memory" / "research_log.md"

    def project_research_log_index_file(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "memory" / "research_log_index.sqlite"

    def project_research_log_by_type_dir(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "memory" / "by_type"

    def project_research_log_type_file(self, project_slug: str, record_type: str) -> Path:
        safe_type = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(record_type or "research_note"))
        return self.project_research_log_by_type_dir(project_slug) / ("%s.md" % (safe_type or "research_note"))

    def project_research_verification_file(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "memory" / "verification.jsonl"

    def project_research_archive_dir(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "memory" / "archive"

    def project_research_checkpoints_file(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "memory" / "research_checkpoints.jsonl"

    def project_research_state_dir(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "memory" / "research_state"

    def project_research_records_file(self, project_slug: str) -> Path:
        return self.project_research_state_dir(project_slug) / "records.jsonl"

    def project_research_artifacts_dir(self, project_slug: str) -> Path:
        return self.project_research_state_dir(project_slug) / "artifacts"

    def project_research_channels_dir(self, project_slug: str) -> Path:
        return self.project_dir(project_slug) / "memory" / "channels"

    def project_research_channel_file(self, project_slug: str, channel: str) -> Path:
        return self.project_research_channels_dir(project_slug) / ("%s.jsonl" % channel)

    def session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def session_meta_file(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "session.json"

    def session_messages_file(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "messages.jsonl"

    def session_transcript_file(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "transcript.md"

    def session_tool_events_file(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "tool_events.jsonl"

    def session_turn_events_file(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "turn_events.jsonl"

    def session_provider_rounds_file(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "provider_rounds.jsonl"

    def session_provider_round_archives_dir(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "turns"

    def session_provider_trace_file(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "provider_trace.md"

    def session_artifacts_dir(self, session_id: str) -> Path:
        return self.session_dir(session_id) / "artifacts"

    def session_context_summaries_file(self, session_id: str) -> Path:
        return self.session_artifacts_dir(session_id) / "context_summaries.jsonl"


def default_config() -> Dict[str, object]:
    """Return a mutable copy of the default config."""
    return {
        "default_mode": DEFAULT_CONFIG["default_mode"],
        "default_project": DEFAULT_CONFIG["default_project"],
        "provider": dict(DEFAULT_CONFIG["provider"]),
        "verification_provider": dict(DEFAULT_CONFIG["verification_provider"]),
        "agent": dict(DEFAULT_CONFIG["agent"]),
        "memory": dict(DEFAULT_CONFIG["memory"]),
        "context": dict(DEFAULT_CONFIG["context"]),
    }


def general_specs() -> List[Dict[str, str]]:
    """Return general memory file specs."""
    return [dict(item) for item in GENERAL_MEMORY_SPECS]


def project_specs() -> List[Dict[str, str]]:
    """Return project memory file specs."""
    return [dict(item) for item in PROJECT_MEMORY_SPECS]


def normalize_research_channel_name(channel: str) -> str:
    """Return the canonical channel name for research-memory retrieval."""
    name = str(channel or "").strip()
    if not name:
        return ""
    return name if name in RESEARCH_MEMORY_CHANNELS else ""


def expand_research_channel_aliases(channel: str) -> List[str]:
    """Return all file-backed aliases that should be searched for one channel."""
    name = str(channel or "").strip()
    if not name:
        return []
    aliases = RESEARCH_CHANNEL_ALIASES.get(name)
    if aliases is not None:
        return list(aliases)
    canonical = normalize_research_channel_name(name)
    if not canonical:
        return []
    return list(RESEARCH_CHANNEL_ALIASES.get(canonical, [canonical]))


def package_root() -> Path:
    """Return the Moonshine package root."""
    return Path(__file__).resolve().parent


def packaged_assets_dir() -> Path:
    """Return the packaged assets directory."""
    return package_root() / "assets"


def packaged_builtin_skills_dir() -> Path:
    """Return the packaged builtin skill directory."""
    return packaged_assets_dir() / "skills" / "builtin"


def packaged_builtin_agents_dir() -> Path:
    """Return the packaged builtin agent directory."""
    return packaged_assets_dir() / "agents" / "builtin"


def packaged_tool_definitions_dir() -> Path:
    """Return the packaged tool-definition directory."""
    return packaged_assets_dir() / "tools" / "definitions"


def packaged_mcp_servers_dir() -> Path:
    """Return the packaged MCP server-definition directory."""
    return packaged_assets_dir() / "tools" / "mcp" / "servers"


def resolve_memory_spec(alias: str, project_slug: Optional[str] = None) -> Dict[str, str]:
    """Resolve a memory file spec by alias."""
    for spec in GENERAL_MEMORY_SPECS:
        if spec["alias"] == alias:
            return dict(spec)
    for spec in PROJECT_MEMORY_SPECS:
        if spec["alias"] == alias:
            if not project_slug:
                raise ValueError("alias '%s' requires a project slug" % alias)
            resolved = dict(spec)
            resolved["relative_path"] = resolved["relative_path"].format(project_slug=project_slug)
            return resolved
    raise KeyError(alias)


def alias_from_relative_path(relative_path: str) -> Dict[str, Optional[str]]:
    """Map a runtime-relative path to alias information."""
    normalized = relative_path.replace("\\", "/")
    for spec in GENERAL_MEMORY_SPECS:
        if spec["relative_path"] == normalized:
            return {
                "alias": spec["alias"],
                "project_slug": None,
                "group": spec["group"],
                "label": spec["label"],
            }

    parts = normalized.split("/")
    if len(parts) == 4 and parts[0] == "projects" and parts[2] == "memory":
        project_slug = parts[1]
        for spec in PROJECT_MEMORY_SPECS:
            expected = spec["relative_path"].format(project_slug=project_slug)
            if expected == normalized:
                return {
                    "alias": spec["alias"],
                    "project_slug": project_slug,
                    "group": spec["group"],
                    "label": spec["label"],
                }

    return {"alias": None, "project_slug": None, "group": None, "label": None}
