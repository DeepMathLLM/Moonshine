<!--
{
  "name": "record_failed_path",
  "handler": "record_failed_path",
  "description": "Store one failed path or decisive obstruction as a structured research artifact.",
  "parameters": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "title": {"type": "string", "minLength": 1},
      "summary": {"type": "string", "minLength": 1},
      "content": {"type": "string"},
      "next_action": {"type": "string"},
      "tags": {"type": "array", "items": {"type": "string"}},
      "related_ids": {"type": "array", "items": {"type": "string"}},
      "review_status": {"type": "string", "enum": ["not_reviewed", "pending", "passed", "failed", "not_applicable"]},
      "set_as_active": {"type": "boolean"},
      "metadata": {"type": "object"}
    },
    "required": ["title", "summary"]
  }
}
-->

# Tool: record_failed_path

## Usage Hint
- Use this tool when a branch or method has been diagnosed as a real dead end.
- Use it when preserving the failure prevents repeated attempts or clarifies the active research direction.

