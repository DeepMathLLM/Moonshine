---
name: problem-refiner
description: Refine a candidate research problem after quality assessment or failed attempts.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge
metadata:
  title: Problem Refiner
  category: builtin
  tags: research,iteration,question-design
  skill-standard: agentskills.io/v1
---

# Problem Refiner

## Usage Hint
- Use this skill to tighten, narrow, or repair a candidate research problem.
- Use it when quality assessment, failed attempts, ambiguity, or counterexamples show that the current statement needs adjustment.

## Summary
- Improve a candidate problem by adjusting assumptions, scope, and target conclusion.
- Use this after quality assessment, counterexamples, or feasibility concerns.

## Execution Steps
1. Identify the main weakness in the current problem statement.
2. Propose refined versions with clearer hypotheses or stronger motivation.
3. Explain what changed and why it improves the problem.
4. Preserve rejected variants and reasons for future reference.

## Tool Calls
- `query_memory`: Retrieve previous refinements and failed variants.
- `search_knowledge`: Check whether the refined statement overlaps stored results.

## File References
- `projects/<project_slug>/memory/by_type/problem.md`
- `projects/<project_slug>/memory/by_type/research_note.md`

## Output Contract
- Return refined problem statements and a short decision memo.

## Notes
- Refinement should reduce ambiguity without trivializing the question.
