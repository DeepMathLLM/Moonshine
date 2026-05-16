<!--
{
  "name": "verify_overall",
  "handler": "verify_overall",
  "description": "Run the full multidimensional pessimistic verification protocol: assumption use, calculation correctness, and logical correctness must all pass.",
  "parameters": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "claim": {"type": "string", "description": "The mathematical claim, lemma, theorem, or final answer to verify."},
      "proof": {"type": "string", "description": "The detailed solution process, proof sketch, or proof blueprint to audit."},
      "context": {"type": "string", "description": "Optional assumptions, definitions, references, memory snippets, or verifier context."},
      "project_slug": {"type": "string", "description": "Optional project scope for traceability."},
      "scope": {"type": "string", "description": "Optional verification scope such as intermediate or final."},
      "blueprint_path": {"type": "string", "description": "Optional workspace-relative blueprint path when auditing the formal project draft."},
      "review_count": {"type": "integer", "description": "Optional independent reviewer count per dimension. Defaults to agent.verification_dimension_review_count."}
    },
    "required": ["claim", "proof"]
  }
}
-->

# Tool: verify_overall

## Usage Hint
- Use this tool to run the full multidimensional correctness verification protocol.
- Use it for important intermediate claims and for final project-level results before accepting them.

