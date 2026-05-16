<!--
{
  "name": "commit_turn",
  "handler": "commit_turn",
  "description": "Legacy compatibility checkpoint for older workflows. It is not needed for ordinary research-mode progress.",
  "parameters": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "title": {"type": "string", "minLength": 1},
      "summary": {"type": "string", "minLength": 1},
      "next_action": {"type": "string"},
      "stage": {"type": "string"},
      "focus_activity": {"type": "string"},
      "status": {"type": "string"},
      "branch_id": {"type": "string"},
      "current_focus": {"type": "string"},
      "current_claim": {"type": "string"},
      "blocker": {"type": "string"},
      "problem_draft": {"type": "string"},
      "blueprint_draft": {"type": "string"},
      "scratchpad": {"type": "string"},
      "open_questions": {"type": "array", "items": {"type": "string"}},
      "failed_paths": {"type": "array", "items": {"type": "string"}},
      "tags": {"type": "array", "items": {"type": "string"}},
      "related_ids": {"type": "array", "items": {"type": "string"}}
    },
    "required": ["title", "summary"]
  }
}
-->

# Tool: commit_turn

## Usage Hint
- Use this tool only when an explicit turn-level checkpoint is required by the runtime workflow.
- Use it for deliberate checkpoints rather than ordinary research reasoning.

