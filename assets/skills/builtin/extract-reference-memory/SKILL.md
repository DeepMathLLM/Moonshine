---
name: extract-reference-memory
description: Extract durable paper, theorem, and resource references from a conversation window.
compatibility: Requires an Agent Skills-compatible runtime that validates dynamic-memory proposals before writing them.
metadata:
  title: Extract Reference Memory
  category: internal
  tags: memory,references,theorems
  skill-standard: agentskills.io/v1
---

# Extract Reference Memory

## Usage Hint
- Use this skill to identify references and source-grounded facts discussed in a conversation window.
- Use it when papers, notes, URLs, citations, or imported background material should be captured accurately.

## Summary
- Identify references that are likely to be useful again, including papers, theorems, and external resources.
- Preserve enough context to make future retrieval meaningful.

## Execution Steps
1. Inspect the conversation window for cited or discussed references.
2. Keep only references that are explicit, reusable, and not purely incidental.
3. Map each result to the correct reference alias.
4. Return structured JSON proposals.

## Tool Calls
- No direct tool calls. The memory manager validates and writes the returned proposals.

## File References
- `memory/references/papers.md`
- `memory/references/theorems.md`
- `memory/references/resources.md`

## Output Contract
- Return JSON with `dynamic_entries` and `knowledge_entries`.
- Allowed aliases: `reference-papers`, `reference-theorems`, `reference-resources`.

## Notes
- Prefer concise reference notes with enough evidence to recover why the item matters.
