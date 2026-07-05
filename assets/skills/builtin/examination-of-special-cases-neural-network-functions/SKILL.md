---
name: examination-of-special-cases-neural-network-functions
description: Examine special neural-network-function cases to test whether a problem's conclusion survives concrete simple examples.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: read_runtime_file query_memory search_knowledge
metadata:
  title: Examination of Special Cases for Neural-Network Functions
  category: builtin
  tags: research,special-cases,neural-networks,functions
  skill-standard: agentskills.io/v1
---

# Examination of Special Cases for Neural-Network Functions

## Usage Hint
- Use this skill to analyze restricted neural-network-function cases before attacking the full statement.
- Use it when small widths, special weights, commensurate slopes, or normalized parameters may reveal the right theorem or obstruction.

## Summary
- Test the conclusion of a neural-network-function problem on simple, concrete special cases before trusting the general formulation.
- Use special neural-network functions to check whether the current formulation survives concrete simple instances and to reveal simplifications or structural patterns without attempting a full proof.

## Execution Steps
1. Understand the conclusion of the problem: state clearly what the problem claims and under which general assumptions or conditions the conclusion is supposed to hold.
2. Select several special neural-network functions as test objects, favoring simple architectures, low neuron counts, special parameter choices, or highly structured weights.
3. Substitute those special neural-network functions into the original problem framework, replacing the general functions or abstract conditions.
4. Check case by case whether the claimed conclusion still holds.
5. If a tested special case does not support the current conclusion, state that this special-case check exposes a serious obstruction or a formulation that does not survive the tested instance.
6. If a special case supports the conclusion, note any simplification, extra pattern, or structural clue suggested by that case.
7. Keep the result at the level of special-case examination rather than a full proof.

## Tool Calls
- `read_runtime_file`: Read the local problem statement and any local notes defining the neural-network-function setting.
- `query_memory`: Recover prior special-case checks, branch notes, or earlier related attempts.
- `search_knowledge`: Reuse known theorem summaries, related conclusions, or known obstructions.

## File References
- `projects/<project_slug>/workspace/problem.md`
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/references/`

## Output Contract
- Return tested special cases together with whether the conclusion holds in each case.
- If a special case does not support the current conclusion, state clearly why the tested instance exposes a serious obstruction or why the current formulation does not survive that case.
- If a special case succeeds, state any extra pattern, simplification, or structural clue it reveals.
- When a special-case result materially changes the branch or formulation, state that effect explicitly.

## Notes
- Prefer this skill when a neural-network-function problem should first be sanity-checked on simple architectures, special weights, or simple examples before deeper proof search.
- This skill is for testing special cases, not for replacing a full general proof.
