---
name: construct-counterexamples
description: Search for examples that satisfy assumptions but violate a proposed claim.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge
metadata:
  title: Construct Counterexamples
  category: builtin
  tags: research,counterexamples,proof
  skill-standard: agentskills.io/v1
---

# Construct Counterexamples

## Usage Hint
- Use this skill to stress-test a proposed theorem by searching for examples that falsify it.
- Use it when assumptions look too weak, a statement feels overgeneral, or a proof attempt exposes a likely boundary case.

## Summary
- Test fragile conjectures and local solve steps by actively looking for counterexamples.
- Use failures to refine assumptions or kill invalid branches.

## Execution Steps
1. State the exact claim to test.
2. Separate assumptions from the desired conclusion.
3. Search common edge cases, degenerate cases, and known pathological families.
4. Check whether each candidate satisfies the assumptions.
5. If a counterexample works, state which branch or claim it refutes.

## Tool Calls
- `query_memory`: Retrieve prior counterexamples and failed paths.
- `search_knowledge`: Check known obstructions and examples.

## File References
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`
- `projects/<project_slug>/memory/by_type/problem.md`

## Output Contract
- Return candidate counterexamples with assumption checks and impact on the research plan.
- When a genuine counterexample is found, state it cleanly; it remains easy to retrieve and reuse later.
- Prior counterexamples can be retrieved from `research_log.jsonl` when relevant.

## Notes
- If no counterexample is found, say only that the search was inconclusive.
