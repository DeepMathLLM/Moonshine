---
name: research-consolidation
description: Summarize research progress, decisions, lemmas, failed paths, and next actions.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge
metadata:
  title: Research Consolidation
  category: builtin
  tags: research,summary,continuation
  skill-standard: agentskills.io/v1
---

# Research Consolidation

## Usage Hint
- Use this skill to consolidate several partial branches, conclusions, or failed attempts into a coherent research direction.
- Use it when the project has accumulated enough material that the next move depends on synthesis.

## Summary
- Summarize partial progress, failed paths, and next actions so future turns can continue.

## Execution Steps
1. Identify the mathematically important outputs from the current turn.
2. Separate project context, decisions, lemmas, failed paths, and stable conclusions.
3. State stable conclusions clearly with evidence and scope.
4. State branch advances, failed paths, and verified conclusions clearly.
5. Avoid saving noisy transcript fragments.

## Tool Calls
- `query_memory`: Check whether a result is already stored.
- `search_knowledge`: Avoid duplicating conclusions.

## File References
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/problem.md`
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Natural language is fine.
- Prefer clear, report-like prose for checkpoints, failed paths, branch notes, and verified conclusions.
- When a mathematical claim may be final or reusable, run `verify_overall` if correctness is still in doubt.
- When the result is a partial branch advance or decisive dead end, state it clearly.
- The archival pass will save clear consolidation output into `research_log.jsonl` for later retrieval.

## Notes
- Partial progress is valuable when its assumptions and failure modes are explicit.
