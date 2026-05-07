# Moonshine

**A local agent framework for autonomous mathematical research, exploration, and evolution.**

Not a Q&A tool. It's a command-line workspace where an LLM can sit with a problem—following long proof attempts, searching its own past thoughts, calling specialized tools, and leaving behind a complete, verifiable research trail.

Moonshine is built for workflows where **a single answer is never enough.**

It keeps working inside a named project. Resumes an old session at your request. Inspects every tool call and model trace. Retrieves verified conclusions. And double-checks before accepting any important mathematical claim.

---

## What you can do with it

- A **persistent research notebook** with an agent living inside it
- An **autonomous proof-search and counterexample-search loop**
- A **project memory system** for mathematical experiments
- An **iterative research exploration and capability evolution** framework
- A **tool/skill harness** for custom research agents

---

## Contents

- [Moonshine](#moonshine)
  - [What you can do with it](#what-you-can-do-with-it)
  - [Contents](#contents)
  - [Requirements](#requirements)
  - [Installation](#installation)
  - [Initial Setup](#initial-setup)
  - [Provider Configuration](#provider-configuration)
    - [OpenAI API](#openai-api)
    - [Other OpenAI-Compatible Providers](#other-openai-compatible-providers)
    - [Azure OpenAI](#azure-openai)
    - [Verification Provider](#verification-provider)
    - [Useful Provider Commands](#useful-provider-commands)
  - [Quick Start](#quick-start)
  - [Chat Mode](#chat-mode)
  - [Research Mode](#research-mode)
    - [Suggested Research Prompts](#suggested-research-prompts)
    - [Research Verification](#research-verification)
  - [Working With Files](#working-with-files)
    - [Project Files](#project-files)
    - [Input Files](#input-files)
    - [Path Rules](#path-rules)
  - [Memory and Retrieval](#memory-and-retrieval)
    - [Project Research Log](#project-research-log)
    - [Search Project Memory](#search-project-memory)
    - [Search Global Knowledge](#search-global-knowledge)
    - [Search Raw Session History](#search-raw-session-history)
  - [Skills, Tools, Agents, and MCP](#skills-tools-agents-and-mcp)
    - [Skills](#skills)
    - [Tools](#tools)
    - [Agents](#agents)
    - [MCP](#mcp)
    - [Exposure Config](#exposure-config)
  - [Runtime Layout](#runtime-layout)
  - [Testing](#testing)
  - [Troubleshooting](#troubleshooting)
  - [Status](#status)

## Requirements

Required:

- Python 3.8+
- A terminal/shell environment
- A configured LLM provider, unless you only want to inspect the CLI in offline mode

Recommended optional dependencies are installed with:

```bash
python -m pip install -e ".[all]" --no-build-isolation
```

The `all` extra includes optional packages used by token counting, vector search,
and workflow-related features.

## Installation

From the package directory:

```bash
cd moonshine
python -m pip install -U pip setuptools wheel
python -m pip install -e ".[all]" --no-build-isolation
```

After installation, initialize the runtime home:

```bash
python -m moonshine init
```

Check optional dependency status:

```bash
python -m moonshine init --check-deps
```

Install optional dependencies from the CLI:

```bash
python -m moonshine init --install-deps
```

## Initial Setup

Moonshine stores runtime data under `MOONSHINE_HOME`.

Default runtime home:

```text
~/.moonshine
```

Use a custom runtime home:

```bash
python -m moonshine --home /path/to/.moonshine init
python -m moonshine --home /path/to/.moonshine provider --show
python -m moonshine --home /path/to/.moonshine shell --mode chat --project general
```

Important runtime files:

```text
config/settings.json       # non-secret configuration
config/credentials.json    # locally stored API keys
projects/                  # research projects
sessions/                  # raw session records
knowledge/                 # global reusable conclusions
skills/                    # runtime skill definitions
tools/                     # runtime tool and MCP definitions
agents/                    # runtime agent definitions
```

Inspect the current provider setup:

```bash
python -m moonshine provider --show
```

## Provider Configuration

Moonshine supports:

- `offline`
- `azure_openai`
- `openai_compatible`

Use `openai_compatible` for OpenAI's API and for other services that expose an
OpenAI-compatible chat-completions endpoint.

### OpenAI API

```bash
python -m moonshine provider --target main --type openai_compatible
python -m moonshine provider --target main --base-url "https://api.openai.com/v1"
python -m moonshine provider --target main --model "gpt-xxx"
python -m moonshine provider --target main --api-key-env "OPENAI_API_KEY"
python -m moonshine provider --target main --set-api-key
python -m moonshine provider --target main --stream
```

`--set-api-key` can be used interactively, or you can pass the key directly:

```bash
python -m moonshine provider --target main --set-api-key "sk-..."
```

### Other OpenAI-Compatible Providers

```bash
python -m moonshine provider --target main --type openai_compatible
python -m moonshine provider --target main --base-url "https://your-provider.example/v1"
python -m moonshine provider --target main --model "your-model-name"
python -m moonshine provider --target main --api-key-env "YOUR_PROVIDER_API_KEY"
python -m moonshine provider --target main --set-api-key
```

### Azure OpenAI

```bash
python -m moonshine provider --target main --type azure_openai
python -m moonshine provider --target main --endpoint "https://your-resource.openai.azure.com/"
python -m moonshine provider --target main --deployment "your-deployment"
python -m moonshine provider --target main --api-version "2024-12-01-preview"
python -m moonshine provider --target main --api-key-env "AZURE_OPENAI_API_KEY"
python -m moonshine provider --target main --set-api-key
```

### Verification Provider

Verification uses the main provider by default.

Use a dedicated verification provider:

```bash
python -m moonshine provider --target verification --dedicated
python -m moonshine provider --target verification --type openai_compatible
python -m moonshine provider --target verification --base-url "https://api.openai.com/v1"
python -m moonshine provider --target verification --model "gpt-4.1-mini"
python -m moonshine provider --target verification --set-api-key
```

Return verification to the main provider:

```bash
python -m moonshine provider --target verification --inherit-main
```

### Useful Provider Commands

```bash
python -m moonshine provider --show
python -m moonshine provider --target main --no-stream
python -m moonshine provider --target main --stream
python -m moonshine provider --target main --temperature 0.2
python -m moonshine provider --target main --clear-temperature
python -m moonshine provider --target main --max-context-tokens 0
```

## Quick Start

Start a normal chat session:

```bash
python -m moonshine shell --mode chat --project general
```

Ask one question and exit:

```bash
python -m moonshine ask --mode chat --project general "Explain Nakayama's lemma."
```

Start an autonomous research session:

```bash
python -m moonshine shell --mode research --project my_research_project
```

Run an autonomous research prompt:

```bash
python -m moonshine ask --mode research --project my_research_project \
  --max-iterations 20 \
  "Study the current problem and continue the research."
```

Run one research turn only:

```bash
python -m moonshine ask --mode research --project my_research_project \
  --interactive \
  "Read the current notes and summarize the next step."
```

Resume a session:

```bash
python -m moonshine shell --mode research \
  --project my_research_project \
  --session session-xxxxxxxxxx
```

Start with an input file:

```bash
python -m moonshine shell --mode research \
  --project my_research_project \
  --input-file /path/to/problem-or-notes.md
```

## Chat Mode

Chat mode is for ordinary assistant use.

Start chat mode:

```bash
python -m moonshine shell --mode chat --project general
```

Run a one-shot chat prompt:

```bash
python -m moonshine ask --mode chat --project general \
  "Compare Noetherian and Artinian rings."
```

Useful chat-mode commands:

```text
/help
/mode chat
/project general
/sessions
/knowledge search <query>
/skills
/tools
/exit
```

## Research Mode

Research mode is for project-based mathematical work.

It supports:

- reading and organizing project notes,
- refining a research problem,
- assessing problem quality,
- searching previous project memory,
- searching reusable global knowledge,
- inspecting exact raw session history,
- trying proof strategies,
- constructing counterexamples,
- recording failed paths,
- checking intermediate claims,
- verifying final results,
- resuming the same project later.

Start research mode:

```bash
python -m moonshine shell --mode research --project my_research_project
```

Autonomous mode is enabled by default in research mode. Limit the number of
iterations:

```bash
python -m moonshine shell --mode research \
  --project my_research_project \
  --max-iterations 50
```

Disable autonomous iteration for manual turn-by-turn work:

```bash
python -m moonshine shell --mode research \
  --project my_research_project \
  --interactive
```

### Suggested Research Prompts

Start from a topic:

```text
Explore possible research problems around <topic>. Assess problem quality before solving.
```

Start from a problem:

```text
Read workspace/problem.md and continue the research. Search prior project memory first if useful.
```

Continue an existing project:

```text
Continue focused work on the current research progress. Use verification tools for important claims.
```

Ask for final review:

```text
If the current result is final, call verify_overall with scope="final" and summarize the verified result.
```

### Research Verification

Available verification tools:

```text
assess_problem_quality
verify_correctness_assumption
verify_correctness_computation
verify_correctness_logic
verify_overall
```

Use `assess_problem_quality` before treating a candidate problem as ready for
serious problem solving.

Use final verification for final project-level results:

```text
verify_overall(scope="final")
```

Use intermediate verification for lemmas, computations, reductions, and partial
claims.

## Working With Files

### Project Files

Each project lives under:

```text
projects/<project-slug>/
```

Common files:

```text
workspace/problem.md
workspace/blueprint.md
workspace/blueprint_verified.md
references/notes/
references/papers/
references/surveys/
memory/research_log.jsonl
memory/research_log.md
memory/research_log_index.sqlite
memory/by_type/
```

### Input Files

Pass a local file at startup:

```bash
python -m moonshine shell --mode research \
  --project my_research_project \
  --input-file /path/to/source.md
```

Moonshine stages the file into the runtime home and asks the agent to read it.

You can also place files manually under:

```text
projects/<project-slug>/references/notes/
projects/<project-slug>/references/papers/
projects/<project-slug>/workspace/
```

### Path Rules

When a project is active, use project-relative paths:

```text
workspace/problem.md
memory/research_log.md
memory/research_log.jsonl
memory/by_type/verified_conclusion.md
references/notes/source.md
```

These legacy paths are also accepted:

```text
projects/<active-project>/workspace/problem.md
```

Global runtime paths:

```text
knowledge/...
sessions/...
```

Examples:

```text
read_runtime_file(relative_path="workspace/problem.md")
read_runtime_file(relative_path="memory/research_log.md")
read_runtime_file(relative_path="knowledge/KNOWLEDGE.md")
read_runtime_file(relative_path="sessions/<session-id>/messages.jsonl")
```

Filesystem MCP tools are project-scoped by default:

```text
mcp_filesystem_read_file(path="workspace/problem.md")
mcp_filesystem_write_file(path="workspace/notes.md", content="...")
```

## Memory and Retrieval

### Project Research Log

Main project log:

```text
memory/research_log.jsonl
```

Readable log:

```text
memory/research_log.md
```

By-type files:

```text
memory/by_type/
```

Search index:

```text
memory/research_log_index.sqlite
```

Research log types:

```text
problem
verified_conclusion
verification
final_result
counterexample
failed_path
research_note
```

### Search Project Memory

```text
query_memory(query="phase mismatch lemma")
```

Search a specific record type:

```text
query_memory(query="phase mismatch lemma", types=["verified_conclusion"])
```

Search across projects:

```text
query_memory(query="polynomial reduction", all_projects=true)
```

### Search Global Knowledge

```text
search_knowledge(query="commensurate slope polynomial reduction")
```

Read the global knowledge index:

```text
read_runtime_file(relative_path="knowledge/KNOWLEDGE.md")
```

Read a specific knowledge entry:

```text
read_runtime_file(relative_path="knowledge/entries/<entry-id>.md")
```

### Search Raw Session History

Search exact records:

```text
query_session_records(query="record_failed_path", session_id="session-...")
```

Common returned paths:

```text
sessions/<session-id>/messages.jsonl
sessions/<session-id>/tool_events.jsonl
sessions/<session-id>/provider_rounds.jsonl
sessions/<session-id>/context_summaries.jsonl
sessions/<session-id>/turns/<round-id>.json.gz
```

Read plain session files:

```text
read_runtime_file(relative_path="sessions/<session-id>/messages.jsonl")
read_runtime_file(relative_path="sessions/<session-id>/tool_events.jsonl")
read_runtime_file(relative_path="sessions/<session-id>/provider_rounds.jsonl")
```

## Skills, Tools, Agents, and MCP

### Skills

List skills:

```bash
python -m moonshine skills
```

Inside shell:

```text
/skills
/skills show <skill-slug>
```

### Tools

List tools:

```bash
python -m moonshine tools
```

Inside shell:

```text
/tools
/tools show <tool-name>
```

### Agents

List or inspect agents:

```bash
python -m moonshine agent
python -m moonshine agent --show research-control-loop
```

Inside shell:

```text
/agent
/agent show <agent-slug>
```

### MCP

List MCP descriptors:

```bash
python -m moonshine mcp
```

Inspect a descriptor:

```bash
python -m moonshine mcp --show filesystem
```

Configure Tavily:

```bash
python -m moonshine mcp --set-tavily-key "tvly-..."
python -m moonshine mcp --enable-tavily
```

Inside shell:

```text
/mcp
/mcp show filesystem
/mcp tavily set-key <api-key>
/mcp tavily enable
/mcp tavily disable
```

### Exposure Config

Control exposed tools and skills in:

```text
config/settings.json
```

```json
{
  "exposure": {
    "tools_include": [],
    "tools_exclude": [],
    "skills_include": [],
    "skills_exclude": []
  }
}
```

An empty include list means all available items are allowed except those in the
matching exclude list.

## Runtime Layout

Default runtime home:

```text
~/.moonshine
```

Typical layout:

```text
MOONSHINE_HOME/
  config/
    settings.json
    credentials.json
  knowledge/
    KNOWLEDGE.md
    conclusions.sqlite3
    entries/
  sessions/
    <session-id>/
      session.json
      messages.jsonl
      transcript.md
      tool_events.jsonl
      turn_events.jsonl
      provider_rounds.jsonl
      context_summaries.jsonl
      turns/
  agents/
  skills/
  tools/
  projects/
    <project-slug>/
      AGENTS.md
      rules.md
      workspace/
      references/
      memory/
```

## Testing

Run all architecture tests:

```bash
cd moonshine
python -m unittest moonshine.tests.test_architecture
```

Run one targeted test:

```bash
python -m unittest moonshine.tests.test_architecture.MoonshineArchitectureTestCase.test_mcp_filesystem_normalizes_legacy_project_prefix_for_writes
```

## Troubleshooting

Show provider config:

```bash
python -m moonshine provider --show
```

List sessions:

```bash
python -m moonshine sessions
```

Search session text:

```bash
python -m moonshine sessions --search "keyword"
```

List tools:

```bash
python -m moonshine tools
```

List MCP descriptors:

```bash
python -m moonshine mcp
```

Check dependency imports:

```bash
python -m moonshine init --check-deps
```

## Status

Moonshine is experimental. It is intended for local, inspectable, project-based
agent workflows for mathematical research, long-running problem solving, and
reusable research memory.
