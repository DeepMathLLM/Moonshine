"""Moonshine command-line interface."""

from __future__ import annotations

import argparse
import getpass
from dataclasses import dataclass
from typing import Optional

from moonshine.agent_runtime.model_metadata import resolve_model_context_window
from moonshine.agent_runtime.display import render_banner
from moonshine.app import MoonshineApp
from moonshine.moonshine_cli.dependencies import (
    install_runtime_dependencies,
    render_dependency_check_report,
    render_dependency_install_report,
)
from moonshine.run_agent import render_agent_events


@dataclass
class CLIResult:
    """Simple command result wrapper."""

    text: str
    exit_code: int = 0


class MoonshineCLI(object):
    """Interactive and batch CLI entry point."""

    def __init__(self, app: MoonshineApp):
        self.app = app

    def _print_memory_notifications(self, state) -> None:
        """Print completed background memory-update notifications."""
        for notification in self.app.poll_memory_notifications(state):
            print(notification)

    def _resolve_research_project_interactively(self, line: str, state) -> None:
        """Resolve a pending research project before the first research turn."""
        if not getattr(state, "auto_project_pending", False):
            return
        result = self.app.prepare_research_project(line, state, allow_user_choice=True)
        if result.get("status") != "needs_choice":
            print("[research] Using project: %s" % result.get("project_slug"))
            return

        resolution = dict(result.get("resolution") or {})
        candidates = list(result.get("candidates") or [])
        print("[research] Similar existing projects found.")
        for index, candidate in enumerate(candidates, start=1):
            print(
                "  %s. %s (confidence %.2f): %s"
                % (
                    index,
                    candidate.get("slug", ""),
                    float(candidate.get("confidence", 0.0) or 0.0),
                    candidate.get("reason", ""),
                )
            )
        print("  n. Create new project: %s" % result.get("new_project_slug"))
        choice = input("Choose an existing project number, or press Enter for new: ").strip().lower()
        selected_slug = ""
        if choice.isdigit():
            index = int(choice) - 1
            if 0 <= index < len(candidates):
                selected_slug = str(candidates[index].get("slug", ""))
        final = self.app.finalize_research_project_choice(state, resolution, selected_project_slug=selected_slug)
        print("[research] Using project: %s" % final.get("project_slug"))

    def _build_input_file_prompt(self, *, relative_path: str, state, user_prompt: str = "") -> str:
        """Build the initial prompt used when a command-line file is supplied."""
        base = "Read %s with read_runtime_file." % relative_path
        if state.mode == "research":
            project_text = state.project_slug if not getattr(state, "auto_project_pending", False) else "this research session"
            default_tail = (
                " Use it as the source material for %s. First understand the material, then continue the research workflow."
                % project_text
            )
        else:
            default_tail = " Use it as the main input for this conversation."
        tail = (" " + user_prompt.strip()) if user_prompt and user_prompt.strip() else default_tail
        return (base + tail).strip()

    def process_input(self, line: str, state, *, auto_run_research: bool = True, max_iterations: int = 100) -> str:
        """Handle one line of shell input."""
        result = self.app.execute_command(line, state)
        if result is not None:
            return result
        self._resolve_research_project_interactively(line, state)
        if state.mode == "research" and auto_run_research:
            render_agent_events(
                self.app.run_research_autopilot_events(
                    line,
                    state,
                    max_iterations=max_iterations,
                )
            )
        else:
            render_agent_events(self.app.ask_stream(line, state))
        return ""

    def run_shell(
        self,
        *,
        mode: str,
        project_slug: str,
        agent_slug: str = "",
        session_id: str = "",
        auto_run_research: bool = True,
        max_iterations: int = 100,
        input_file: str = "",
    ) -> CLIResult:
        """Run the interactive shell."""
        state = self.app.start_shell_state(mode=mode, project_slug=project_slug, agent_slug=agent_slug, session_id=session_id or None)
        print(render_banner(state.mode, state.project_slug, state.session_id, state.agent_slug))
        for notice in self.app.startup_notices():
            print(notice)
        if input_file:
            staged = self.app.stage_input_file(
                input_file,
                project_slug="" if getattr(state, "auto_project_pending", False) else state.project_slug,
            )
            print("[input] Using file: %s" % staged["relative_path"])
            render_agent_events(
                self.app.run_research_autopilot_events(
                    self._build_input_file_prompt(relative_path=staged["relative_path"], state=state),
                    state,
                    max_iterations=max_iterations,
                )
                if state.mode == "research" and auto_run_research
                else self.app.ask_stream(
                    self._build_input_file_prompt(relative_path=staged["relative_path"], state=state),
                    state,
                )
            )
        print("Type /help for commands. Type /exit to close the session.")
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                self.app.close_session(state)
                print()
                break
            if not line:
                continue
            self._print_memory_notifications(state)
            result = self.process_input(
                line,
                state,
                auto_run_research=auto_run_research,
                max_iterations=max_iterations,
            )
            self._print_memory_notifications(state)
            if result == "EXIT":
                self.app.close_session(state)
                break
            if result:
                print(result)
        return CLIResult("shell closed")


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level CLI parser."""
    parser = argparse.ArgumentParser(prog="moonshine")
    parser.add_argument("--home", dest="home", default=None, help="Moonshine runtime home")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Initialize the Moonshine runtime home")
    init_parser.add_argument(
        "--install-deps",
        action="store_true",
        help="Install all runtime dependencies declared by Moonshine's pyproject",
    )
    init_parser.add_argument(
        "--check-deps",
        action="store_true",
        help="Check whether runtime dependencies are importable without installing them",
    )

    ask_parser = subparsers.add_parser("ask", help="Send one prompt to Moonshine")
    ask_parser.add_argument("prompt", help="User prompt")
    ask_parser.add_argument("--mode", default=None)
    ask_parser.add_argument("--project", default=None)
    ask_parser.add_argument("--agent", default=None, help="Agent profile slug to use for this one-shot run.")
    ask_parser.add_argument("--session", default=None, help="Resume an existing session id and continue its full conversation history.")
    ask_parser.add_argument("--input-file", default=None, help="Optional text or markdown file to stage into Moonshine home and read as part of this one-shot turn.")
    ask_parser.add_argument(
        "--auto-run",
        action="store_true",
        help="Deprecated compatibility flag. Research ask runs autonomously by default.",
    )
    ask_parser.add_argument(
        "--interactive",
        action="store_true",
        help="In research mode, run only one turn and wait for the next user input.",
    )
    ask_parser.add_argument(
        "--no-auto-run",
        action="store_true",
        help="Alias for --interactive; disable default research autopilot for this prompt.",
    )
    ask_parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Maximum research autopilot iterations. Defaults to agent.research_max_iterations.",
    )

    shell_parser = subparsers.add_parser("shell", help="Start the interactive shell")
    shell_parser.add_argument("--mode", default=None)
    shell_parser.add_argument("--project", default=None)
    shell_parser.add_argument("--agent", default=None, help="Agent profile slug to use for this interactive session.")
    shell_parser.add_argument("--session", default=None, help="Resume an existing session id and continue its full conversation history.")
    shell_parser.add_argument("--input-file", default=None, help="Optional text or markdown file to stage into Moonshine home and read automatically before the interactive shell continues.")
    shell_parser.add_argument(
        "--auto-run",
        action="store_true",
        help="Deprecated compatibility flag. Research shell inputs run autonomously by default.",
    )
    shell_parser.add_argument(
        "--interactive",
        action="store_true",
        help="In research mode, run one turn per input instead of autonomous iteration.",
    )
    shell_parser.add_argument(
        "--no-auto-run",
        action="store_true",
        help="Alias for --interactive; disable default research autopilot in shell.",
    )
    shell_parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="Maximum research autopilot iterations. Defaults to agent.research_max_iterations.",
    )

    sessions_parser = subparsers.add_parser("sessions", help="List or search sessions")
    sessions_parser.add_argument("--search", default="")
    sessions_parser.add_argument("--project", default=None)

    memory_parser = subparsers.add_parser("memory", help="Inspect memory files")
    memory_parser.add_argument("--show", default="")
    memory_parser.add_argument("--project", default=None)

    knowledge_parser = subparsers.add_parser("knowledge", help="Inspect the knowledge layer")
    knowledge_parser.add_argument("--search", default="")
    knowledge_parser.add_argument("--project", default=None)

    subparsers.add_parser("skills", help="List available skills")
    agent_parser = subparsers.add_parser("agent", help="Inspect agent profiles")
    agent_parser.add_argument("--show", default="")
    subparsers.add_parser("tools", help="List registered tools")
    mcp_parser = subparsers.add_parser("mcp", help="Inspect MCP server descriptors")
    mcp_parser.add_argument("--show", default="")
    mcp_parser.add_argument(
        "--set-tavily-key",
        nargs="?",
        const="",
        default=None,
        metavar="KEY",
        help="Store your Tavily API key in Moonshine's local credentials file and enable Tavily MCP.",
    )
    mcp_parser.add_argument("--enable-tavily", action="store_true", help="Enable the Tavily MCP descriptor.")
    mcp_parser.add_argument("--disable-tavily", action="store_true", help="Disable the Tavily MCP descriptor.")

    provider_parser = subparsers.add_parser("provider", help="Configure or inspect the active LLM provider")
    provider_parser.add_argument("--show", action="store_true", help="Show the active provider configuration.")
    provider_parser.add_argument("--target", choices=["main", "verification"], default="main", help="Select which provider configuration to manage.")
    provider_parser.add_argument("--type", default=None, help="Set the provider type directly, for example offline, azure_openai, or openai_compatible.")
    provider_parser.add_argument("--azure-openai", action="store_true", help="Configure Azure OpenAI chat completions.")
    provider_parser.add_argument("--openai-compatible", action="store_true", help="Configure an OpenAI-compatible chat completions provider.")
    provider_parser.add_argument("--endpoint", default=None, help="Set the Azure OpenAI endpoint, for example https://name.openai.azure.com/.")
    provider_parser.add_argument("--base-url", default=None, help="Set the OpenAI-compatible base URL, for example https://api.openai.com/v1.")
    provider_parser.add_argument("--deployment", default=None, help="Set the Azure OpenAI deployment name.")
    provider_parser.add_argument("--model", default=None, help="Set the provider model name.")
    provider_parser.add_argument("--api-version", default=None, help="Set the Azure OpenAI REST API version.")
    provider_parser.add_argument("--api-key-env", default=None, help="Set the environment variable or credential name used for the provider API key.")
    provider_parser.add_argument(
        "--set-api-key",
        nargs="?",
        const="",
        default=None,
        metavar="KEY",
        help="Store the provider API key in Moonshine's local credentials file.",
    )
    provider_parser.add_argument("--stream", action="store_true", help="Enable provider streaming.")
    provider_parser.add_argument("--no-stream", action="store_true", help="Disable provider streaming.")
    provider_parser.add_argument("--temperature", type=float, default=None, help="Optional temperature. Omit for Azure GPT-5 deployments.")
    provider_parser.add_argument("--clear-temperature", action="store_true", help="Clear the configured temperature and let the provider default apply.")
    provider_parser.add_argument("--timeout-seconds", type=int, default=None)
    provider_parser.add_argument("--inherit-main", action="store_true", help="For verification provider only, inherit the main provider instead of using a dedicated one.")
    provider_parser.add_argument("--dedicated", action="store_true", help="For verification provider only, use a dedicated verification provider instead of inheriting the main provider.")
    provider_parser.add_argument(
        "--max-context-tokens",
        type=int,
        default=None,
        help="Override context window budgeting. Default is 258000; pass 0 to infer from model metadata.",
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)
    app = MoonshineApp(home=args.home)
    cli = MoonshineCLI(app)

    if args.command in {None, "shell"}:
        mode = getattr(args, "mode", None) or app.config.default_mode
        project_arg = getattr(args, "project", None)
        interactive = bool(getattr(args, "interactive", False))
        no_auto_run = bool(getattr(args, "no_auto_run", False))
        auto_run = bool(getattr(args, "auto_run", False))
        result = cli.run_shell(
            mode=mode,
            project_slug=project_arg if project_arg is not None else (None if mode == "research" else app.config.default_project),
            agent_slug=str(getattr(args, "agent", None) or ""),
            session_id=str(getattr(args, "session", None) or ""),
            auto_run_research=auto_run or (mode == "research" and not interactive and not no_auto_run),
            max_iterations=int(getattr(args, "max_iterations", None) or app.config.agent.research_max_iterations),
            input_file=str(getattr(args, "input_file", None) or ""),
        )
        return result.exit_code

    if args.command == "init":
        print("Initialized Moonshine home at %s" % app.paths.home)
        if args.check_deps:
            print(render_dependency_check_report())
        if args.install_deps:
            result = install_runtime_dependencies()
            print(render_dependency_install_report(result))
            if not result.success:
                return result.exit_code
        return 0

    if args.command == "ask":
        mode = args.mode or app.config.default_mode
        for notice in app.startup_notices():
            print(notice)
        state = app.start_shell_state(
            mode=mode,
            project_slug=args.project if args.project is not None else (None if mode == "research" else app.config.default_project),
            agent_slug=args.agent,
            session_id=args.session,
        )
        prompt_text = args.prompt
        if args.input_file:
            staged = app.stage_input_file(
                args.input_file,
                project_slug="" if getattr(state, "auto_project_pending", False) else state.project_slug,
            )
            prompt_text = cli._build_input_file_prompt(
                relative_path=staged["relative_path"],
                state=state,
                user_prompt=args.prompt,
            )
        should_auto_run = bool(args.auto_run) or (mode == "research" and not args.interactive and not args.no_auto_run)
        if should_auto_run:
            render_agent_events(
                app.run_research_autopilot_events(
                    prompt_text,
                    state,
                    max_iterations=int(args.max_iterations or app.config.agent.research_max_iterations),
                )
            )
        else:
            render_agent_events(app.ask_stream(prompt_text, state))
        for notification in app.poll_memory_notifications(state):
            print(notification)
        app.close_session(state)
        return 0

    if args.command == "sessions":
        if args.search:
            rows = app.session_store.search_messages(args.search, project_slug=args.project, limit=10)
            for item in rows:
                print("[%s] %s" % (item["role"], item["content"]))
        else:
            rows = app.session_store.list_sessions(limit=10)
            for item in rows:
                print("%s | %s | %s | %s" % (item["id"], item["mode"], item["project_slug"], item["title"] or "(untitled)"))
        return 0

    if args.command == "memory":
        project_slug = args.project or app.config.default_project
        if args.show:
            scope = project_slug if args.show.startswith("project-") else None
            print(app.memory.dynamic_store.read_file(args.show, project_slug=scope).strip())
        else:
            for item in app.memory.dynamic_store.list_memory_files(project_slug=project_slug):
                print("%s: %s" % (item["alias"], item["path"]))
        return 0

    if args.command == "knowledge":
        rows = app.memory.knowledge_store.search(args.search or "", project_slug=args.project, limit=10)
        for item in rows:
            print("%s: %s" % (item["title"], item["statement"]))
        return 0

    if args.command == "skills":
        registry = app.skill_manager.list_skills()
        for section in ("builtin", "installed"):
            print("%s:" % section)
            items = registry.get(section, [])
            if not items:
                print("- none")
                continue
            for item in items:
                print("- %s: %s" % (item["slug"], item["description"]))
        return 0

    if args.command == "agent":
        if args.show:
            agent = app.agent_manager.get_agent(args.show)
            if agent is None:
                print("Agent not found: %s" % args.show)
                return 1
            print(agent.body.strip() or ("%s: %s" % (agent.title, agent.description)))
            return 0
        print(app.agent_manager.build_prompt_summary())
        return 0

    if args.command == "tools":
        for item in app.tool_manager.list_tools():
            print("%s: %s" % (item.name, item.description))
        return 0

    if args.command == "mcp":
        if args.set_tavily_key is not None:
            api_key = args.set_tavily_key.strip() if args.set_tavily_key else ""
            if not api_key:
                api_key = getpass.getpass("TAVILY_API_KEY: ").strip()
            try:
                result = app.configure_tavily_api_key(api_key, enable=True)
            except ValueError as exc:
                print(str(exc))
                return 1
            print(
                "Stored Tavily API key in %s and enabled Tavily MCP descriptor %s. Restart Moonshine to discover tools."
                % (result["credential_file"], result["descriptor_file"])
            )
            return 0
        if args.enable_tavily:
            result = app.set_tavily_enabled(True)
            suffix = "" if result["has_key"] else " Set TAVILY_API_KEY first with `python -m moonshine mcp --set-tavily-key`."
            print("Enabled Tavily MCP descriptor %s.%s" % (result["descriptor_file"], suffix))
            return 0
        if args.disable_tavily:
            result = app.set_tavily_enabled(False)
            print("Disabled Tavily MCP descriptor %s." % result["descriptor_file"])
            return 0
        if args.show:
            server = app.tool_manager.get_mcp_server(args.show)
            if server is None:
                print("MCP server not found: %s" % args.show)
                return 1
            print(server.body.strip() or ("%s: %s" % (server.title, server.description)))
            return 0
        servers = app.tool_manager.list_mcp_servers(include_disabled=True)
        if not servers:
            print("No MCP server descriptors are configured.")
            return 0
        for item in servers:
            print("%s [%s]: %s" % (item["slug"], "enabled" if item["enabled"] else "disabled", item["description"] or item["title"]))
        return 0

    if args.command == "provider":
        update_requested = any(
            [
                bool(args.type),
                bool(args.azure_openai),
                bool(args.openai_compatible),
                args.endpoint is not None,
                args.base_url is not None,
                args.deployment is not None,
                args.model is not None,
                args.api_version is not None,
                args.api_key_env is not None,
                args.set_api_key is not None,
                bool(args.stream),
                bool(args.no_stream),
                args.temperature is not None,
                bool(args.clear_temperature),
                args.timeout_seconds is not None,
                args.max_context_tokens is not None,
                bool(args.inherit_main),
                bool(args.dedicated),
            ]
        )
        if args.show or not update_requested:
            provider = app.config.provider
            print("[main]")
            print("Provider: %s" % provider.type)
            print("Model/deployment: %s" % provider.model)
            print("Base URL/endpoint: %s" % provider.base_url)
            print("API key env: %s" % provider.api_key_env)
            if provider.api_version:
                print("API version: %s" % provider.api_version)
            print("Stream: %s" % str(provider.stream).lower())
            print(
                "Max context tokens: %s"
                % resolve_model_context_window(provider.model, configured=provider.max_context_tokens)
            )
            verification = app.config.verification_provider
            print("")
            print("[verification]")
            print("Inherit main provider: %s" % str(verification.inherit_from_main).lower())
            print("Provider: %s" % verification.type)
            print("Model/deployment: %s" % verification.model)
            print("Base URL/endpoint: %s" % verification.base_url)
            print("API key env: %s" % verification.api_key_env)
            if verification.api_version:
                print("API version: %s" % verification.api_version)
            print("Stream: %s" % str(verification.stream).lower())
            print(
                "Max context tokens: %s"
                % resolve_model_context_window(verification.model, configured=verification.max_context_tokens)
            )
            if not update_requested:
                return 0
        if args.azure_openai and args.openai_compatible:
            print("Choose only one provider family: --azure-openai or --openai-compatible.")
            return 1
        if args.inherit_main and args.dedicated:
            print("Choose only one verification mode: --inherit-main or --dedicated.")
            return 1
        if (args.inherit_main or args.dedicated) and args.target != "verification":
            print("--inherit-main and --dedicated apply only to --target verification.")
            return 1

        provider_type = args.type
        if args.azure_openai:
            provider_type = "azure_openai"
        if args.openai_compatible:
            provider_type = "openai_compatible"

        if args.endpoint is not None and args.base_url is not None and args.endpoint.strip().rstrip("/") != args.base_url.strip().rstrip("/"):
            print("--endpoint and --base-url should not disagree in the same command.")
            return 1
        if args.deployment is not None and args.model is not None and args.deployment.strip() != args.model.strip():
            print("--deployment and --model should not disagree in the same command.")
            return 1

        provider_config = app.config.provider if args.target == "main" else app.config.verification_provider
        inferred_api_key_env = args.api_key_env
        if inferred_api_key_env is None and provider_type:
            current_key_name = str(provider_config.api_key_env or "").strip()
            normalized_type = str(provider_type).strip().lower()
            if normalized_type in {"azure_openai", "azure", "azure_chat_completions"} and current_key_name in {"", "OPENAI_API_KEY"}:
                inferred_api_key_env = "AZURE_OPENAI_API_KEY"
            if normalized_type in {"openai_compatible", "openai_chat", "chat_completions"} and current_key_name in {"", "AZURE_OPENAI_API_KEY"}:
                inferred_api_key_env = "OPENAI_API_KEY"

        stream_value = True if args.stream else (False if args.no_stream else None)
        normalized_provider_type = str(provider_type or "").strip().lower()
        force_clear_temperature = (
            normalized_provider_type in {"azure_openai", "azure", "azure_chat_completions"}
            and args.temperature is None
            and not args.clear_temperature
        )
        temperature_specified = args.temperature is not None or args.clear_temperature or force_clear_temperature
        temperature_value = None if (args.clear_temperature or force_clear_temperature) else args.temperature
        inherit_from_main = True if args.inherit_main else (False if args.dedicated else None)
        base_url_value = args.base_url if args.base_url is not None else args.endpoint
        model_value = args.model if args.model is not None else args.deployment

        api_key = ""
        if args.set_api_key is not None:
            key_name_for_prompt = str(
                inferred_api_key_env
                or provider_config.api_key_env
                or ("AZURE_OPENAI_API_KEY" if str(provider_type or provider_config.type).strip().lower() in {"azure_openai", "azure", "azure_chat_completions"} else "OPENAI_API_KEY")
            ).strip()
            api_key = args.set_api_key.strip() if args.set_api_key else ""
            if not api_key:
                api_key = getpass.getpass("%s: " % key_name_for_prompt).strip()

        try:
            result = app.update_provider_config(
                target=args.target,
                provider_type=provider_type,
                base_url=base_url_value,
                model=model_value,
                api_version=args.api_version,
                api_key=api_key,
                api_key_env=inferred_api_key_env,
                stream=stream_value,
                temperature=temperature_value,
                temperature_specified=temperature_specified,
                timeout_seconds=args.timeout_seconds,
                max_context_tokens=args.max_context_tokens,
                inherit_from_main=inherit_from_main,
            )
        except ValueError as exc:
            print(str(exc))
            return 1

        print("[%s]" % result["target"])
        print("Provider: %s" % result["provider_type"])
        if args.target == "verification":
            print("Inherit main provider: %s" % str(result["inherit_from_main"]).lower())
        print("Model/deployment: %s" % result["model"])
        print("Base URL/endpoint: %s" % result["base_url"])
        print("API key env: %s" % result["api_key_env"])
        if result["api_version"]:
            print("API version: %s" % result["api_version"])
        print("Stream: %s" % str(result["stream"]).lower())
        print("Temperature: %s" % result["temperature"])
        print("Timeout seconds: %s" % result["timeout_seconds"])
        print("Max context tokens: %s" % resolve_model_context_window(result["model"], configured=result["max_context_tokens"]))
        print("Config: %s" % result["config_file"])
        if args.set_api_key is not None:
            print("Stored API key in %s without echoing the secret." % result["credential_file"])
        return 0

    parser.error("unknown command")
    return 2
