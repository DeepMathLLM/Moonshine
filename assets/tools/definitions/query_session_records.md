<!--
{
  "name": "query_session_records",
  "handler": "query_session_records",
  "description": "Search the current or selected session through the unified session-record index and return source-linked local context plus raw archive locations.",
  "parameters": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "query": {"type": "string", "description": "Keyword or phrase to search in original session-record content."},
      "session_id": {"type": "string", "description": "Optional session id. Defaults to the active session."},
      "limit": {"type": "integer", "description": "Maximum number of matching session-record results to return.", "minimum": 1, "maximum": 50}
    },
    "required": ["query"]
  }
}
-->

# Tool: query_session_records

## Usage Hint
- Use this for exact session-level recovery when `query_memory` is not precise enough.
- Use it for original wording, local context windows, tool interactions, archive paths, provider-round references, or omitted retrieval-tool payload locations.

