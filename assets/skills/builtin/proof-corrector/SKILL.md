---
name: proof-corrector
description: Repair solve attempts after verifier feedback or discovered gaps.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge
metadata:
  title: Proof Corrector
  category: builtin
  tags: research,proof,repair
  skill-standard: agentskills.io/v1
---

# Proof Corrector

## Usage Hint
- Use this skill to repair a proof after gaps, verifier failures, or counterexamples have been identified.
- Use it when the target statement may still be salvageable but the current argument is not correct.

## Summary
- Use verifier feedback to repair a proof or decide that a branch must be abandoned.
- Prioritize critical errors before polishing exposition.

## Execution Steps
1. List verifier errors and gaps by severity.
2. Determine whether each issue is local, structural, or fatal.
3. Repair local gaps with explicit arguments.
4. If the route is invalid, identify the failed path and propose an alternative.
5. Present corrected lemmas or conclusions only when justified.

## Tool Calls
- `query_memory`: Retrieve prior corrections and failed paths.
- `search_knowledge`: Check reusable repaired lemmas.

## File References
- `projects/<project_slug>/memory/by_type/problem.md`
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`
- `projects/<project_slug>/memory/by_type/research_note.md`

## Output Contract
- Return a repair plan, corrected proof fragments, and any branch decisions.

## Persistence Guidance
- If a route is invalid, state a concise failed-path summary.
- If the route remains viable but needs more work, state a concise solve-attempt checkpoint.

## Notes
- Do not patch over a fatal counterexample.
