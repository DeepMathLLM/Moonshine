---
name: construct-toy-examples
description: Build simple examples that illuminate assumptions, mechanisms, or proof ideas.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge
metadata:
  title: Construct Toy Examples
  category: builtin
  tags: research,examples,intuition
  skill-standard: agentskills.io/v1
---

# Construct Toy Examples

## Usage Hint
- Use this skill to explore small or simplified examples before committing to a general proof.
- Use it when definitions, conjectured behavior, or edge cases are still unclear.

## Summary
- Generate small examples satisfying the assumptions to reveal structure.
- Use toy examples to identify invariants, reductions, and likely proof routes.

## Execution Steps
1. Restate the target claim and assumptions.
2. Construct simple examples in low-dimensional or familiar cases.
3. Verify which assumptions and conclusions hold.
4. Extract patterns or proof hints suggested by the examples.
5. Note limitations of the examples.
6. When an example materially changes the current direction, assumptions, or branch priority, state that consequence explicitly.
7. `## Toy Example` is optional readability or fallback capture, not the primary persistence path.

## Tool Calls
- `query_memory`: Reuse prior examples and example-driven insights.
- `search_knowledge`: Check stored example-related conclusions.

## File References
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`
- `projects/<project_slug>/memory/by_type/research_note.md`

## Output Contract
- Natural language is fine.
- If a toy example changes the current direction, assumptions, or branch priority, make that mathematical change explicit; its mathematical consequence is easy to retrieve and reuse later.

## Notes
- Toy examples guide reasoning but do not replace proof.
