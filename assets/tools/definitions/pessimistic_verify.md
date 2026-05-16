<!--
{
  "name": "pessimistic_verify",
  "handler": "pessimistic_verify",
  "description": "Legacy fallback verifier: run multiple independent schema-constrained LLM proof reviews and fail the aggregate if any reviewer finds a wrong or inconclusive result.",
  "parameters": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "claim": {"type": "string", "description": "The mathematical claim, lemma, theorem, or final answer to verify."},
      "proof": {"type": "string", "description": "The proof, proof sketch, or proof blueprint to audit."},
      "context": {"type": "string", "description": "Optional assumptions, definitions, references, memory snippets, or verifier context."},
      "project_slug": {"type": "string", "description": "Optional project scope for traceability."},
      "review_count": {"type": "integer", "description": "Optional independent reviewer count. Omit it to use the tool default; pass it only when intentionally overriding. Capped at 5."},
      "scope": {"type": "string", "description": "Optional verification scope such as intermediate or final."},
      "blueprint_path": {"type": "string", "description": "Optional workspace-relative blueprint path when auditing the formal project draft."}
    },
    "required": ["claim", "proof"]
  }
}
-->

# Tool: pessimistic_verify

## Usage Hint
- Use this tool for adversarial verification of a mathematical claim or proof.
- Use it when a result needs skeptical checking before it is trusted.

