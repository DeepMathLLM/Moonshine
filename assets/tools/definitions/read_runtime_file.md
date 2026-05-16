<!--
{
  "name": "read_runtime_file",
  "handler": "read_runtime_file",
  "description": "Read a runtime-managed file such as project drafts, rules, AGENTS guidance, local references, or session records.",
  "parameters": {
    "type": "object",
    "properties": {
      "relative_path": {"type": "string", "description": "Path relative to the active project root when a project is active; otherwise relative to the Moonshine runtime home. Use inputs/..., knowledge/..., or sessions/... for home-level runtime files, and projects/<other-project>/... when you need a file from a different project. Legacy projects/<active-project>/... paths are accepted and normalized. Absolute paths are accepted only when they stay inside Moonshine home."}
    },
    "required": ["relative_path"]
  }
}
-->

# Tool: read_runtime_file

## Usage Hint
- Use this tool to inspect runtime-managed files inside the Moonshine workspace.
- In research mode, use project-relative paths such as `workspace/problem.md`, `memory/research_log.md`, `memory/research_log.jsonl`, `memory/by_type/verified_conclusion.md`, or `references/notes/source.md`.
- Use `inputs/<filename>` for source material staged into Moonshine home outside any specific project.
- Use `projects/<other-project>/memory/research_log.md`, `projects/<other-project>/memory/research_log.jsonl`, or another path returned by `query_memory` when you need the original record from a different project.
- Use `knowledge/KNOWLEDGE.md` or `knowledge/entries/<id>.md` only when you need to inspect global knowledge files directly; prefer `search_knowledge` for semantic knowledge retrieval.
- Use `sessions/<session_id>/messages.jsonl`, `sessions/<session_id>/tool_events.jsonl`, `sessions/<session_id>/provider_rounds.jsonl`, or paths returned by `query_session_records` when exact raw session history is needed.
- Legacy `projects/<active-project>/...` paths are accepted, but project-relative paths are preferred.

