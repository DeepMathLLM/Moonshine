"""Central slash command definitions for Moonshine."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class SlashCommand:
    """Metadata for a slash command."""

    name: str
    usage: str
    description: str


COMMAND_REGISTRY: List[SlashCommand] = [
    SlashCommand("/help", "/help", "Show the available slash commands."),
    SlashCommand("/exit", "/exit", "Exit the interactive shell."),
    SlashCommand("/auto-run", "/auto-run <prompt>", "Run research autopilot for this prompt only."),
    SlashCommand("/mode", "/mode <chat|research>", "Switch the current conversation mode."),
    SlashCommand("/project", "/project <slug>", "Switch the active project scope and start a fresh session."),
    SlashCommand("/remember", "/remember", "Review the memory system and surface maintenance suggestions."),
    SlashCommand("/memory", "/memory", "List memory files for the current scope."),
    SlashCommand("/memory show", "/memory show <alias>", "Show a memory file."),
    SlashCommand("/memory write", "/memory write <text>", "Store an explicit memory instruction."),
    SlashCommand("/memory edit", "/memory edit <alias>", "Open a memory file in $EDITOR or $VISUAL."),
    SlashCommand("/memory review", "/memory review", "Review dynamic memory quality."),
    SlashCommand("/memory promote", "/memory promote <slug>", "Promote a memory summary into static rules."),
    SlashCommand("/knowledge add", "/knowledge add <title> | <statement> | <proof sketch>", "Store a knowledge item."),
    SlashCommand("/knowledge search", "/knowledge search <query>", "Search the knowledge layer."),
    SlashCommand("/sessions", "/sessions", "List recent sessions."),
    SlashCommand("/sessions search", "/sessions search <query>", "Search across session history."),
    SlashCommand("/context", "/context", "Inspect the current retrieval context."),
    SlashCommand("/agent", "/agent", "Show the active agent profile summary."),
    SlashCommand("/agent show", "/agent show [slug]", "Show the full markdown definition for an agent profile."),
    SlashCommand("/skills", "/skills", "List builtin and installed skills."),
    SlashCommand("/skills show", "/skills show <slug>", "Show the content of a skill markdown file."),
    SlashCommand("/tools", "/tools", "List registered tool summaries."),
    SlashCommand("/tools show", "/tools show <name>", "Show the content of a tool markdown definition."),
    SlashCommand("/mcp", "/mcp", "List configured MCP server descriptors."),
    SlashCommand("/mcp show", "/mcp show <slug>", "Show the content of an MCP server descriptor."),
    SlashCommand("/mcp tavily set-key", "/mcp tavily set-key <api-key>", "Store your Tavily API key locally and enable Tavily MCP."),
    SlashCommand("/mcp tavily enable", "/mcp tavily enable", "Enable the Tavily MCP descriptor."),
    SlashCommand("/mcp tavily disable", "/mcp tavily disable", "Disable the Tavily MCP descriptor."),
]


def render_help() -> str:
    """Render help text from the command registry."""
    lines = ["Moonshine commands", ""]
    for command in COMMAND_REGISTRY:
        lines.append("%s" % command.usage)
        lines.append("  %s" % command.description)
    return "\n".join(lines)


def execute_slash_command(app, command_line: str, state) -> Optional[str]:
    """Execute a slash command or return None."""
    if not command_line.startswith("/"):
        return None

    stripped = command_line.strip()
    if stripped == "/help":
        return render_help()
    if stripped == "/exit":
        return "EXIT"

    if stripped == "/mode":
        return "Usage: /mode <chat|research>"
    if stripped.startswith("/mode "):
        mode = stripped.split(None, 1)[1].strip().lower()
        if mode not in {"chat", "research"}:
            return "Mode must be either 'chat' or 'research'."
        state.mode = mode
        return "Mode switched to %s." % mode

    if stripped == "/project":
        return "Usage: /project <slug>"
    if stripped.startswith("/project "):
        project_slug = stripped.split(None, 1)[1].strip()
        if not project_slug:
            return "Project slug cannot be empty."
        result = app.switch_project_session(state, project_slug)
        if not result["changed"]:
            return "Already using project %s in session %s." % (result["project_slug"], result["session_id"])
        return (
            "Project switched to %s. Closed session %s and started new session %s."
            % (result["project_slug"], result["previous_session_id"], result["session_id"])
        )

    if stripped == "/remember":
        return app.memory.review(project_slug=state.project_slug).to_text()
    if stripped.startswith("/remember "):
        if stripped.startswith("/remember promote "):
            slug = stripped.split(None, 2)[2].strip()
            try:
                target = app.memory.promote(slug)
            except KeyError:
                return "Memory entry not found: %s" % slug
            return "Promoted memory into %s." % target
        return "Use /memory write <text> to store an explicit memory, or /remember to review memory quality."

    if stripped == "/memory":
        lines = ["Memory files"]
        for item in app.memory.dynamic_store.list_memory_files(project_slug=state.project_slug):
            lines.append("- %s: %s" % (item["alias"], item["path"]))
        return "\n".join(lines)

    if stripped == "/memory show":
        return "Usage: /memory show <alias>"
    if stripped.startswith("/memory show "):
        alias = stripped.split(None, 2)[2].strip()
        project_slug = state.project_slug if alias.startswith("project-") else None
        try:
            return app.memory.dynamic_store.read_file(alias, project_slug=project_slug).strip()
        except (KeyError, ValueError):
            return "Memory file alias not found: %s" % alias

    if stripped == "/memory write":
        return "Usage: /memory write <text>"
    if stripped.startswith("/memory write "):
        text = stripped.split(None, 2)[2].strip()
        entry = app.memory.remember_explicit(
            text,
            project_slug=state.project_slug,
            session_id=state.session_id,
            source_message_role="user",
        )
        return "Stored explicit memory '%s' (%s)." % (entry.summary, entry.slug)

    if stripped == "/memory edit":
        return "Usage: /memory edit <alias>"
    if stripped.startswith("/memory edit "):
        alias = stripped.split(None, 2)[2].strip()
        project_slug = state.project_slug if alias.startswith("project-") else None
        try:
            path = app.memory.dynamic_store.resolve_path(alias, project_slug=project_slug)
        except (KeyError, ValueError):
            return "Memory file alias not found: %s" % alias
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
        if not editor:
            return "Set $EDITOR or $VISUAL, then open %s." % path
        try:
            completed = subprocess.call([editor, str(path)])
        except OSError as exc:
            return "Failed to open editor '%s': %s" % (editor, exc)
        if completed != 0:
            return "Editor exited with code %s for %s." % (completed, path)
        app.memory.dynamic_store.rebuild_index()
        return "Memory updated from %s." % path

    if stripped == "/memory review":
        return app.memory.review(project_slug=state.project_slug).to_text()

    if stripped == "/memory promote":
        return "Usage: /memory promote <slug>"
    if stripped.startswith("/memory promote "):
        slug = stripped.split(None, 2)[2].strip()
        try:
            target = app.memory.promote(slug)
        except KeyError:
            return "Memory entry not found: %s" % slug
        return "Promoted memory into %s." % target

    if stripped == "/knowledge add":
        return "Usage: /knowledge add <title> | <statement> | <proof sketch>"
    if stripped.startswith("/knowledge add "):
        payload = stripped.split(None, 2)[2]
        parts = [part.strip() for part in payload.split("|")]
        if len(parts) < 2:
            return "Usage: /knowledge add <title> | <statement> | <proof sketch>"
        item_id = app.memory.knowledge_store.add_conclusion(
            title=parts[0],
            statement=parts[1],
            proof_sketch=parts[2] if len(parts) > 2 else "",
            project_slug=state.project_slug,
            source_type="manual",
            source_ref="cli",
        )
        return "Stored knowledge item %s." % item_id

    if stripped == "/knowledge search":
        return "Usage: /knowledge search <query>"
    if stripped.startswith("/knowledge search "):
        query = stripped.split(None, 2)[2].strip()
        rows = app.memory.knowledge_store.search(query, project_slug=state.project_slug, limit=5)
        if not rows:
            return "No knowledge items matched the query."
        return "\n".join(["Knowledge search results"] + ["- %s: %s" % (item["title"], item["statement"]) for item in rows])

    if stripped == "/sessions":
        rows = app.session_store.list_sessions(limit=10)
        if not rows:
            return "No sessions recorded yet."
        return "\n".join(
            ["Recent sessions"]
            + [
                "- %s | %s | %s | %s"
                % (item["id"], item["mode"], item["project_slug"], item["title"] or "(untitled)")
                for item in rows
            ]
        )

    if stripped == "/sessions search":
        return "Usage: /sessions search <query>"
    if stripped.startswith("/sessions search "):
        query = stripped.split(None, 2)[2].strip()
        rows = app.session_store.search_messages(query, project_slug=state.project_slug, limit=5)
        if not rows:
            return "No matching session messages were found."
        return "\n".join(["Session search results"] + ["- [%s] %s" % (item["role"], item["content"]) for item in rows])

    if stripped == "/context":
        return app.context_manager.build_startup_context(
            mode=state.mode,
            project_slug=state.project_slug,
            session_id=state.session_id,
        ).to_display_text()

    if stripped == "/agent":
        return app.agent_manager.build_prompt_summary()

    if stripped == "/agent show":
        agent = app.agent_manager.get_agent()
        if agent is None:
            return "No agent profiles are registered."
        return agent.body.strip() or ("%s: %s" % (agent.title, agent.description))
    if stripped.startswith("/agent show "):
        slug = stripped.split(None, 2)[2].strip()
        agent = app.agent_manager.get_agent(slug)
        if agent is None:
            return "Agent not found: %s" % slug
        return agent.body.strip() or ("%s: %s" % (agent.title, agent.description))

    if stripped == "/skills":
        registry: Dict[str, List[Dict[str, str]]] = app.skill_manager.list_skills()
        lines = ["Skills", "", "Builtin"]
        lines.extend("- %s: %s" % (item["slug"], item["description"]) for item in registry.get("builtin", []))
        installed = registry.get("installed", [])
        lines.append("")
        lines.append("Installed")
        if installed:
            lines.extend("- %s: %s" % (item["slug"], item["description"]) for item in installed)
        else:
            lines.append("- none")
        return "\n".join(lines)

    if stripped == "/skills show":
        return "Usage: /skills show <slug>"
    if stripped.startswith("/skills show "):
        slug = stripped.split(None, 2)[2].strip()
        skill = app.skill_manager.get_skill(slug)
        if skill is None:
            return "Skill not found: %s" % slug
        return skill.body.strip() or ("%s: %s" % (skill.title, skill.description))

    if stripped == "/tools":
        definitions = app.tool_manager.list_tools()
        if not definitions:
            return "No tools are registered."
        return "\n".join(["Tools"] + ["- %s: %s" % (item.name, item.description) for item in definitions])

    if stripped == "/tools show":
        return "Usage: /tools show <name>"
    if stripped.startswith("/tools show "):
        name = stripped.split(None, 2)[2].strip()
        tool = app.tool_manager.get_tool(name)
        if tool is None:
            return "Tool not found: %s" % name
        return tool.body.strip() or ("%s: %s" % (tool.name, tool.description))

    if stripped == "/mcp":
        servers = app.tool_manager.list_mcp_servers(include_disabled=True)
        if not servers:
            return "No MCP server descriptors are configured."
        return "\n".join(
            ["MCP servers"]
            + [
                "- %s [%s]: %s"
                % (item["slug"], "enabled" if item["enabled"] else "disabled", item["description"] or item["title"])
                for item in servers
            ]
        )

    if stripped == "/mcp show":
        return "Usage: /mcp show <slug>"
    if stripped.startswith("/mcp show "):
        slug = stripped.split(None, 2)[2].strip()
        server = app.tool_manager.get_mcp_server(slug)
        if server is None:
            return "MCP server not found: %s" % slug
        return server.body.strip() or ("%s: %s" % (server.title, server.description))

    if stripped == "/mcp tavily set-key":
        return "Usage: /mcp tavily set-key <api-key>"
    if stripped.startswith("/mcp tavily set-key "):
        api_key = stripped.split(None, 3)[3].strip()
        try:
            result = app.configure_tavily_api_key(api_key, enable=True)
        except ValueError as exc:
            return str(exc)
        return (
            "Stored Tavily API key in %s and enabled Tavily MCP descriptor %s. "
            "Restart Moonshine to discover Tavily tools."
            % (result["credential_file"], result["descriptor_file"])
        )

    if stripped == "/mcp tavily enable":
        result = app.set_tavily_enabled(True)
        suffix = "" if result["has_key"] else " Set your Tavily API key first with /mcp tavily set-key <api-key>."
        return "Enabled Tavily MCP descriptor %s.%s" % (result["descriptor_file"], suffix)

    if stripped == "/mcp tavily disable":
        result = app.set_tavily_enabled(False)
        return "Disabled Tavily MCP descriptor %s." % result["descriptor_file"]

    return "Unknown command. Use /help to inspect the available commands."
