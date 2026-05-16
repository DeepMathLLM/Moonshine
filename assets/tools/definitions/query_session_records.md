<!--
{
  "name": "query_session_records",
  "handler": "query_session_records",
  "description": "Search complete raw records for the current or selected session, including full messages, transcript, tool events, provider-round archives, and context summaries.",
  "parameters": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "query": {"type": "string", "description": "Keyword or phrase to search in raw session records."},
      "session_id": {"type": "string", "description": "Optional session id. Defaults to the active session."},
      "limit": {"type": "integer", "description": "Maximum number of matching raw-record locations to return."}
    },
    "required": ["query"]
  }
}
-->

# Tool: query_session_records

## Usage Hint
- Use this tool to retrieve records from session history.
- Use it when exact prior conversation or tool-use context may matter more than summarized project memory.

