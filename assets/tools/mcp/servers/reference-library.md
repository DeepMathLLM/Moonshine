<!--
{
  "slug": "reference-library",
  "title": "Reference Library MCP Server",
  "description": "Disabled example descriptor for connecting an external reference lookup MCP server.",
  "transport": "stdio",
  "enabled": false,
  "command": "python",
  "args": ["-m", "reference_library_server"],
  "tool_hints": [
    {
      "name": "lookup_reference_entry",
      "description": "Look up a bibliographic or theorem reference from the external library server.",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {"type": "string", "description": "Reference query to search for."}
        },
        "required": ["query"]
      }
    }
  ]
}
-->
# Reference Library MCP Server

## Purpose
- Demonstrate how to describe an external MCP server in markdown.
- Keep the descriptor disabled until the transport command is available in the runtime environment.

## Notes
- Set `enabled` to `true` only after the backing MCP server command is installed and reachable.
- `tool_hints` provide summary-level metadata so Moonshine can understand what the external server offers.
