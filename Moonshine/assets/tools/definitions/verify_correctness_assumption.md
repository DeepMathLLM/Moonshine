<!--
{
  "name": "verify_correctness_assumption",
  "handler": "verify_correctness_assumption",
  "description": "Run a configurable number of independent pessimistic reviews that only check whether every condition and assumption in the subproblem is actually used.",
  "parameters": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "claim": {"type": "string", "description": "The mathematical claim, lemma, theorem, or subproblem being checked."},
      "proof": {"type": "string", "description": "The detailed solution process, proof sketch, or proof blueprint to audit."},
      "context": {"type": "string", "description": "Optional assumptions, definitions, references, or memory snippets needed for the assumption-usage check."},
      "project_slug": {"type": "string", "description": "Optional project scope for traceability."},
      "scope": {"type": "string", "description": "Optional verification scope such as intermediate or final."},
      "blueprint_path": {"type": "string", "description": "Optional workspace-relative blueprint path when auditing a formal draft."},
      "review_count": {"type": "integer", "description": "Optional independent reviewer count for this dimension. Defaults to agent.verification_dimension_review_count."}
    },
    "required": ["claim", "proof"]
  }
}
-->

# Tool: verify_correctness_assumption

## Usage Hint
- Use this tool to verify assumption use and definition matching in a claim or proof.
- Use it when hidden assumptions, domain restrictions, or exceptional cases are the main concern.

