"""High-level tool management and progressive loading."""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from moonshine.moonshine_constants import MoonshinePaths
from moonshine.skills.skill_document import extract_skill_sections
from moonshine.tools.mcp_bridge import MCPServerDefinition, MCPServerRegistry
from moonshine.tools.registry import ToolDefinition, ToolRegistry
from moonshine.utils import shorten


class ToolManager(object):
    """Compose native markdown tools and optional MCP-backed tool hints."""

    def __init__(self, paths: MoonshinePaths):
        self.paths = paths
        self.registry = ToolRegistry(paths=paths)
        self.mcp_registry = MCPServerRegistry(paths)
        self.refresh_external_tools()

    def refresh_external_tools(self) -> None:
        """Register any enabled MCP tool hints into the callable registry."""
        for hint in self.mcp_registry.export_enabled_tool_hints():
            name = str(hint.get("name", "")).strip()
            if not name:
                continue
            description = str(hint.get("description", "")).strip()
            parameters = dict(hint.get("parameters") or hint.get("input_schema") or {})
            server_slug = str(hint.get("server_slug", "")).strip()
            remote_name = str(hint.get("remote_name") or name).strip()

            def _dispatch(runtime, _server_slug=server_slug, _remote_name=remote_name, **kwargs):
                return self.mcp_registry.call_tool(_server_slug, _remote_name, kwargs, runtime=runtime)

            self.registry.register(
                ToolDefinition(
                    name=name,
                    description=description,
                    parameters=parameters,
                    handler=_dispatch,
                    handler_name="mcp:%s:%s" % (server_slug, remote_name),
                    body=str(hint.get("body", "")).strip(),
                    source_path=str(hint.get("source_path", "")),
                    source="mcp:%s" % server_slug,
                )
            )

    def schemas(
        self,
        mode: Optional[str] = None,
        *,
        include: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, object]]:
        """Return provider-facing tool schemas."""
        return self.registry.schemas(mode=mode, include=include, exclude=exclude)

    def dispatch(self, name: str, arguments: Dict[str, object], runtime: Dict[str, object]) -> Dict[str, object]:
        """Dispatch a tool call."""
        return self.registry.dispatch(name, arguments, runtime)

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        """Return a tool definition by name."""
        return self.registry.get(name)

    def list_tools(
        self,
        mode: Optional[str] = None,
        *,
        include: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
    ) -> List[ToolDefinition]:
        """Return all registered tools."""
        return self.registry.list_definitions(mode=mode, include=include, exclude=exclude)

    def build_prompt_index(
        self,
        limit: int = 64,
        mode: Optional[str] = None,
        *,
        include: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
    ) -> str:
        """Build a compact tool summary for the system prompt."""
        definitions = [
            item
            for item in self.list_tools(mode=mode, include=include, exclude=exclude)
            if not getattr(item, "internal", False)
        ]
        if not definitions:
            return ""
        effective_limit = max(0, int(limit or 0))
        shown_definitions = definitions if effective_limit <= 0 else definitions[:effective_limit]
        lines = ["Available tools (short descriptions and usage guidance; use matching tools when they provide evidence or persistence):"]
        for item in shown_definitions:
            usage = self._usage_hint(item)
            suffix = " Usage: %s" % usage if usage else ""
            lines.append("- %s: %s%s" % (item.name, item.description or item.name, suffix))
        if effective_limit > 0 and len(definitions) > effective_limit:
            lines.append("- ... plus %s more tools" % (len(definitions) - effective_limit))
        lines.append("Use matching tools for retrieval, reading, verification, experiments, file updates, and durable records; load a tool's full markdown definition with load_tool_definition when the summary and schema are insufficient.")
        return "\n".join(lines)

    def _usage_hint(self, definition: ToolDefinition) -> str:
        """Extract a compact usage hint from a markdown tool definition."""
        body = str(definition.body or "")
        if not body.strip():
            return ""
        sections = extract_skill_sections(body)
        explicit_hint = str(sections.get("Usage Hint") or sections.get("Tool Usage Hint") or "").strip()
        if explicit_hint:
            lines = []
            for raw_line in explicit_hint.splitlines():
                line = raw_line.strip().lstrip("-0123456789. ")
                if line:
                    lines.append(line)
            if lines:
                return " ".join(lines)
            return explicit_hint
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("<!--") or line.startswith("{") or line.startswith("}"):
                continue
            if line.lower().startswith("use this tool"):
                return shorten(line, 180)
        paragraph = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or line.startswith("<!--"):
                if paragraph:
                    break
                continue
            paragraph.append(line)
            if len(" ".join(paragraph)) > 180:
                break
        return shorten(" ".join(paragraph), 180)

    def list_mcp_servers(self, include_disabled: bool = False) -> List[Dict[str, object]]:
        """Return MCP server descriptors in display form."""
        servers = []
        for item in self.mcp_registry.list_servers():
            if include_disabled or item.enabled:
                servers.append(item.to_display_item())
        return servers

    def get_mcp_server(self, slug: str) -> Optional[MCPServerDefinition]:
        """Return an MCP server descriptor."""
        return self.mcp_registry.get_server(slug)

    def build_mcp_index(self, limit: int = 6) -> str:
        """Build a compact MCP summary for the system prompt."""
        return self.mcp_registry.build_prompt_index(limit=limit)
