---
name: memory-hygiene
description: Promote durable facts and avoid storing noisy, one-off details.
compatibility: Works in Agent Skills-compatible runtimes that maintain layered memory files.
allowed-tools: query_memory
metadata:
  title: Memory Hygiene
  category: builtin
  tags: memory,quality,retrieval
  skill-standard: agentskills.io/v1
---

# Memory Hygiene

## Usage Hint
- Use this skill to review whether stored memory is redundant, stale, or too noisy for retrieval.
- Use it when memory quality itself is blocking reliable reuse of prior research context.

## Summary
- Keep dynamic memory concise, durable, and easy to retrieve later.
- Prefer summaries with traceable sources over raw transcript dumps.

## Execution Steps
1. Store only information that is likely to matter in future turns or sessions.
2. Add project scope whenever a fact is specific to one project.
3. Use the knowledge layer for stable conclusions and reusable claims.
4. Avoid copying entire chats into memory files when a short structured summary is enough.

## Tool Calls
- `query_memory`: Inspect existing memory before adding more overlapping content.

## File References
- `memory/MEMORY.md`
- `memory/user/preferences.md`
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/research_note.md`

## Output Contract
- Preserve only durable facts, constraints, conclusions, and next-step signals.

## Notes
- This skill is especially useful before or after context compression.
