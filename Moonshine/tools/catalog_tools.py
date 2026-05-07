"""Tools for progressively loading markdown-backed definitions."""

from __future__ import annotations


def load_skill_definition(runtime: dict, slug: str) -> dict:
    """Load the full markdown body for a skill."""
    exposure = dict(runtime.get("exposure") or {})
    skill_manager = runtime["skill_manager"]
    if not skill_manager.is_skill_exposed(
        slug,
        include=list(exposure.get("skills_include") or []),
        exclude=list(exposure.get("skills_exclude") or []),
        agent_slug=str(runtime.get("agent_slug") or ""),
    ):
        raise KeyError("skill not exposed: %s" % slug)
    skill = skill_manager.get_skill(slug)
    if skill is None:
        raise KeyError("skill not found: %s" % slug)
    if str(skill.category).strip().lower() == "internal":
        raise KeyError("skill not exposed: %s" % slug)
    skill_manager.record_load(
        slug=slug,
        session_id=str(runtime.get("session_id", "") or ""),
        project_slug=str(runtime.get("project_slug", "") or ""),
    )
    return {
        "slug": skill.slug,
        "title": skill.title,
        "description": skill.description,
        "category": skill.category,
        "tags": list(skill.tags),
        "allowed_tools": list(skill.allowed_tools),
        "compatibility": skill.compatibility,
        "path": skill.path,
        "sections": dict(skill.sections),
        "tool_calls": list(skill.tool_calls),
        "file_references": list(skill.file_references),
        "output_contract": skill.output_contract,
        "usage_hint": str(getattr(skill, "usage_hint", "") or ""),
        "runtime_notice": (
            "Tool Calls and File References are structured guidance from SKILL.md. "
            "They are not executed or loaded automatically; call the listed tools "
            "or read_runtime_file/query_memory when the task needs those sources."
        ),
        "body": skill.body,
    }


def load_tool_definition(runtime: dict, name: str) -> dict:
    """Load the full markdown body for a tool."""
    exposure = dict(runtime.get("exposure") or {})
    tool_manager = runtime["tool_manager"]
    mode = str(runtime.get("mode") or "")
    visible = {
        item.name
        for item in tool_manager.list_tools(
            mode=mode,
            include=list(exposure.get("tools_include") or []),
            exclude=list(exposure.get("tools_exclude") or []),
        )
    }
    if name not in visible:
        raise KeyError("tool not exposed: %s" % name)
    tool = runtime["tool_manager"].get_tool(name)
    if tool is None:
        raise KeyError("tool not found: %s" % name)
    return {
        "name": tool.name,
        "description": tool.description,
        "handler": tool.handler_name,
        "source": tool.source,
        "path": tool.source_path,
        "parameters": dict(tool.parameters),
        "body": tool.body,
    }


def load_agent_definition(runtime: dict, slug: str = "") -> dict:
    """Load the full markdown body for an agent profile."""
    agent = runtime["agent_manager"].get_agent(slug or str(runtime.get("agent_slug", "") or ""))
    if agent is None:
        raise KeyError("agent not found: %s" % (slug or runtime.get("agent_slug", "")))
    return {
        "slug": agent.slug,
        "title": agent.title,
        "description": agent.description,
        "category": agent.category,
        "tags": list(agent.tags),
        "path": agent.path,
        "runtime_body": agent.runtime_body(),
        "body": agent.body,
    }


def list_mcp_servers(runtime: dict, include_disabled: bool = False) -> dict:
    """List configured MCP server descriptors."""
    servers = runtime["tool_manager"].list_mcp_servers(include_disabled=include_disabled)
    return {"servers": servers}


def load_mcp_server_definition(runtime: dict, slug: str) -> dict:
    """Load the full markdown body for an MCP server descriptor."""
    server = runtime["tool_manager"].get_mcp_server(slug)
    if server is None:
        raise KeyError("MCP server not found: %s" % slug)
    return {
        "slug": server.slug,
        "title": server.title,
        "description": server.description,
        "transport": server.transport,
        "enabled": server.enabled,
        "command": server.command,
        "args": list(server.args),
        "cwd": server.cwd,
        "env": dict(server.env),
        "discover_tools": server.discover_tools,
        "timeout_seconds": server.timeout_seconds,
        "path": server.path,
        "tool_hints": list(server.tool_hints),
        "body": server.body,
    }
