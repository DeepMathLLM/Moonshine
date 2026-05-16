<!--
{
  "name": "add_knowledge",
  "handler": "add_knowledge",
  "description": "Store a structured knowledge conclusion.",
  "parameters": {
    "type": "object",
    "properties": {
      "title": {"type": "string", "description": "Knowledge title."},
      "statement": {"type": "string", "description": "The structured conclusion statement."},
      "proof_sketch": {"type": "string", "description": "Optional proof sketch or rationale."},
      "project_slug": {"type": "string", "description": "Optional project scope."}
    },
    "required": ["title", "statement"]
  }
}
-->

# Tool: add_knowledge

## Usage Hint
- Use this tool to promote a stable conclusion into reusable knowledge.
- Use it when a verified or highly reliable result should be available beyond the current turn or project.

