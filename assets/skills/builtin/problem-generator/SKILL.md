---
name: problem-generator
description: Generate candidate research problems from a topic, literature scan, and project memory.
compatibility: Works in Agent Skills-compatible research runtimes.
allowed-tools: query_memory search_knowledge
metadata:
  title: Problem Generator
  category: builtin
  tags: research,question-design
  skill-standard: agentskills.io/v1
---

# Problem Generator

## Usage Hint
- Use this skill to generate candidate research problems from a topic, background, or broad direction.
- Use it when the project is still early and does not yet have a fixed problem statement.

## Summary
- Generate focused, researchable mathematical questions from a broad topic.
- Prefer questions with clear assumptions, expected outputs, and reusable subproblems.

## Execution Steps
1. Restate the topic and relevant background.
2. Generate several candidate questions with precise hypotheses.
3. For each candidate, list likely techniques and possible obstructions.
4. Identify dependencies on known results or missing definitions.
5. Recommend a small shortlist for quality evaluation.
6. State shortlist-worthy candidates clearly.
7. Use `## Candidate Problem` only as optional readability or fallback capture, not as the primary persistence path.
8. When one candidate becomes the working target, state that selection clearly in ordinary prose with enough precision for later retrieval.

## Tool Calls
- `query_memory`: Avoid regenerating prior questions or failed directions.
- `search_knowledge`: Reuse known conclusions and theorem summaries.

## File References
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/references/surveys/`

## Output Contract
- Natural language is fine.
- When a candidate should survive long iterations, present it clearly enough for later retrieval.
- When you are no longer just proposing but actually selecting the working target, state the selected problem explicitly rather than leaving the active problem implicit.
- If repeated generation work stalls, explain the failure mode or blocked direction explicitly in the output itself.

## Notes
- A good problem should be precise enough to attack, not merely interesting.
