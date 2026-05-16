"""High-level application facade for Moonshine."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from moonshine.agents.manager import AgentManager
from moonshine.agent_runtime.model_metadata import DEFAULT_CONTEXT_WINDOW_TOKENS, resolve_model_context_window
from moonshine.agent_runtime.context_manager import ContextManager
from moonshine.agent_runtime.memory_manager import MemoryManager
from moonshine.agent_runtime.research_mode import PENDING_RESEARCH_PROJECT, ResearchProjectResolver
from moonshine.credentials import credential_source, get_credential, load_credentials_into_environment, set_credential
from moonshine.moonshine_cli.commands import execute_slash_command
from moonshine.moonshine_cli.config import ensure_project_layout, ensure_runtime_home, load_config, resolve_home, save_config
from moonshine.moonshine_cli.runtime_provider import resolve_runtime_provider, resolve_verification_provider
from moonshine.moonshine_constants import MoonshinePaths
from moonshine.run_agent import AIAgent, AgentEvent
from moonshine.skills.manager import SkillManager
from moonshine.storage.session_store import SessionStore
from moonshine.tools.manager import ToolManager
from moonshine.utils import atomic_write, utc_now


TAVILY_API_KEY_URL = "https://app.tavily.com/"


def render_tavily_mcp_descriptor(*, enabled: bool) -> str:
    """Render the runtime Tavily MCP descriptor without embedding secrets."""
    metadata = {
        "slug": "tavily",
        "title": "Tavily MCP Server",
        "description": "Runtime Tavily MCP server for real-time web search and page extraction.",
        "transport": "stdio",
        "enabled": bool(enabled),
        "command": "npx",
        "args": ["-y", "tavily-mcp@0.1.3"],
        "env": {
            "TAVILY_API_KEY": "${TAVILY_API_KEY}",
        },
        "discover_tools": True,
        "tool_hints": [
            {
                "name": "tavily-search",
                "description": "Search the live web with Tavily for up-to-date information.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query."},
                        "topic": {"type": "string", "description": "Search topic such as general or news."},
                        "search_depth": {"type": "string", "description": "Search depth such as basic or advanced."},
                        "max_results": {"type": "integer", "description": "Maximum number of results to return."},
                        "include_answer": {"type": "boolean", "description": "Whether to request Tavily's direct answer summary."},
                        "include_raw_content": {"type": "boolean", "description": "Whether to include raw page content."},
                        "include_images": {"type": "boolean", "description": "Whether to include images."},
                        "include_favicon": {"type": "boolean", "description": "Whether to include favicons."},
                        "include_domains": {"type": "array", "items": {"type": "string"}, "description": "Restrict search to these domains."},
                        "exclude_domains": {"type": "array", "items": {"type": "string"}, "description": "Exclude these domains."},
                        "time_range": {"type": "string", "description": "Optional time range filter."},
                        "days": {"type": "integer", "description": "Limit news recency to the last N days."},
                    },
                    "required": ["query"],
                    "additionalProperties": True,
                },
            },
            {
                "name": "tavily-extract",
                "description": "Extract clean content from one or more URLs with Tavily.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "urls": {
                            "oneOf": [
                                {"type": "string"},
                                {"type": "array", "items": {"type": "string"}},
                            ],
                            "description": "One URL or a list of URLs to extract.",
                        },
                        "extract_depth": {"type": "string", "description": "Extraction depth such as basic or advanced."},
                        "format": {"type": "string", "description": "Output format such as markdown or text."},
                        "query": {"type": "string", "description": "Optional extraction focus query."},
                        "include_images": {"type": "boolean", "description": "Whether to include images."},
                        "include_favicon": {"type": "boolean", "description": "Whether to include favicons."},
                    },
                    "required": ["urls"],
                    "additionalProperties": True,
                },
            },
        ],
        "timeout_seconds": 60,
    }
    return """<!--
%s
-->
# Tavily MCP Server

## Purpose
- Connect Tavily as an external MCP server for real-time web search and page extraction.
- The API key is read from `TAVILY_API_KEY`, either from the process environment or from Moonshine's local credentials file.
- Get a personal Tavily API key from <https://app.tavily.com/>.

## Enable
1. Run `python -m moonshine mcp --set-tavily-key` and enter your own API key.
2. Restart Moonshine so it can register `mcp_tavily_tavily_search` and `mcp_tavily_tavily_extract`, then discover any extra Tavily MCP tools automatically.

## Safety
- Do not place a raw API key in this descriptor.
- Moonshine stores user-provided keys in `config/credentials.json`, which is created only after setup.
""" % json.dumps(metadata, ensure_ascii=False, indent=2)


@dataclass
class ShellState:
    """Mutable shell state."""

    mode: str
    project_slug: str
    session_id: str
    agent_slug: str
    auto_project_pending: bool = False
    research_autopilot_default_enabled: bool = True


class MoonshineApp(object):
    """Composition root for the Moonshine runtime."""

    def __init__(self, home: Optional[str] = None):
        self.paths = MoonshinePaths(resolve_home(home))
        ensure_runtime_home(self.paths)
        load_credentials_into_environment(self.paths)
        self.config = load_config(self.paths)
        self.session_store = SessionStore(self.paths)
        self.agent_manager = AgentManager(self.paths)
        self.skill_manager = SkillManager(self.paths)
        self.skill_store = self.skill_manager.store
        self.provider = resolve_runtime_provider(self.config)
        self.verification_provider = resolve_verification_provider(self.config, fallback_provider=self.provider)
        self.research_project_resolver = ResearchProjectResolver(paths=self.paths, provider=self.provider)
        self.memory = MemoryManager(
            self.paths,
            self.config,
            self.session_store,
            provider=self.provider,
            skill_manager=self.skill_manager,
        )
        self.tool_manager = ToolManager(paths=self.paths)
        self.tool_registry = self.tool_manager.registry
        self.context_manager = ContextManager(
            paths=self.paths,
            config=self.config,
            provider=self.provider,
            memory_manager=self.memory,
            session_store=self.session_store,
            tool_manager=self.tool_manager,
        )
        self.agent = AIAgent(
            config=self.config,
            paths=self.paths,
            provider=self.provider,
            verification_provider=self.verification_provider,
            memory_manager=self.memory,
            session_store=self.session_store,
            agent_manager=self.agent_manager,
            skill_manager=self.skill_manager,
            tool_manager=self.tool_manager,
            context_manager=self.context_manager,
        )
        self.ensure_project(self.config.default_project)

    def _refresh_runtime_providers(self) -> None:
        """Re-resolve provider instances after config changes."""
        self.provider = resolve_runtime_provider(self.config)
        self.verification_provider = resolve_verification_provider(self.config, fallback_provider=self.provider)
        self.research_project_resolver.provider = self.provider
        self.memory.set_provider(self.provider)
        self.context_manager.provider = self.provider
        self.agent.provider = self.provider
        self.agent.verification_provider = self.verification_provider
        self.agent.research_workflow.provider = self.provider

    def startup_notices(self) -> list:
        """Return non-blocking startup notices for optional integrations."""
        notices = []
        tavily = self.tool_manager.get_mcp_server("tavily")
        if tavily is None:
            return notices
        key_source = credential_source(self.paths, "TAVILY_API_KEY")
        has_key = bool(key_source)
        if not has_key:
            notices.append(
                "[setup] Tavily web search is disabled because TAVILY_API_KEY is not set. "
                "If you need web search, get your own API key from %s, then run "
                "`python -m moonshine mcp --set-tavily-key` before starting Moonshine."
                % TAVILY_API_KEY_URL
            )
        elif not tavily.enabled:
            notices.append(
                "[setup] Tavily API key is configured in %s, but Tavily MCP is disabled in %s. "
                "Run `python -m moonshine mcp --enable-tavily` if you want Moonshine to use Tavily web search."
                % (key_source, tavily.path)
            )
        return notices

    def configure_tavily_api_key(self, api_key: str, *, enable: bool = True) -> dict:
        """Store the user's Tavily key in the runtime credential file."""
        set_credential(self.paths, "TAVILY_API_KEY", api_key)
        if enable:
            atomic_write(self.paths.mcp_servers_dir / "tavily.md", render_tavily_mcp_descriptor(enabled=True))
        return {
            "credential_file": str(self.paths.credentials_file),
            "descriptor_file": str(self.paths.mcp_servers_dir / "tavily.md"),
            "enabled": bool(enable),
        }

    def set_tavily_enabled(self, enabled: bool) -> dict:
        """Enable or disable the runtime Tavily MCP descriptor."""
        atomic_write(self.paths.mcp_servers_dir / "tavily.md", render_tavily_mcp_descriptor(enabled=enabled))
        return {
            "descriptor_file": str(self.paths.mcp_servers_dir / "tavily.md"),
            "enabled": bool(enabled),
            "has_key": bool(get_credential(self.paths, "TAVILY_API_KEY")),
        }

    def configure_azure_openai(
        self,
        *,
        endpoint: str,
        deployment: str,
        api_version: str = "2024-12-01-preview",
        api_key: str = "",
        api_key_env: str = "AZURE_OPENAI_API_KEY",
        stream: bool = True,
        temperature: Optional[float] = None,
        timeout_seconds: Optional[int] = None,
        max_context_tokens: Optional[int] = None,
        target: str = "main",
    ) -> dict:
        """Configure Azure OpenAI as the main or verification provider."""
        endpoint = str(endpoint or "").strip().rstrip("/")
        deployment = str(deployment or "").strip()
        api_version = str(api_version or "2024-12-01-preview").strip()
        api_key_env = str(api_key_env or "AZURE_OPENAI_API_KEY").strip()
        target = str(target or "main").strip().lower()
        if not endpoint:
            raise ValueError("Azure OpenAI endpoint cannot be empty.")
        if not deployment:
            raise ValueError("Azure OpenAI deployment cannot be empty.")
        if not api_key_env:
            raise ValueError("Azure OpenAI api_key_env cannot be empty.")
        if target not in {"main", "verification"}:
            raise ValueError("Provider target must be 'main' or 'verification'.")
        if api_key:
            set_credential(self.paths, api_key_env, api_key)
            load_credentials_into_environment(self.paths)
        provider_config = self.config.provider if target == "main" else self.config.verification_provider
        provider_config.type = "azure_openai"
        provider_config.model = deployment
        provider_config.base_url = endpoint
        provider_config.api_key_env = api_key_env
        provider_config.api_version = api_version
        provider_config.stream = bool(stream)
        provider_config.temperature = temperature
        if target == "verification":
            provider_config.inherit_from_main = False
        if timeout_seconds is not None:
            provider_config.timeout_seconds = int(timeout_seconds)
        configured_context = DEFAULT_CONTEXT_WINDOW_TOKENS if max_context_tokens is None else max_context_tokens
        provider_config.max_context_tokens = resolve_model_context_window(
            deployment,
            configured=configured_context,
        )
        save_config(self.paths, self.config)
        self._refresh_runtime_providers()
        return {
            "target": target,
            "provider_type": provider_config.type,
            "endpoint": provider_config.base_url,
            "deployment": provider_config.model,
            "api_version": provider_config.api_version,
            "api_key_env": provider_config.api_key_env,
            "credential_file": str(self.paths.credentials_file),
            "config_file": str(self.paths.config_file),
        }

    def configure_openai_compatible(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str = "",
        api_key_env: str = "OPENAI_API_KEY",
        stream: bool = True,
        temperature: Optional[float] = None,
        timeout_seconds: Optional[int] = None,
        max_context_tokens: Optional[int] = None,
        target: str = "main",
    ) -> dict:
        """Configure an OpenAI-compatible chat-completions provider."""
        base_url = str(base_url or "").strip().rstrip("/")
        model = str(model or "").strip()
        api_key_env = str(api_key_env or "OPENAI_API_KEY").strip()
        target = str(target or "main").strip().lower()
        if not base_url:
            raise ValueError("OpenAI-compatible base_url cannot be empty.")
        if not model:
            raise ValueError("OpenAI-compatible model cannot be empty.")
        if not api_key_env:
            raise ValueError("OpenAI-compatible api_key_env cannot be empty.")
        if target not in {"main", "verification"}:
            raise ValueError("Provider target must be 'main' or 'verification'.")
        if api_key:
            set_credential(self.paths, api_key_env, api_key)
            load_credentials_into_environment(self.paths)
        provider_config = self.config.provider if target == "main" else self.config.verification_provider
        provider_config.type = "openai_compatible"
        provider_config.model = model
        provider_config.base_url = base_url
        provider_config.api_key_env = api_key_env
        provider_config.api_version = ""
        provider_config.stream = bool(stream)
        provider_config.temperature = temperature
        if target == "verification":
            provider_config.inherit_from_main = False
        if timeout_seconds is not None:
            provider_config.timeout_seconds = int(timeout_seconds)
        configured_context = DEFAULT_CONTEXT_WINDOW_TOKENS if max_context_tokens is None else max_context_tokens
        provider_config.max_context_tokens = resolve_model_context_window(
            model,
            configured=configured_context,
        )
        save_config(self.paths, self.config)
        self._refresh_runtime_providers()
        return {
            "target": target,
            "provider_type": provider_config.type,
            "base_url": provider_config.base_url,
            "model": provider_config.model,
            "api_key_env": provider_config.api_key_env,
            "credential_file": str(self.paths.credentials_file),
            "config_file": str(self.paths.config_file),
        }

    def set_verification_provider_inherit_main(self, inherit_from_main: bool = True) -> dict:
        """Switch the verification provider between inherited and dedicated modes."""
        self.config.verification_provider.inherit_from_main = bool(inherit_from_main)
        save_config(self.paths, self.config)
        self._refresh_runtime_providers()
        return {
            "inherit_from_main": bool(self.config.verification_provider.inherit_from_main),
            "config_file": str(self.paths.config_file),
        }

    def update_provider_config(
        self,
        *,
        target: str = "main",
        provider_type: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_version: Optional[str] = None,
        api_key: str = "",
        api_key_env: Optional[str] = None,
        stream: Optional[bool] = None,
        temperature: Optional[float] = None,
        temperature_specified: bool = False,
        timeout_seconds: Optional[int] = None,
        max_context_tokens: Optional[int] = None,
        inherit_from_main: Optional[bool] = None,
    ) -> dict:
        """Update one provider configuration incrementally."""
        target = str(target or "main").strip().lower()
        if target not in {"main", "verification"}:
            raise ValueError("Provider target must be 'main' or 'verification'.")
        provider_config = self.config.provider if target == "main" else self.config.verification_provider
        if inherit_from_main is not None:
            if target != "verification":
                raise ValueError("inherit_from_main applies only to the verification provider.")
            provider_config.inherit_from_main = bool(inherit_from_main)
        if provider_type is not None:
            normalized = str(provider_type or "").strip().lower()
            if not normalized:
                raise ValueError("Provider type cannot be empty.")
            provider_config.type = normalized
            if target == "verification":
                provider_config.inherit_from_main = False
        if base_url is not None:
            provider_config.base_url = str(base_url or "").strip().rstrip("/")
        if model is not None:
            provider_config.model = str(model or "").strip()
        if api_version is not None:
            provider_config.api_version = str(api_version or "").strip()
        if api_key_env is not None:
            provider_config.api_key_env = str(api_key_env or "").strip()
        if stream is not None:
            provider_config.stream = bool(stream)
        if temperature_specified:
            provider_config.temperature = temperature
        if timeout_seconds is not None:
            provider_config.timeout_seconds = int(timeout_seconds)
        if max_context_tokens is not None:
            provider_config.max_context_tokens = resolve_model_context_window(
                provider_config.model,
                configured=max_context_tokens,
            )
        if api_key:
            key_name = str(provider_config.api_key_env or "").strip()
            if not key_name:
                raise ValueError("api_key_env must be set before storing a provider API key.")
            set_credential(self.paths, key_name, api_key)
            load_credentials_into_environment(self.paths)
        if provider_config.type in {"openai_compatible", "openai_chat", "chat_completions"}:
            provider_config.api_version = ""
        save_config(self.paths, self.config)
        self._refresh_runtime_providers()
        return {
            "target": target,
            "provider_type": str(provider_config.type or ""),
            "base_url": str(provider_config.base_url or ""),
            "model": str(provider_config.model or ""),
            "api_version": str(provider_config.api_version or ""),
            "api_key_env": str(provider_config.api_key_env or ""),
            "stream": bool(provider_config.stream),
            "temperature": provider_config.temperature,
            "timeout_seconds": int(provider_config.timeout_seconds),
            "max_context_tokens": int(provider_config.max_context_tokens),
            "inherit_from_main": bool(getattr(provider_config, "inherit_from_main", False)) if target == "verification" else False,
            "credential_file": str(self.paths.credentials_file),
            "config_file": str(self.paths.config_file),
        }

    def ensure_project(self, project_slug: str) -> None:
        """Ensure a project exists."""
        ensure_project_layout(self.paths, project_slug)
        self.memory.ensure_project(project_slug)

    def start_shell_state(
        self,
        mode: Optional[str] = None,
        project_slug: Optional[str] = None,
        agent_slug: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> ShellState:
        """Create a new shell state or resume an existing session."""
        if session_id:
            session_meta = self.session_store.get_session_meta(str(session_id))
            if not session_meta or not session_meta.get("id"):
                raise ValueError("Session not found: %s" % session_id)

            resolved_mode = str(session_meta.get("mode") or mode or self.config.default_mode).strip() or self.config.default_mode
            requested_mode = str(mode or "").strip()
            if requested_mode and requested_mode != resolved_mode:
                raise ValueError("Session %s uses mode=%s, not %s." % (session_id, resolved_mode, requested_mode))

            resolved_project = str(session_meta.get("project_slug") or project_slug or self.config.default_project).strip()
            requested_project = project_slug if project_slug is not None else None
            if requested_project is not None and str(requested_project).strip() != resolved_project:
                raise ValueError("Session %s uses project=%s, not %s." % (session_id, resolved_project, requested_project))

            auto_project_pending = resolved_mode == "research" and resolved_project == PENDING_RESEARCH_PROJECT
            default_agent = "research-control-loop" if resolved_mode == "research" else self.agent_manager.default_slug
            stored_agent = str(session_meta.get("agent_slug") or "").strip()
            requested_agent = str(agent_slug or "").strip()
            if requested_agent and stored_agent and requested_agent != stored_agent:
                raise ValueError("Session %s uses agent=%s, not %s." % (session_id, stored_agent, requested_agent))
            resolved_agent = stored_agent or requested_agent or default_agent
            if self.agent_manager.get_agent(resolved_agent) is None:
                raise ValueError("Agent not found: %s" % resolved_agent)

            if not auto_project_pending:
                self.ensure_project(resolved_project)
            updated_at = utc_now()
            self.session_store.update_session_meta(
                str(session_id),
                mode=resolved_mode,
                project_slug=resolved_project,
                agent_slug=resolved_agent,
                updated_at=updated_at,
                status="active",
            )
            self.session_store.db.update_session(str(session_id), updated_at=updated_at, status="active")
            return ShellState(
                mode=resolved_mode,
                project_slug=resolved_project,
                session_id=str(session_id),
                agent_slug=resolved_agent,
                auto_project_pending=auto_project_pending,
            )

        resolved_mode = mode or self.config.default_mode
        auto_project_pending = resolved_mode == "research" and not project_slug
        resolved_project = PENDING_RESEARCH_PROJECT if auto_project_pending else (project_slug or self.config.default_project)
        default_agent = "research-control-loop" if resolved_mode == "research" else self.agent_manager.default_slug
        resolved_agent = str(agent_slug or default_agent).strip()
        if self.agent_manager.get_agent(resolved_agent) is None:
            raise ValueError("Agent not found: %s" % resolved_agent)
        if not auto_project_pending:
            self.ensure_project(resolved_project)
        session_id = self.session_store.create_session(resolved_mode, resolved_project, resolved_agent)
        return ShellState(
            mode=resolved_mode,
            project_slug=resolved_project,
            session_id=session_id,
            agent_slug=resolved_agent,
            auto_project_pending=auto_project_pending,
        )

    def _finalize_research_project(self, state: ShellState, project_slug: str, resolution: dict) -> dict:
        """Bind a pending research session to a concrete project."""
        self.ensure_project(project_slug)
        state.project_slug = project_slug
        state.auto_project_pending = False
        self.session_store.rebind_session_project(state.session_id, project_slug)
        self.memory.write_manual_entry(
            alias="project-context",
            title="Research Project Initialization",
            summary=str(resolution.get("summary") or resolution.get("title") or project_slug),
            body=(
                "Research project: %s\n\nSummary: %s\n\nTags: %s"
                % (
                    str(resolution.get("title") or project_slug),
                    str(resolution.get("summary") or ""),
                    ", ".join(str(tag) for tag in list(resolution.get("tags") or [])),
                )
            ).strip(),
            project_slug=project_slug,
            source="research-project-resolver",
            session_id=state.session_id,
            source_message_role="system",
            source_excerpt=str(resolution.get("summary") or ""),
        )
        return {"status": "ready", "project_slug": project_slug}

    def prepare_research_project(self, user_message: str, state: ShellState, *, allow_user_choice: bool = False) -> dict:
        """Resolve or propose the project for a pending research-mode session."""
        if not state.auto_project_pending:
            return {"status": "ready", "project_slug": state.project_slug}
        resolution = self.research_project_resolver.resolve(user_message)
        payload = resolution.to_dict()
        candidates = list(payload.get("candidate_existing_projects") or [])
        if allow_user_choice and resolution.recommended_action == "ask_user" and candidates:
            return {
                "status": "needs_choice",
                "resolution": payload,
                "candidates": candidates,
                "new_project_slug": resolution.slug,
            }
        if resolution.recommended_action == "reuse_existing" and resolution.selected_project_slug:
            return self._finalize_research_project(state, resolution.selected_project_slug, payload)
        return self._finalize_research_project(state, resolution.slug, payload)

    def finalize_research_project_choice(self, state: ShellState, resolution: dict, selected_project_slug: str = "") -> dict:
        """Finalize a user-selected research project choice."""
        target = selected_project_slug or str(resolution.get("slug") or "").strip()
        if not target:
            target = str(resolution.get("new_project_slug") or "research_project")
        return self._finalize_research_project(state, target, resolution)

    def switch_project_session(self, state: ShellState, project_slug: str) -> dict:
        """Switch project scope and start a fresh session for the new project."""
        if project_slug == state.project_slug:
            return {
                "changed": False,
                "project_slug": state.project_slug,
                "session_id": state.session_id,
                "previous_session_id": state.session_id,
            }

        previous_session_id = state.session_id
        self.close_session(state)
        replacement = self.start_shell_state(mode=state.mode, project_slug=project_slug, agent_slug=state.agent_slug)
        state.project_slug = replacement.project_slug
        state.session_id = replacement.session_id
        state.mode = replacement.mode
        state.agent_slug = replacement.agent_slug
        return {
            "changed": True,
            "project_slug": state.project_slug,
            "session_id": state.session_id,
            "previous_session_id": previous_session_id,
        }

    def stage_input_file(self, source_path: str, *, project_slug: str = "") -> dict:
        """Make an external text input readable through read_runtime_file."""
        raw = str(source_path or "").strip()
        if not raw:
            raise ValueError("input file path cannot be empty")
        source = Path(raw).expanduser()
        if not source.is_absolute():
            source = (Path.cwd() / source).resolve()
        else:
            source = source.resolve()
        if not source.exists() or not source.is_file():
            raise ValueError("input file does not exist: %s" % source)

        home = self.paths.home.resolve()
        try:
            relative_path = source.relative_to(home).as_posix()
            return {
                "relative_path": relative_path,
                "runtime_path": str(source),
                "staged": False,
            }
        except ValueError:
            pass

        if project_slug:
            self.ensure_project(project_slug)
            target_dir = self.paths.project_reference_notes_dir(project_slug)
        else:
            target_dir = self.paths.home / "inputs"
            target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        suffix = 1
        while target.exists() and target.read_text(encoding="utf-8") != source.read_text(encoding="utf-8"):
            stem = source.stem
            extension = source.suffix
            target = target_dir / ("%s_%s%s" % (stem, suffix, extension))
            suffix += 1
        atomic_write(target, source.read_text(encoding="utf-8"))
        return {
            "relative_path": target.relative_to(home).as_posix(),
            "runtime_path": str(target),
            "staged": True,
        }

    def ask(self, user_message: str, state: ShellState) -> str:
        """Run one user turn."""
        if state.auto_project_pending:
            self.prepare_research_project(user_message, state, allow_user_choice=False)
        return self.agent.run_conversation(
            user_message=user_message,
            mode=state.mode,
            project_slug=state.project_slug,
            session_id=state.session_id,
            agent_slug=state.agent_slug,
        )

    def ask_stream(self, user_message: str, state: ShellState):
        """Stream one user turn as agent events."""
        if state.auto_project_pending:
            self.prepare_research_project(user_message, state, allow_user_choice=False)
        return self.agent.run_conversation_events(
            user_message=user_message,
            mode=state.mode,
            project_slug=state.project_slug,
            session_id=state.session_id,
            agent_slug=state.agent_slug,
        )

    def run_research_autopilot_events(self, initial_prompt: str, state: ShellState, *, max_iterations: int = 100):
        """Run Research Mode as repeated conversation turns until budget exhaustion."""
        if state.mode != "research":
            yield AgentEvent(
                type="status",
                text="Research autopilot requires mode=research; running one normal turn instead.",
                payload={"mode": state.mode},
            )
            for event in self.ask_stream(initial_prompt, state):
                yield event
            return

        if state.auto_project_pending:
            self.prepare_research_project(initial_prompt, state, allow_user_choice=False)

        iteration_limit = max(1, int(max_iterations or 1))
        next_prompt = initial_prompt
        previous_final_text = ""

        for iteration in range(1, iteration_limit + 1):
            yield AgentEvent(
                type="status",
                text="Research autopilot iteration %s/%s." % (iteration, iteration_limit),
                payload={"iteration": iteration, "max_iterations": iteration_limit},
            )
            final_verification_passed = False
            provider_offline = False
            final_text = ""
            for event in self.ask_stream(next_prompt, state):
                if event.type == "tool_result" and event.text == "verify_overall":
                    output = dict(event.payload.get("output") or {})
                    scope = str(output.get("scope") or "").strip().lower()
                    if bool(output.get("passed")) and scope == "final":
                        final_verification_passed = True
                elif event.type == "final":
                    final_text = str(event.text or "").strip()
                    reason = str(event.payload.get("reason") or "").strip()
                    if reason in {"provider_offline", "verification_provider_offline"}:
                        provider_offline = True
                yield event
            if provider_offline:
                yield AgentEvent(
                    type="status",
                    text="Research autopilot stopped because a required provider is offline or unavailable.",
                    payload={"iteration": iteration, "max_iterations": iteration_limit},
                )
                return
            if final_text:
                normalized_final_text = final_text.strip()
                if previous_final_text and normalized_final_text == previous_final_text:
                    yield AgentEvent(
                        type="status",
                        text="Research autopilot stopped because two consecutive iterations produced identical output.",
                        payload={"iteration": iteration, "max_iterations": iteration_limit},
                    )
                    return
                previous_final_text = normalized_final_text
            if final_verification_passed and final_text:
                yield AgentEvent(
                    type="status",
                    text="Research autopilot completed after final verification passed.",
                    payload={"iteration": iteration, "max_iterations": iteration_limit},
                )
                return
            next_prompt = (
                "Continue focusing on the current research progress and advance the next step directly from the multi-turn conversation above. "
                "Use the prior conversation, tool results, and current proof text as the live mathematical context. "
                "If you judge that you already have a complete candidate final result or proof blueprint, call `verify_overall` with `scope=\"final\"` for final review. "
                "If final review passes, give the final conclusion; otherwise continue repairing the argument."
            )
        yield AgentEvent(
            type="status",
            text="Research autopilot stopped after reaching the iteration budget.",
            payload={"max_iterations": iteration_limit},
        )

    def poll_memory_notifications(self, state: Optional[ShellState] = None):
        """Return completed background memory notifications."""
        session_id = state.session_id if state is not None else None
        return self.memory.collect_auto_extract_notifications(session_id=session_id)

    def execute_command(self, command_line: str, state: ShellState):
        """Execute a slash command or return None."""
        return execute_slash_command(self, command_line, state)

    def close_session(self, state: ShellState) -> None:
        """Mark a session as closed."""
        self.memory.wait_for_auto_extract_tasks(state.session_id, timeout_seconds=0.1)
        self.memory.extract_session_end(
            session_id=state.session_id,
            project_slug=state.project_slug,
        )
        self.memory.collect_auto_extract_notifications(session_id=state.session_id)
        self.session_store.mark_closed(state.session_id)
