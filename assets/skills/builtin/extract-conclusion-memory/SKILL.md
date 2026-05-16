---
name: extract-conclusion-memory
description: Extract stable structured conclusions that should be promoted into the knowledge memory layer.
compatibility: Requires an Agent Skills-compatible runtime that validates knowledge proposals before writing them.
metadata:
  title: Extract Conclusion Memory
  category: internal
  tags: memory,knowledge,conclusions
  skill-standard: agentskills.io/v1
---

# Extract Conclusion Memory

## Usage Hint
- Use this skill to identify durable mathematical conclusions inside a conversation or transcript window.
- Use it when a memory-extraction pass needs to separate reusable claims from ordinary discussion.

## Summary
- Identify results that are stable enough to live in the structured knowledge layer.
- Preserve statement, scope, evidence, and confidence without overclaiming.

## Execution Steps
1. Read the trigger payload and find conclusion-like mathematical results.
2. Keep only results that are durable, reusable, and reasonably well supported.
3. Return structured knowledge proposals with title, statement, proof sketch, status, tags, and project scope.
4. Do not emit dynamic-memory entries unless the result also deserves a project note.

## Tool Calls
- No direct tool calls. The memory manager validates and writes the returned proposals.

## File References
- `projects/<project_slug>/memory/research_log_index.sqlite`
- `knowledge/entries/*.md`
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`

## Output Contract
- Return JSON with `dynamic_entries` and `knowledge_entries`.
- Each knowledge entry must include `title`, `statement`, `proof_sketch`, `status`, and `tags`.

## Notes
- Prefer `partial` or `candidate` status when the evidence is promising but not fully verified.
