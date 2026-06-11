<!--
{
  "name": "query_memory",
  "handler": "query_memory",
  "description": "Retrieve relevant memory on demand. Searches project research logs, unified session records, dynamic memory, and knowledge records from original stored content and returns source-linked results.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {"type": "string", "description": "The historical information need to search for. In ordinary chat mode this is usually the only required argument."},
      "project_slug": {"type": "string", "description": "Optional project scope override."},
      "all_projects": {"type": "boolean", "description": "When true, search across all projects instead of restricting retrieval to the current project scope."},
      "types": {
        "type": "array",
        "description": "Preferred research-log type filter for research mode. Use only when the need is clearly type-specific. Types: problem=problems/revisions; verified_conclusion=verified lemmas or conclusions; verification=verifier reports; final_result=final theorems/results; counterexample=explicit refutations; failed_path=failed routes or methods; research_note=other progress notes.",
        "items": {
          "type": "string",
          "enum": [
            "problem",
            "verified_conclusion",
            "verification",
            "final_result",
            "counterexample",
            "failed_path",
            "research_note"
          ]
        }
      },
      "channels": {
        "type": "array",
        "description": "Legacy alias for `types`. Prefer `types` for new research-mode calls. Older aliases such as `failed_paths`, `verification_reports`, `solve_steps`, `subgoals`, `branch_states`, `special_case_checks`, and `novelty_notes` are accepted here and normalized internally.",
        "items": {
          "type": "string",
          "enum": [
            "problem",
            "verified_conclusion",
            "verification",
            "final_result",
            "counterexample",
            "failed_path",
            "research_note",
            "failed_paths",
            "verification_reports",
            "solve_steps",
            "subgoals",
            "branch_states",
            "special_case_checks",
            "novelty_notes"
          ]
        }
      },
      "channel_mode": {
        "type": "string",
        "description": "How to retrieve from selected research-log types. This only matters when `types` or legacy `channels` is provided: `search` uses the query inside those types, `recent` returns the most recent records, `all` returns the full selected slice up to the per-type limit.",
        "enum": ["search", "recent", "all"]
      },
      "limit_per_channel": {
        "type": "integer",
        "description": "Maximum number of records to recover from each selected research-log type. This only matters when `types` or legacy `channels` is provided.",
        "minimum": 1,
        "maximum": 20
      },
      "prefer_raw": {
        "type": "boolean",
        "description": "Compatibility flag. Current retrieval favors original stored content and local context rather than summary-only results."
      }
    },
    "required": ["query"]
  }
}
-->

# Tool: query_memory

## Usage Hint
- Use this as the default retrieval tool for relevant prior work from project research logs, unified session records, dynamic memory, and knowledge records.
- Use it when prior facts, failed paths, verified conclusions, decisions, examples, or tool-supported observations may affect the current step.

