<!--
{
  "slug": "filesystem",
  "title": "Filesystem MCP Tools",
  "description": "Built-in local filesystem MCP-namespaced tools scoped to the current Moonshine project by default, or the current session when no project is active.",
  "transport": "local",
  "enabled": true,
  "command": "",
  "args": [],
  "discover_tools": false,
  "tool_hints": [
    {
      "name": "read_file",
      "description": "Read the complete contents of a file under the configured filesystem MCP root.",
      "parameters": {
        "type": "object",
        "properties": {
          "path": {"type": "string", "description": "File path relative to the active project root when a project is active, or an allowed absolute path. Legacy projects/<active-project>/... paths are accepted and normalized."}
        },
        "required": ["path"],
        "additionalProperties": false
      }
    },
    {
      "name": "write_file",
      "description": "Create or overwrite a file under the configured filesystem MCP root.",
      "parameters": {
        "type": "object",
        "properties": {
          "path": {"type": "string", "description": "File path relative to the active project root when a project is active, or an allowed absolute path. Legacy projects/<active-project>/... paths are accepted and normalized."},
          "content": {"type": "string", "description": "Complete file content to write."}
        },
        "required": ["path", "content"],
        "additionalProperties": false
      }
    },
    {
      "name": "list_directory",
      "description": "List files and directories under the configured filesystem MCP root.",
      "parameters": {
        "type": "object",
        "properties": {
          "path": {"type": "string", "description": "Directory path relative to the active project root when a project is active, or an allowed absolute path. Legacy projects/<active-project>/... paths are accepted and normalized."}
        },
        "required": ["path"],
        "additionalProperties": false
      }
    },
    {
      "name": "create_directory",
      "description": "Create a directory under the configured filesystem MCP root.",
      "parameters": {
        "type": "object",
        "properties": {
          "path": {"type": "string", "description": "Directory path relative to the active project root when a project is active, or an allowed absolute path. Legacy projects/<active-project>/... paths are accepted and normalized."}
        },
        "required": ["path"],
        "additionalProperties": false
      }
    },
    {
      "name": "search_files",
      "description": "Search for files under the configured filesystem MCP root.",
      "parameters": {
        "type": "object",
        "properties": {
          "path": {"type": "string", "description": "Directory path to search."},
          "pattern": {"type": "string", "description": "Search pattern."}
        },
        "required": ["path", "pattern"],
        "additionalProperties": true
      }
    },
    {
      "name": "get_file_info",
      "description": "Return metadata for a file or directory under the configured filesystem MCP root.",
      "parameters": {
        "type": "object",
        "properties": {
          "path": {"type": "string", "description": "Path relative to the active project root when a project is active, or an allowed absolute path. Legacy projects/<active-project>/... paths are accepted and normalized."}
        },
        "required": ["path"],
        "additionalProperties": false
      }
    }
  ],
  "timeout_seconds": 60
}
-->
# Filesystem MCP Tools

## Purpose
- Provide built-in local filesystem tools under the `mcp_filesystem_*` names for reading project files and making explicit user-requested project file edits in allowed directories.
- At tool-call time, Moonshine resolves `MOONSHINE_MCP_FILESYSTEM_ROOT` to the current project directory when a project is active, otherwise to the current session directory.
- In research mode, use `query_memory` first for project research memory; use project-relative paths such as `workspace/problem.md`, `memory/research_log.md`, `memory/research_log.jsonl`, or `references/notes/source.md` when reading exact file contents is needed.
- Use write tools for explicit project file edits, generated outputs, or user-requested filesystem changes.
- Users can override `MOONSHINE_MCP_FILESYSTEM_ROOT` to point at a specific safe workspace.

## Registration
1. This descriptor is enabled by default.
2. Optionally set `MOONSHINE_MCP_FILESYSTEM_ROOT` to an absolute directory path.
3. Restart Moonshine so tools are registered as `mcp_filesystem_<tool_name>`.

## Safety
- Do not point this at your whole home directory or system root.
- Prefer the default Moonshine project/session directory or a dedicated sandbox directory.
- These tools are implemented directly inside Moonshine rather than by spawning an external Node-based MCP server.
