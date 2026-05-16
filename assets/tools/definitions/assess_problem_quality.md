<!--
{
  "name": "assess_problem_quality",
  "handler": "assess_problem_quality",
  "description": "Run exactly one dedicated quality-assessor review for a candidate research problem and persist the resulting problem_review.",
  "parameters": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "problem": {"type": "string", "description": "The candidate or active research problem to assess."},
      "context": {"type": "string", "description": "Optional context, candidate comparisons, novelty notes, memory snippets, or known constraints."},
      "project_slug": {"type": "string", "description": "Optional project scope for traceability."},
      "set_as_active": {"type": "boolean", "description": "Whether this reviewed problem should become the active problem. Defaults to true."}
    },
    "required": ["problem"]
  }
}
-->

# Tool: assess_problem_quality

## Usage Hint
- Use this tool to run the dedicated quality gate for a candidate research problem.
- Use it when deciding whether a problem is ready to move from design into solving.

