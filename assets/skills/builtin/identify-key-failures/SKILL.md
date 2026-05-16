---
name: identify-key-failures
description: Identify failed mathematical attempts, present the key failure points, and extract lessons that should guide the next research plan.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge
metadata:
  title: Identify Key Failures
  category: builtin
  tags: research,failures,planning
  skill-standard: agentskills.io/v1
---

# Identify Key Failures

## Usage Hint
- Use this skill to diagnose why a research branch or proof strategy failed.
- Use it when repeated unsuccessful attempts, failed estimates, counterexamples, or verifier objections point to a structural obstruction.

## Summary
- Use this skill when repeated attempts to solve a mathematical problem fail and the project needs a disciplined failure diagnosis.
- Identify the key failure point, classify the type of failure, and extract lessons that should guide the next research plan.

## Execution Steps
1. Keep a failure log for the current problem. State the problem, known conditions, and target conclusion, and number each attempt in order.
2. Gather the failed paths, invalid constructions, special-case checks, counterexamples, and stuck solve attempts relevant to the current problem.
3. For each failed attempt, state the core idea in one or two sentences together with the main steps, constructions, or key lemmas used.
4. Identify the point of failure precisely: where the argument got stuck, where a condition was misused, where a calculation broke, or why a proposed counterexample or construction failed.
5. Classify the failure:
   - technical failure: a correctable local error such as a missing condition, miscalculation, or small logical gap;
   - structural failure: the method or decomposition is fundamentally unsuitable for the problem;
   - invalid counterexample: the proposed counterexample violates the hypotheses or does not actually refute the conclusion.
6. Identify the key failure point or the most important recurring obstruction across the current family of failed attempts.
7. Extract lessons and insights:
   - what was learned from the failure,
   - what should be avoided in future attempts,
   - which failed constructions, decompositions, or lemmas may still be reusable elsewhere.
8. Turn those lessons into guidance for the next iteration of plan generation, repair, or reformulation.

## Tool Calls
- `query_memory`: Retrieve failed paths, solve attempts, special-case checks, branch states, and other prior failed work that should be compared.
- `search_knowledge`: Check whether known conclusions, theorem summaries, or obstructions explain the failure.

## File References
- `projects/<project_slug>/memory/by_type/problem.md`
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/memory/by_type/verified_conclusion.md`

## Output Contract
- Return the numbered failed attempts or the key subset being analyzed, the main failure point of each one, the classified failure type, and the synthesized lesson for the next iteration.
- State the main obstruction or transferable lesson clearly.
- Prior failed-path analysis can be retrieved from `research_log.jsonl` when relevant.

## Notes
- Be concrete about the exact step, construction, condition, or counterexample attempt that fails.
- Prefer failure diagnoses that help the next research-plan iteration rather than merely restating that an attempt did not work.
