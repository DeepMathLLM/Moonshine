<!--
{
  "name": "query_memory",
  "handler": "query_memory",
  "description": "Retrieve relevant memory on demand. In research mode project research memory is stored only in research_log.jsonl and searched through research_log_index.sqlite, so retrieval returns project-memory records and direct content excerpts with source references.",
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
        "description": "When true, prefer original windows and direct excerpts over extra summarization. Research mode turns this on by default."
      }
    },
    "required": ["query"]
  }
}
-->

# Tool: query_memory

## Usage Hint
- Use this tool to retrieve project memory, research-log records, by-type memory files, knowledge, or session snippets.
- Use it when prior research progress may contain facts, failed paths, verified conclusions, or decisions relevant to the current step.

