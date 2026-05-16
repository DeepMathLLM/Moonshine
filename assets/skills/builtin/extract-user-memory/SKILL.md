---
name: extract-user-memory
description: Extract durable user profile, preference, correction, and success-pattern memory proposals.
compatibility: Requires an Agent Skills-compatible runtime that validates dynamic-memory proposals before writing them.
metadata:
  title: Extract User Memory
  category: internal
  tags: memory,user,preferences
  skill-standard: agentskills.io/v1
---

# Extract User Memory

## Usage Hint
- Use this skill to identify durable user preferences or standing instructions from conversation text.
- Use it when the user states a stable preference, workflow rule, or long-term constraint rather than a one-off request.

## Summary
- Identify durable user-level information that should persist across projects and sessions.
- Focus on stable preferences, background facts, corrections, and successful collaboration patterns.

## Execution Steps
1. Read the provided trigger payload and conversation window.
2. Extract only durable user-level facts that are likely to matter later.
3. Map each fact to one of the allowed aliases.
4. Return strict JSON proposals without writing files directly.

## Tool Calls
- No direct tool calls. The memory manager validates and writes the returned proposals.

## File References
- `memory/user/profile.md`
- `memory/user/preferences.md`
- `memory/feedback/corrections.md`
- `memory/feedback/success_patterns.md`

## Output Contract
- Return JSON with `dynamic_entries` and `knowledge_entries`.
- Allowed aliases: `user-profile`, `user-preferences`, `feedback-corrections`, `feedback-success`.

## Notes
- Skip transient requests or one-off wording choices that are unlikely to matter later.
