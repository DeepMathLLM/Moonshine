<!--
{
  "name": "record_research_artifact",
  "handler": "record_research_artifact",
  "internal": true,
  "description": "Store one key structured research artifact without forcing the main assistant response into a rigid schema.",
  "parameters": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "artifact_type": {
        "type": "string",
        "enum": [
          "candidate_problem",
          "problem_review",
          "active_problem",
          "stage_transition",
          "example",
          "counterexample",
          "special_case_check",
          "novelty_note",
          "subgoal_plan",
          "solve_attempt",
          "lemma_candidate",
          "conclusion",
          "verification_report",
          "failed_path",
          "branch_update",
          "decision",
          "checkpoint",
          "note",
          "artifact"
        ]
      },
      "title": {"type": "string", "minLength": 1},
      "summary": {"type": "string", "minLength": 1},
      "content": {"type": "string"},
      "stage": {"type": "string", "enum": ["", "problem_design", "problem_solving"]},
      "focus_activity": {"type": "string"},
      "status": {"type": "string"},
      "review_status": {"type": "string", "enum": ["not_reviewed", "pending", "passed", "failed", "not_applicable"]},
      "related_ids": {"type": "array", "items": {"type": "string"}},
      "tags": {"type": "array", "items": {"type": "string"}},
      "next_action": {"type": "string"},
      "set_as_active": {"type": "boolean"},
      "metadata": {"type": "object"}
    },
    "required": ["artifact_type", "title", "summary"]
  }
}
-->

# Tool: record_research_artifact

## Usage Hint
- Use this tool when an explicit research artifact must be persisted outside ordinary reasoning.
- Use it when the user or workflow specifically asks for a named artifact.

