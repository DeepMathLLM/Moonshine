---
name: math-research-loop
description: Break research work into assumptions, lemmas, obstacles, and next actions.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge
metadata:
  title: Math Research Loop
  category: builtin
  tags: math,research,workflow
  skill-standard: agentskills.io/v1
---

# Math Research Loop

## Usage Hint
- Use this skill to organize an open-ended mathematical investigation into iterative problem, attempt, verification, and refinement steps.
- Use it when the task is not a single proof step but a continuing research process.

## Summary
- Keep mathematical research conversations structured and traceable.
- Separate conjectures, verified statements, obstacles, and next experiments.

## Execution Steps
1. Restate the problem precisely and fix notation.
2. List known assumptions, definitions, and constraints.
3. Separate proven facts from hypotheses and open subclaims.
4. End with the next concrete research action or lemma to inspect.

## Tool Calls
- `query_memory`: Recover prior discussions, decisions, and partial results.
- `search_knowledge`: Reuse previous lemmas and conclusions.

## File References
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return a structured research update with assumptions, active claims, blockers, and next steps.

## Notes
- Prefer concise mathematical terminology over conversational filler.
