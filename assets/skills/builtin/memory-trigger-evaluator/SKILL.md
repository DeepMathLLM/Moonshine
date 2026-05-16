---
name: memory-trigger-evaluator
description: Decide which specialized memory extraction skills should run for a lifecycle trigger.
compatibility: Requires an Agent Skills-compatible runtime that can invoke internal extraction skills and write validated memory proposals.
metadata:
  title: Memory Trigger Evaluator
  category: internal
  tags: memory,trigger,evaluation
  skill-standard: agentskills.io/v1
---

# Memory Trigger Evaluator

## Usage Hint
- Use this skill to decide whether a conversation window contains information worth remembering.
- Use it when memory extraction requires deciding whether information is durable memory or transient discussion.

## Summary
- Inspect a lifecycle event and decide whether memory extraction should run.
- Route the event to the minimum useful set of specialized extraction skills.

## Execution Steps
1. Read the trigger type, scope, and provided conversation window.
2. Decide whether the material contains durable information worth storing.
3. Select only the extraction skills that match the evidence.
4. Return strict JSON naming the chosen skills and the reason.

## Tool Calls
- No external tool calls are required. This skill returns routing decisions for the memory manager.

## File References
- `memory/MEMORY.md`
- `projects/<project_slug>/memory/research_log.jsonl`
- `projects/<project_slug>/memory/by_type/research_note.md`
- `projects/<project_slug>/memory/research_log_index.sqlite`

## Output Contract
- Return JSON with `run`, `skills`, `reason`, and optional `notes`.

## Notes
- Do not propose explicit-memory writes; direct user remember commands are handled deterministically elsewhere.
