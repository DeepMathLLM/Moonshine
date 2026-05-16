---
name: extract-project-memory
description: Extract durable project context from a conversation window.
compatibility: Requires an Agent Skills-compatible runtime that validates project-scoped memory proposals before writing them.
metadata:
  title: Extract Project Memory
  category: internal
  tags: memory,project,context
  skill-standard: agentskills.io/v1
---

# Extract Project Memory

## Usage Hint
- Use this skill to identify durable project context from a conversation window.
- Use it when recent discussion may contain stable project background, constraints, or scope facts.

## Summary
- Capture durable project-scoped context that should persist across sessions.
- Keep each proposal tied to the active project scope.

## Execution Steps
1. Read the trigger payload and confirm the active project.
2. Extract context updates that are likely to matter later.
3. Avoid duplicating transient chat phrasing unless it carries durable project state.
4. Return JSON proposals for the allowed project aliases only.

## Tool Calls
- No direct tool calls. The memory manager validates and writes the returned proposals.

## File References
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/problem.md`
- `projects/<project_slug>/memory/by_type/research_note.md`

## Output Contract
- Return JSON with `dynamic_entries` and `knowledge_entries`.
- Allowed aliases: `project-context`.

## Notes
- Prefer summaries that make sense even when revisited in a later session.
