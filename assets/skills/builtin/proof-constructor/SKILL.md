---
name: proof-constructor
description: Integrate solved lemmas and solve steps into a coherent formal draft.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge verify_overall verify_correctness_assumption verify_correctness_computation verify_correctness_logic
metadata:
  title: Proof Constructor
  category: builtin
  tags: research,proof,integration
  skill-standard: agentskills.io/v1
---

# Proof Constructor

## Usage Hint
- Use this skill to assemble a coherent proof from established lemmas, reductions, and checked subarguments.
- Use it when enough pieces exist and the main task is proof integration rather than discovering a new branch.

## Summary
- Assemble partial results into a readable mathematical proof draft.
- Keep assumptions, definitions, lemmas, and theorem order explicit.

## Execution Steps
1. Retrieve solved subgoals, partial lemmas, and relevant references.
2. Check dependency order before writing the proof.
3. State lemmas before using them.
4. Assemble the main theorem proof last.
5. Flag any remaining gaps instead of hiding them.

## Tool Calls
- `query_memory`: Gather solve steps and branch outcomes.
- `search_knowledge`: Retrieve reusable lemmas and conclusions.
- `verify_overall`: Check a near-complete integrated argument or important integrated subclaim.
- `verify_correctness_assumption`, `verify_correctness_computation`, `verify_correctness_logic`: Use only when one verification dimension needs targeted diagnosis.

## File References
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return a formal draft plus a list of unresolved gaps and dependencies.
- When the formal project proof meaningfully improves, state the improved proof clearly in ordinary prose so later turns can retrieve and reuse it.
- If the integration result is not yet final, state the checkpoint clearly rather than forcing premature finality.
- Prior proof-integration progress can be retrieved from `research_log.jsonl` when relevant.

## Notes
- Do not claim the proof is complete until verification passes.
- Keep dead ends, rejected branches, and failed experiments clearly labeled separately from accepted proof progress.
