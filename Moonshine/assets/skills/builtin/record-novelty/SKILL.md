---
name: record-novelty
description: Extract and record valuable new concepts, methods, or theory-level ideas from the current branch, solution, or exploration, optionally checking them against known work when helpful.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: read_runtime_file query_memory search_knowledge
metadata:
  title: Record Novelty
  category: builtin
  tags: research,novelty,writing
  skill-standard: agentskills.io/v1
---

# Record Novelty

## Usage Hint
- Use this skill to reason about whether a problem, lemma, or result appears novel relative to known context.
- Use it when novelty matters for choosing, refining, or presenting a research direction.

## Summary
- Use this skill when a branch, solution, framework, or completed exploration appears to contain something genuinely new.
- The goal is not to prove correctness again, but to identify and record valuable new concepts, methods, tools, or theory-level ideas that emerged from the current work.

## Execution Steps
1. Deconstruct the core structure of the solution of the mathematical problem in the file.
2. Identify the concepts, methods, and tools used in that solution.
3. Compare the current solution with the closest known work:
   - find similar existing mathematical results;
   - compare the involved concepts, methods, and tools point by point with those identified in the current solution.
4. Summarize the comparison and identify the genuinely unprecedented elements among the concepts, methods, and tools used in the current solution:
   - new concept: does the solution define an object, property, or classification not clearly defined by previous authors?
   - new method: does it introduce a cross-disciplinary technique or make a fundamental variation of a classical method?
   - new theory: does it construct a new theoretical framework or a new axiomatic system?
5. State the novelty clearly and conservatively. If the idea is only tentative, partial, or local to one branch, say so explicitly instead of overstating it.
6. State the novelty cleanly with its scope and evidence.

## Tool Calls
- `read_runtime_file`: Read the current local solution draft, blueprint, or nearby notes that contain the idea whose novelty should be recorded.
- `query_memory`: Recover prior project notes, earlier novelty notes, or nearby branch history when the current novelty statement depends on previous project work.
- `search_knowledge`: Check whether a concept, method, or theoretical idea already resembles something that was previously stored as known work or stable project knowledge.

## File References
- `projects/<project_slug>/workspace/blueprint.md`
- `projects/<project_slug>/workspace/blueprint_verified.md`
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/references/`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return a concise novelty note about the current work.
- State clearly whether the value lies in a new concept, a new method, a new theory-level idea, or in no substantial novelty worth preserving yet.
- Present the novelty note clearly enough for later retrieval.

## Notes
- Prefer conservative, evidence-based novelty claims over inflated wording.
- This skill is for recording novelty extracted from the current work itself, not for proving priority claims against the whole literature.
