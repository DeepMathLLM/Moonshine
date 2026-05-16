---
name: literature-survey
description: Survey local project references and summarize relevant mathematical background for a research topic.
compatibility: Works in Agent Skills-compatible research runtimes with project reference files.
allowed-tools: read_runtime_file query_memory search_knowledge
metadata:
  title: Literature Survey
  category: builtin
  tags: research,literature,math
  skill-standard: agentskills.io/v1
---

# Literature Survey

## Usage Hint
- Use this skill to check project references or external literature before relying on novelty or known-theorem assumptions.
- Use it when the next step depends on what is already known, cited, or available in the reference folder.

## Summary
- Review project-local references before relying on external claims.
- Produce a compact survey of definitions, known results, proof techniques, and open gaps.

## Execution Steps
1. Read `projects/<project_slug>/references/index.jsonl` if it exists.
2. Load only relevant notes or survey files from the project reference folders.
3. If reference text is too large, summarize it before using it in the active context.
4. Extract statements, assumptions, terminology, and proof ideas that matter for the current topic.
5. Identify unresolved gaps and suggested next references.

## Tool Calls
- `read_runtime_file`: Load local reference indexes, notes, and survey summaries.
- `query_memory`: Retrieve prior literature notes or related project history.
- `search_knowledge`: Reuse known theorem and conclusion summaries.

## File References
- `projects/<project_slug>/references/index.jsonl`
- `projects/<project_slug>/references/notes/`
- `projects/<project_slug>/references/surveys/`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return a source-aware literature summary with useful statements, applicability checks, and open questions.

## Notes
- Do not treat identical terminology across papers as identical without checking local definitions.
