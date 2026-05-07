<!--
{
  "name": "search_sessions",
  "handler": "search_sessions",
  "description": "Search messages across sessions.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "Session-search query."},
      "project_slug": {"type": "string", "description": "Optional project scope."}
    },
    "required": ["query"]
  }
}
-->

# Tool: search_sessions

## Usage Hint
- Use this tool to search past session transcripts or session metadata.
- Use it when relevant information is likely in prior conversation rather than durable project memory.

