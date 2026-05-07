---
name: extract-project-claims
description: Extract lemma candidates, intermediate claims, and counterexample notes for the active project.
compatibility: Requires an Agent Skills-compatible runtime that validates project-scoped memory proposals before writing them.
metadata:
  title: Extract Project Claims
  category: internal
  tags: memory,project,lemmas
  skill-standard: agentskills.io/v1
---

# Extract Project Claims

## Usage Hint
- Use this skill to identify project-scoped mathematical claims in recent project material.
- Use it when a transcript contains conjectures, lemmas, theorem candidates, or problem statements that should be recognized as claims.

## Summary
- Capture intermediate project claims that are worth revisiting later.
- Focus on lemma candidates, subclaims, and counterexample notes rather than fully stable knowledge.

## Execution Steps
1. Read the trigger payload and isolate claim-like mathematical content.
2. Keep only project-scoped claims that are specific enough to be useful later.
3. Return structured proposals under the project lemma alias.
4. Leave fully stable conclusions to the dedicated conclusion extractor when appropriate.

## Tool Calls
- No direct tool calls. The memory manager validates and writes the returned proposals.

## File References
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`
- `projects/<project_slug>/memory/research_log.jsonl`

## Output Contract
- Return JSON with `dynamic_entries` and `knowledge_entries`.
- Allowed alias: `project-lemmas`.

## Notes
- Preserve uncertainty when a claim is only partial or conjectural.
