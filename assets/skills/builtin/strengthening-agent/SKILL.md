---
name: strengthening-agent
description: After repeated failures, modify assumptions or conclusions to find a stronger useful result.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge
metadata:
  title: Strengthening Agent
  category: builtin
  tags: research,strengthening,repair
  skill-standard: agentskills.io/v1
---

# Strengthening Agent

## Usage Hint
- Use this skill to look for stronger statements, sharper hypotheses, or better formulations after a partial result.
- Use it when the current theorem is correct but may be weak, nonoptimal, or missing a natural extension.

## Summary
- Explore whether adding assumptions, changing scope, or proving a stronger intermediate statement makes progress possible.
- Use only after concrete failures have been identified.

## Execution Steps
1. Review failed paths and the obstruction they reveal.
2. Propose modified hypotheses or stronger intermediate claims.
3. Explain how the modification addresses the obstruction.
4. Check whether the new statement remains meaningful and nontrivial.
5. Recommend a revised proof route.

## Tool Calls
- `query_memory`: Retrieve failed paths and branch states.
- `search_knowledge`: Check whether strengthened variants are already known.

## File References
- `projects/<project_slug>/memory/by_type/problem.md`
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return strengthened variants, motivation, risks, and next solve steps.

## Notes
- Strengthening should clarify the mathematics, not merely evade the problem.
