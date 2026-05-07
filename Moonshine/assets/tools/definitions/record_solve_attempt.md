<!--
{
  "name": "record_solve_attempt",
  "handler": "record_solve_attempt",
  "description": "Store one durable solve attempt or branch-level proof attempt as a structured research artifact.",
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

# Tool: record_solve_attempt

## Usage Hint
- Use this tool when a substantial solve attempt should be preserved as an explicit attempt record.
- Use it when the attempt contains reusable structure even if it does not finish the proof.

