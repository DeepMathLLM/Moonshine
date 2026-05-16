<!--
{
  "name": "list_mcp_servers",
  "handler": "list_mcp_servers",
  "description": "List configured MCP server definitions and their enablement state.",
  "parameters": {
    "type": "object",
    "properties": {
      "include_disabled": {"type": "boolean", "description": "Whether to include disabled MCP server definitions."}
    }
  }
}
-->

# Tool: list_mcp_servers

## Usage Hint
- Use this tool to inspect available MCP server definitions and whether they are enabled.
- Use it when external capabilities such as filesystem or web-search MCP tools may be relevant.

