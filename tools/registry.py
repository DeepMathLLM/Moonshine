"""Central tool registry for Moonshine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from moonshine.json_schema import validate_json_schema
from moonshine.markdown_metadata import load_markdown_metadata
from moonshine.moonshine_constants import MoonshinePaths, packaged_tool_definitions_dir
from moonshine.tools.catalog_tools import (
    list_mcp_servers,
    load_agent_definition,
    load_mcp_server_definition,
    load_skill_definition,
    load_tool_definition,
)
from moonshine.tools.file_tools import read_runtime_file
from moonshine.tools.knowledge_tools import add_knowledge, search_knowledge, store_conclusion
from moonshine.tools.memory_tools import memory_overview
from moonshine.tools.research_tools import (
    assess_problem_quality,
    commit_turn,
    record_failed_path,
    record_research_artifact,
    record_solve_attempt,
)
from moonshine.tools.retrieval_tools import query_memory
from moonshine.tools.session_tools import list_sessions, query_session_records, search_sessions
from moonshine.tools.skill_tools import manage_skill
from moonshine.tools.verification_tools import (
    pessimistic_verify,
    verify_correctness_assumption,
    verify_correctness_computation,
    verify_correctness_logic,
    verify_overall,
)


@dataclass
class ToolDefinition:
    """Provider-callable tool metadata."""

    name: str
    description: str
    parameters: Dict[str, object]
    handler: Callable[..., Dict[str, object]]
    handler_name: str = ""
    body: str = ""
    source_path: str = ""
    source: str = "native"
    internal: bool = False


class ToolRegistry(object):
    """Register and dispatch model tools."""

    MODE_HIDDEN_TOOLS = {
        "chat": {
            "commit_turn",
            "record_solve_attempt",
            "record_failed_path",
        },
        "research": {
            "manage_skill",
            "commit_turn",
            "record_research_artifact",
            "record_solve_attempt",
            "record_failed_path",
            "store_conclusion",
            "add_knowledge",
        },
    }

    def __init__(self, paths: Optional[MoonshinePaths] = None):
        self.paths = paths
        self._tools: Dict[str, ToolDefinition] = {}
        self._handler_map = {
            "memory_overview": lambda runtime, **kwargs: memory_overview(runtime, **kwargs),
            "assess_problem_quality": lambda runtime, **kwargs: assess_problem_quality(runtime, **kwargs),
            "commit_turn": lambda runtime, **kwargs: commit_turn(runtime, **kwargs),
            "record_research_artifact": lambda runtime, **kwargs: record_research_artifact(runtime, **kwargs),
            "record_solve_attempt": lambda runtime, **kwargs: record_solve_attempt(runtime, **kwargs),
            "record_failed_path": lambda runtime, **kwargs: record_failed_path(runtime, **kwargs),
            "add_knowledge": lambda runtime, **kwargs: add_knowledge(runtime, **kwargs),
            "store_conclusion": lambda runtime, **kwargs: store_conclusion(runtime, **kwargs),
            "search_knowledge": lambda runtime, **kwargs: search_knowledge(runtime, **kwargs),
            "query_memory": lambda runtime, **kwargs: query_memory(runtime, **kwargs),
            "list_sessions": lambda runtime, **kwargs: list_sessions(runtime, **kwargs),
            "search_sessions": lambda runtime, **kwargs: search_sessions(runtime, **kwargs),
            "query_session_records": lambda runtime, **kwargs: query_session_records(runtime, **kwargs),
            "read_runtime_file": lambda runtime, **kwargs: read_runtime_file(runtime, **kwargs),
            "manage_skill": lambda runtime, **kwargs: manage_skill(runtime, **kwargs),
            "pessimistic_verify": lambda runtime, **kwargs: pessimistic_verify(runtime, **kwargs),
            "verify_correctness_assumption": lambda runtime, **kwargs: verify_correctness_assumption(runtime, **kwargs),
            "verify_correctness_computation": lambda runtime, **kwargs: verify_correctness_computation(runtime, **kwargs),
            "verify_correctness_logic": lambda runtime, **kwargs: verify_correctness_logic(runtime, **kwargs),
            "verify_overall": lambda runtime, **kwargs: verify_overall(runtime, **kwargs),
            "load_skill_definition": lambda runtime, **kwargs: load_skill_definition(runtime, **kwargs),
            "load_tool_definition": lambda runtime, **kwargs: load_tool_definition(runtime, **kwargs),
            "load_agent_definition": lambda runtime, **kwargs: load_agent_definition(runtime, **kwargs),
            "list_mcp_servers": lambda runtime, **kwargs: list_mcp_servers(runtime, **kwargs),
            "load_mcp_server_definition": lambda runtime, **kwargs: load_mcp_server_definition(runtime, **kwargs),
        }
        self._register_from_markdown()

    def _definition_directories(self) -> List[Path]:
        """Return the ordered tool-definition directories."""
        directories = [packaged_tool_definitions_dir()]
        if self.paths is not None:
            directories.append(self.paths.tool_definitions_dir)
        return [path for path in directories if path.exists()]

    def _register_from_markdown(self) -> None:
        """Register tools from markdown definition files."""
        for directory in self._definition_directories():
            for definition_path in sorted(directory.rglob("*.md")):
                metadata, body = load_markdown_metadata(definition_path)
                name = str(metadata.get("name", "")).strip()
                handler_name = str(metadata.get("handler", name)).strip()
                if not name:
                    continue
                handler = self._handler_map.get(handler_name)
                if handler is None:
                    raise KeyError("tool handler not found: %s" % handler_name)
                self.register(
                    ToolDefinition(
                        name=name,
                        description=str(metadata.get("description", "")).strip(),
                        parameters=dict(metadata.get("parameters", {})),
                        handler=handler,
                        handler_name=handler_name,
                        body=body,
                        source_path=str(definition_path),
                        source="runtime" if self.paths is not None and str(definition_path).startswith(str(self.paths.home)) else "packaged",
                        internal=bool(metadata.get("internal", False)),
                    )
                )

    def register(self, definition: ToolDefinition) -> None:
        """Register a tool definition."""
        self._tools[definition.name] = definition

    def _visible_in_mode(self, definition: ToolDefinition, mode: Optional[str] = None) -> bool:
        """Return True when a tool should be exposed in the given mode."""
        normalized_mode = str(mode or "").strip().lower()
        if definition.internal:
            return False
        if normalized_mode and definition.name in self.MODE_HIDDEN_TOOLS.get(normalized_mode, set()):
            return False
        return True

    def _included_by_name(
        self,
        name: str,
        *,
        include: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
    ) -> bool:
        include_set = {str(item).strip() for item in list(include or []) if str(item).strip()}
        exclude_set = {str(item).strip() for item in list(exclude or []) if str(item).strip()}
        if include_set and name not in include_set:
            return False
        if name in exclude_set:
            return False
        return True

    def schemas(
        self,
        mode: Optional[str] = None,
        *,
        include: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
    ) -> List[Dict[str, object]]:
        """Return provider-facing schemas."""
        return [
            {
                "name": item.name,
                "description": item.description,
                "parameters": item.parameters,
            }
            for item in self._tools.values()
            if self._visible_in_mode(item, mode=mode)
            and self._included_by_name(item.name, include=include, exclude=exclude)
        ]

    def get(self, name: str) -> Optional[ToolDefinition]:
        """Return a tool definition by name."""
        return self._tools.get(name)

    def list_definitions(
        self,
        mode: Optional[str] = None,
        *,
        include: Optional[Sequence[str]] = None,
        exclude: Optional[Sequence[str]] = None,
    ) -> List[ToolDefinition]:
        """Return all tool definitions."""
        return [
            item
            for item in self._tools.values()
            if self._visible_in_mode(item, mode=mode)
            and self._included_by_name(item.name, include=include, exclude=exclude)
        ]

    def dispatch(self, name: str, arguments: Dict[str, object], runtime: Dict[str, object]) -> Dict[str, object]:
        """Dispatch a tool call."""
        if name not in self._tools:
            raise KeyError("unknown tool: %s" % name)
        definition = self._tools[name]
        normalized_arguments = dict(arguments or {})
        validate_json_schema(normalized_arguments, definition.parameters)
        return definition.handler(runtime, **normalized_arguments)
