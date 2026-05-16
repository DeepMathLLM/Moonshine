<!--
{
  "name": "verify_correctness_computation",
  "handler": "verify_correctness_computation",
  "description": "Run a configurable number of independent pessimistic reviews that only check for calculation errors in the solution process.",
  "parameters": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "claim": {"type": "string", "description": "The mathematical claim, lemma, theorem, or subproblem being checked."},
      "proof": {"type": "string", "description": "The detailed solution process, proof sketch, or proof blueprint to audit."},
      "context": {"type": "string", "description": "Optional assumptions, definitions, references, or memory snippets needed for the computation check."},
      "project_slug": {"type": "string", "description": "Optional project scope for traceability."},
      "scope": {"type": "string", "description": "Optional verification scope such as intermediate or final."},
      "blueprint_path": {"type": "string", "description": "Optional workspace-relative blueprint path when auditing a formal draft."},
      "review_count": {"type": "integer", "description": "Optional independent reviewer count for this dimension. Defaults to agent.verification_dimension_review_count."}
    },
    "required": ["claim", "proof"]
  }
}
-->

# Tool: verify_correctness_computation

## Usage Hint
- Use this tool to verify computations, formulas, estimates, or transformations.
- Use it when algebraic, analytic, numerical, or symbolic correctness is the main concern.

