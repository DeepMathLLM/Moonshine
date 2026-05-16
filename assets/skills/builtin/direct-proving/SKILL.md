---
name: direct-proving
description: Try to solve selected subgoals directly and identify stuck points.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge
metadata:
  title: Direct Proving
  category: builtin
  tags: research,proof,reasoning
  skill-standard: agentskills.io/v1
---

# Direct Proving

## Usage Hint
- Use this skill to attempt a direct proof of a precise theorem, lemma, or selected subgoal.
- Use it when the statement is already clear and the next useful move is to exploit the hypotheses rather than redesign the problem.

## Summary
- Attempt direct proofs for selected subgoals using available memory and known results.
- If a plan fails, identify the concrete mathematical failure mode.

## Execution Steps
1. Select one decomposition plan or subgoal.
2. State all assumptions and definitions needed for the proof.
3. Try a direct argument, reduction, or adaptation of a known proof.
4. Mark each proof step as solved, partial, or stuck.
5. State stable lemmas, partial conclusions, and blockers clearly.

## Tool Calls
- `query_memory`: Retrieve related solve attempts and failed paths.
- `search_knowledge`: Find reusable conclusions.

## File References
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return solve attempts, solved steps, stuck points, and next actions.

## Retrieval Guidance
- When a direct attempt materially advances the branch but is not yet a formal conclusion, state the claim, assumptions, evidence, and remaining gap clearly.
- Prior proof progress can be retrieved from `research_log.jsonl` when relevant.

## Notes
- A failed proof is useful if it clearly identifies what breaks.
