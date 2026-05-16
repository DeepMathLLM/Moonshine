<!--
{
  "slug": "tavily",
  "title": "Tavily MCP Server",
  "description": "Disabled template for Tavily real-time web search and page extraction through MCP.",
  "transport": "stdio",
  "enabled": false,
  "command": "npx",
  "args": ["-y", "tavily-mcp@0.1.3"],
  "env": {
    "TAVILY_API_KEY": "${TAVILY_API_KEY}"
  },
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
          "days": {"type": "integer", "description": "Limit news recency to the last N days."}
        },
        "required": ["query"],
        "additionalProperties": true
      }
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
              {"type": "array", "items": {"type": "string"}}
            ],
            "description": "One URL or a list of URLs to extract."
          },
          "extract_depth": {"type": "string", "description": "Extraction depth such as basic or advanced."},
          "format": {"type": "string", "description": "Output format such as markdown or text."},
          "query": {"type": "string", "description": "Optional extraction focus query."},
          "include_images": {"type": "boolean", "description": "Whether to include images."},
          "include_favicon": {"type": "boolean", "description": "Whether to include favicons."}
        },
        "required": ["urls"],
        "additionalProperties": true
      }
    }
  ],
  "discover_tools": true,
  "timeout_seconds": 60
}
-->
# Tavily MCP Server

## Purpose
- Connect Tavily as an external MCP server for web search and page extraction.
- Keep this descriptor disabled until the user has configured `TAVILY_API_KEY`.
- Get a personal Tavily API key from <https://app.tavily.com/>.

## Enable
1. Run `python -m moonshine mcp --set-tavily-key` and enter your own API key.
2. Moonshine stores the key in the local runtime file `config/credentials.json`.
3. Restart Moonshine so it can register `mcp_tavily_tavily_search` and `mcp_tavily_tavily_extract`, then discover any extra Tavily tools from `tools/list`.

## Notes
- Moonshine resolves `${TAVILY_API_KEY}` at subprocess launch time from the environment or local credentials file, so the descriptor does not need to store the secret.
- The credentials file is not created during initialization; it appears only after the user explicitly sets a credential.
- The static search/extract tools above keep Tavily usable even if live `tools/list` discovery is temporarily unavailable.
