<!--
{
  "name": "search_knowledge",
  "handler": "search_knowledge",
  "description": "Search structured knowledge conclusions before re-deriving or reusing a result.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "Knowledge search query."},
      "project_slug": {"type": "string", "description": "Optional project scope."}
    },
    "required": ["query"]
  }
}
-->

# Tool: search_knowledge

## Usage Hint
- Use this tool to search durable cross-project knowledge.
- Use it when a known verified result may save work or constrain the current claim.

