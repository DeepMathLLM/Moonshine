---
name: query-memory
description: Pull detailed historical context only when the startup summaries are not enough.
compatibility: Requires an Agent Skills-compatible runtime that exposes query_memory and conclusion-storage tools.
allowed-tools: query_memory
metadata:
  title: Query Memory
  category: builtin
  tags: memory,retrieval,context
  skill-standard: agentskills.io/v1
---

# Query Memory

## Usage Hint
- Use this skill to retrieve relevant prior project or session context before repeating work.
- Use it when earlier results, failed paths, references, or decisions may affect the current research step.

## Summary
- Keep the default context light and only retrieve deeper history when the current turn genuinely needs it.
- Use retrieved evidence as grounded working context instead of guessing from partial recall.

## Execution Steps
1. Review the startup summaries already present in the current context.
2. If important historical detail is still missing, call `query_memory` with a focused query.
3. In research mode, add `types=[...]` only when the need is clearly type-specific; otherwise leave type filters out.
4. Read the returned source-labeled summary and reconstructed windows before making claims.
5. If the retrieved evidence reveals a durable result, consider saving it into the knowledge layer.

## Tool Calls
- `query_memory`: Retrieve source-labeled summaries and reconstructed local windows from dynamic memory, session memory, and knowledge memory.

## File References
- `memory/MEMORY.md`
- `projects/<project_slug>/memory/research_log.jsonl`
- `sessions/<session_id>/provider_trace.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return an answer grounded in retrieved evidence.
- Mention uncertainty when the retrieved material is incomplete or conflicting.
- In research mode, prefer the smallest relevant research-log type scope instead of querying every type by default.

## Notes
- Prefer `all_projects=true` only when the current project scope is insufficient.
