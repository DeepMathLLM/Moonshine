<!--
{
  "name": "load_mcp_server_definition",
  "handler": "load_mcp_server_definition",
  "description": "Load the full markdown definition for an MCP server descriptor.",
  "parameters": {
    "type": "object",
    "properties": {
      "slug": {"type": "string", "description": "The MCP server slug to inspect."}
    },
    "required": ["slug"]
  }
}
-->

# Tool: load_mcp_server_definition

## Usage Hint
- Use this tool to read the full markdown definition of an MCP server descriptor.
- Use it when the compact MCP summary is insufficient for deciding whether or how to use that server.

