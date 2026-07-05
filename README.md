# Moonshine

**Moonshine is an autonomous mathematical research agent whose central
objective is to generate conjectures.**

Its core capability is to extract structure from classical problems, distill
new concepts, and formulate conjectures of mathematical significance. Rather
than treating the solution of a single proposition as its endpoint, Moonshine
is built to grow an extensible theoretical framework through conjecture
generation, bridge building, obstacle identification, and verification.

Moonshine can also run as a normal chat assistant, but its main strength is
research mode: a persistent project workspace where the agent can explore a
problem across many turns and sessions while keeping a traceable research
record.

## Highlights

- Conjecture generation from mathematical structure and classical problems.
- Bridge building between problems, methods, concepts, and examples.
- Obstacle identification through failed paths and counterexample search.
- Chat mode and research mode from one CLI.
- Persistent projects, sessions, memory, and knowledge.
- Autonomous research iteration with configurable iteration limits.
- Project research logs with typed records and by-type Markdown views.
- Verification tools for assumptions, computations, logic, and final results.
- Independent provider slots for main, verification, and archival calls.
- OpenAI-compatible chat-completions, OpenAI Responses, Azure OpenAI, and offline mode.
- Reasoning effort and reasoning summary settings for compatible providers.
- Session continuation with raw message, provider-round, and tool-event traces.
- Unified session retrieval for messages and non-retrieval tool results.
- Indexed tool-event retrieval with large payloads kept recoverable.
- Markdown-defined agents, skills, tools, and MCP server descriptors.
- Optional Tavily MCP integration for web search and extraction.
- Project-local Python script execution and package installation tools.
- Exposure controls for selecting which skills and tools are visible.

## Latest Updates

### 2026-07

- Added automatic project-level final research report generation after
  `verify_overall(scope="final")` passes.
- Added non-overwriting Markdown reports under `projects/<project>/reports/`.
- Added continuation from the latest previous report so each new report begins
  with a clear summary of prior project progress.
- Added cross-session pending report windows for project-level reports, so
  unfinished reporting work from earlier sessions can be included in a later
  session's final report.
- Added report retry and enable/disable controls.

### 2026-06

- Added OpenAI Responses provider support.
- Added reasoning effort and reasoning summary configuration.
- Added dedicated archival provider configuration.
- Added archival fallback to the main provider.
- Added structured-output support for Responses-based verification and archival.
- Added project-local Python script execution.
- Added Python package installation for project scripts.
- Improved compatibility for assistant `reasoning_content` in session history.
- Improved session continuation with reasoning and visible assistant content.

### 2026-05

- Added typed project research logs.
- Added `research_log.jsonl`, readable `research_log.md`, by-type Markdown files,
  and `research_log_index.sqlite`.
- Added unified session-record retrieval.
- Added indexed tool-event retrieval.
- Added recovery references for large archived tool payloads.
- Added query-time backfill for older session files.
- Added `query_session_records` for exact raw session recovery.
- Added exposure controls for skills and tools.
- Added Usage Hint loading from skill and tool Markdown files.
- Added Tavily MCP setup commands.
- Improved research-mode provider failure handling.

## Contents

- [Latest Updates](#latest-updates)
- [Requirements](#requirements)
- [From Install to First Run](#from-install-to-first-run)
- [Runtime Home](#runtime-home)
- [Provider Setup](#provider-setup)
- [Daily Use After Setup](#daily-use-after-setup)
- [Chat Mode](#chat-mode)
- [Research Mode](#research-mode)
- [Sessions](#sessions)
- [Memory and Retrieval](#memory-and-retrieval)
- [Verification](#verification)
- [Files and Paths](#files-and-paths)
- [Skills, Tools, Agents, and MCP](#skills-tools-agents-and-mcp)
- [Python Tools](#python-tools)
- [Runtime Layout](#runtime-layout)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

## Requirements

- Python 3.8+
- A terminal environment
- An LLM provider for live model calls

Offline mode is available for inspecting the CLI and runtime layout without an
API key.

## From Install to First Run

This is the recommended path from a fresh checkout to a working Moonshine run.
For detailed options, see [Runtime Home](#runtime-home),
[Provider Setup](#provider-setup), [Research Mode](#research-mode), and
[Memory and Retrieval](#memory-and-retrieval) after this first-run path.

### 1. Install Moonshine

From the repository root:

```bash
python -m pip install -e ".[all]" --no-build-isolation
```

Optional but recommended before installation:

```bash
python -m pip install -U pip setuptools wheel
```

### 2. Initialize a runtime home

Use the default runtime home:

```bash
python -m moonshine init
```

Or choose one explicitly:

```bash
python -m moonshine --home /home/ubuntu/.moonshine init
```

Use the same `--home` value for later commands that should share the same
projects, sessions, credentials, memory, skills, tools, and agents.

### 3. Configure the main provider

You must configure a live provider before expecting chat or research mode to
make real LLM calls.

Moonshine has three provider slots:

- `main`: normal chat and research-agent calls
- `verification`: verification and problem-quality calls
- `archival`: research-log archival calls

By default, `verification` and `archival` inherit `main`, so a first run only
needs the `main` provider. Dedicated secondary providers are optional; see
[Verification Provider](#verification-provider) and
[Archival Provider](#archival-provider).

Choose one main provider family:

OpenAI-compatible chat completions:

```bash
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --openai-compatible
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --base-url "https://api.openai.com/v1"
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --model "your-model"
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --api-key-env "OPENAI_API_KEY"
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --set-api-key
```

OpenAI Responses:

```bash
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --openai-responses
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --base-url "https://api.openai.com/v1"
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --model "your-model"
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --api-key-env "OPENAI_API_KEY"
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --set-api-key
```

Azure OpenAI:

```bash
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --azure-openai
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --endpoint "https://your-resource.openai.azure.com/"
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --deployment "your-deployment"
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --api-version "2024-12-01-preview"
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --api-key-env "AZURE_OPENAI_API_KEY"
python -m moonshine --home /home/ubuntu/.moonshine provider --target main --set-api-key
```

### 4. Check the provider

```bash
python -m moonshine --home /home/ubuntu/.moonshine provider --show
```

Confirm that:

- `main` is not `offline`
- the model name is correct
- the base URL or Azure endpoint is correct
- the API key environment name is correct

### 5. Start research mode

Research mode is Moonshine's main mode. It creates a persistent project
workspace for conjecture generation, bridge building, obstacle identification,
verification, and long-running mathematical exploration. For details, see
[Research Mode](#research-mode).

```bash
python -m moonshine --home /home/ubuntu/.moonshine shell --mode research --project my_research_project
```

Or start from an input file:

```bash
python -m moonshine --home /home/ubuntu/.moonshine shell --mode research \
  --project my_research_project \
  --input-file /path/to/problem-or-notes.md
```

### 6. Use chat mode when needed

Chat mode is for ordinary assistant turns, lightweight explanations, and
general work that does not need the full research workflow. For details, see
[Chat Mode](#chat-mode).

```bash
python -m moonshine --home /home/ubuntu/.moonshine shell --mode chat --project general
```

One-shot chat call:

```bash
python -m moonshine --home /home/ubuntu/.moonshine ask --mode chat --project general "Explain Nakayama's lemma."
```

### 7. Optional checks

Check optional dependencies:

```bash
python -m moonshine --home /home/ubuntu/.moonshine init --check-deps
```

Install optional dependencies from the CLI:

```bash
python -m moonshine --home /home/ubuntu/.moonshine init --install-deps
```

## Runtime Home

Moonshine stores runtime data under a runtime home.

Default:

```text
~/.moonshine
```

Use `--home` when you want a specific runtime directory:

```bash
python -m moonshine --home /home/ubuntu/.moonshine init
python -m moonshine --home /home/ubuntu/.moonshine provider --show
```

On Windows:

```powershell
python -m moonshine --home D:/moonshine-home init
python -m moonshine --home D:/moonshine-home provider --show
```

Use the same `--home` for commands that should share projects, sessions,
credentials, memory, skills, tools, and agents.

Important files under the runtime home:

```text
config/settings.json       # provider, memory, context, and exposure config
config/credentials.json    # locally stored API keys
projects/                  # project workspaces and research memory
sessions/                  # raw session traces
databases/sessions.sqlite3 # unified session index
knowledge/                 # reusable verified conclusions
memory/                    # dynamic memory
agents/                    # agent definitions
skills/                    # skill definitions
tools/                     # tool and MCP definitions
```

## Provider Setup

The examples in this reference section omit `--home`. Add the same `--home`
value you used during initialization when you are not using the default
`~/.moonshine` runtime home.

For first-time setup, configure `main` first. `verification` and `archival`
inherit `main` unless you make them dedicated.

Show current provider settings:

```bash
python -m moonshine provider --show
```

Moonshine has three provider slots:

- `main`: normal assistant and research-agent calls
- `verification`: verification and problem-quality calls
- `archival`: research-log archival calls

By default, `verification` and `archival` inherit `main`.

### OpenAI-Compatible Chat Completions

Use this for OpenAI-compatible `/chat/completions` APIs:

```bash
python -m moonshine provider --target main --openai-compatible
python -m moonshine provider --target main --base-url "https://api.openai.com/v1"
python -m moonshine provider --target main --model "your-model"
python -m moonshine provider --target main --api-key-env "OPENAI_API_KEY"
python -m moonshine provider --target main --set-api-key
```

### OpenAI Responses

Use this for Responses-compatible APIs:

```bash
python -m moonshine provider --target main --openai-responses
python -m moonshine provider --target main --base-url "https://api.openai.com/v1"
python -m moonshine provider --target main --model "your-model"
python -m moonshine provider --target main --api-key-env "OPENAI_API_KEY"
python -m moonshine provider --target main --set-api-key
```

Set reasoning options for compatible Responses models:

```bash
python -m moonshine provider --target main --reasoning-effort high
python -m moonshine provider --target main --reasoning-summary detailed
```

Supported `--reasoning-effort` values:

```text
minimal, low, medium, high, xhigh
```

Supported `--reasoning-summary` values:

```text
auto, concise, detailed
```

Clear either setting by passing an empty value:

```bash
python -m moonshine provider --target main --reasoning-effort ""
python -m moonshine provider --target main --reasoning-summary ""
```

### Azure OpenAI

```bash
python -m moonshine provider --target main --azure-openai
python -m moonshine provider --target main --endpoint "https://your-resource.openai.azure.com/"
python -m moonshine provider --target main --deployment "your-deployment"
python -m moonshine provider --target main --api-version "2024-12-01-preview"
python -m moonshine provider --target main --api-key-env "AZURE_OPENAI_API_KEY"
python -m moonshine provider --target main --set-api-key
```

### Verification Provider

Use the main provider for verification:

```bash
python -m moonshine provider --target verification --inherit-main
```

Use a dedicated verifier:

```bash
python -m moonshine provider --target verification --dedicated
python -m moonshine provider --target verification --openai-compatible
python -m moonshine provider --target verification --base-url "https://api.openai.com/v1"
python -m moonshine provider --target verification --model "your-verifier-model"
python -m moonshine provider --target verification --api-key-env "VERIFY_API_KEY"
python -m moonshine provider --target verification --set-api-key
```

Verification calls use structured output. If verification inherits `main`, it
uses the same provider type and settings as `main`, including Responses and
reasoning settings when configured.

### Archival Provider

Use the main provider for research archival:

```bash
python -m moonshine provider --target archival --inherit-main
```

Use a dedicated archival provider:

```bash
python -m moonshine provider --target archival --dedicated
python -m moonshine provider --target archival --openai-responses
python -m moonshine provider --target archival --base-url "https://api.openai.com/v1"
python -m moonshine provider --target archival --model "your-archive-model"
python -m moonshine provider --target archival --api-key-env "ARCHIVE_API_KEY"
python -m moonshine provider --target archival --set-api-key
```

If a dedicated archival provider fails, Moonshine retries the archival call with
the main provider. If both fail, research autopilot stops and reports the error.

### Useful Provider Commands

```bash
python -m moonshine provider --show
python -m moonshine provider --target main --stream
python -m moonshine provider --target main --no-stream
python -m moonshine provider --target main --temperature 0.2
python -m moonshine provider --target main --clear-temperature
python -m moonshine provider --target main --structured-output-format json_schema
python -m moonshine provider --target main --max-context-tokens 0
```

## Daily Use After Setup

After installation and provider setup, the common commands are:

Start research mode:

Research mode is for persistent project work: conjectures, proof attempts,
counterexamples, verification, and reusable research memory. See
[Research Mode](#research-mode) for the full workflow.

```bash
python -m moonshine shell --mode research --project my_research_project
```

Run one autonomous research prompt:

```bash
python -m moonshine ask --mode research --project my_research_project \
  "Study the current problem and continue the research."
```

Start research mode from an input file:

```bash
python -m moonshine shell --mode research \
  --project my_research_project \
  --input-file /path/to/problem-or-notes.md
```

Use chat mode for ordinary assistant work:

Chat mode is for general conversation, explanations, and tasks that do not need
project research automation. See [Chat Mode](#chat-mode) for details.

```bash
python -m moonshine shell --mode chat --project general
```

Ask one chat question and exit:

```bash
python -m moonshine ask --mode chat --project general "Explain Nakayama's lemma."
```

For session continuation, see [Sessions](#sessions). For research memory and
retrieval, see [Memory and Retrieval](#memory-and-retrieval).

## Chat Mode

Chat mode is for ordinary assistant work with persistent session history.

```bash
python -m moonshine shell --mode chat --project general
```

Useful shell commands:

```text
/help
/mode chat
/project general
/sessions
/knowledge search <query>
/skills
/tools
/mcp
/exit
```

## Research Mode

Research mode is for project-based mathematical exploration, conjecture
generation, and theory building.

It supports:

- reading project notes and input files
- refining candidate problems
- checking problem quality
- extracting structural patterns from known problems
- formulating conjectures and theoretical directions
- identifying bridges to related methods or domains
- trying proofs, reductions, examples, and counterexamples
- recording obstacles and failed paths
- running verification tools on important claims
- preserving research progress in project memory
- retrieving prior project work
- resuming a project later

Start a research shell:

```bash
python -m moonshine shell --mode research --project my_research_project
```

Run with a maximum autonomous iteration count:

```bash
python -m moonshine shell --mode research \
  --project my_research_project \
  --max-iterations 50
```

Run one turn only:

```bash
python -m moonshine shell --mode research \
  --project my_research_project \
  --interactive
```

One-shot research command with a limit:

```bash
python -m moonshine ask --mode research \
  --project my_research_project \
  --max-iterations 20 \
  "Continue from the current project memory and advance the research."
```

Research autopilot stops when it reaches its iteration limit, completes a final
verified result, detects provider failure, or hits configured safety limits.

### Final research reports

When a research run reaches a project-level result and `verify_overall` passes
with `scope="final"`, Moonshine can generate a polished Markdown research report
for direct review.

Reports are written under:

```text
projects/<project-slug>/reports/
```

Each report is saved as a new file; existing reports are not overwritten. When a
previous report exists, the next report uses the latest report as the starting
summary of prior progress, then focuses on the new verified progress from the
current reporting window.

Report generation is project-level. If multiple sessions have contributed to the
same project and some progress has not yet been included in a report, the next
successful final report includes the pending project research log plus the
relevant pending session messages and tool events. After the report is written,
the pending report offsets for that project are cleared so the same window is not
reported again.

### Research progress types

Research mode preserves project progress as small research-report records. Each
record has exactly one type:

- `problem`: the research problem itself, including revisions, hypothesis
  changes, object definitions, or target changes.
- `verified_conclusion`: reusable verified mathematics such as lemmas,
  propositions, theorems, constructions, or classifications. This type is about
  the conclusion itself.
- `verification`: a review/checking record. This type is about what was checked,
  whether it passed, what defects were found, and what should be repaired.
- `project_result`: the project-level final result only. Use this for the final
  answer, theorem, construction, classification, or negative result after final
  verification has passed. Ordinary verified lemmas or intermediate theorems go
  under `verified_conclusion`.
- `counterexample`: a counterexample or negative construction refuting a claim.
  Even verified counterexamples stay in this type when the record centers on the
  refutation.
- `failed_path`: a failed route, method, proof strategy, or technical approach.
  It can coexist with a counterexample, but focuses on why the route failed.
- `research_note`: other stage-level research progress, such as unverified
  derivations, calculations, observations, reductions, plans, or next steps.

## Sessions

Resume an existing session:

```bash
python -m moonshine shell --mode research \
  --project my_research_project \
  --session session-xxxxxxxxxx
```

Resume a session with one command:

```bash
python -m moonshine ask --mode research \
  --project my_research_project \
  --session session-xxxxxxxxxx \
  --max-iterations 20 \
  "Continue the previous session."
```

List sessions:

```bash
python -m moonshine sessions
```

Search sessions:

```bash
python -m moonshine sessions --search "keyword"
```

Session records include messages, tool events, provider rounds, transcripts,
context summaries, and compressed provider-round archives.

## Memory and Retrieval

Moonshine has four main retrieval surfaces:

- project research memory
- raw session history
- dynamic memory
- global knowledge

### Project Research Memory

Research mode writes project progress into:

```text
projects/<project>/memory/research_log.jsonl
projects/<project>/memory/research_log.md
projects/<project>/memory/by_type/
projects/<project>/memory/research_log_index.sqlite
```

Research-log record types:

```text
problem
verified_conclusion
verification
project_result
counterexample
failed_path
research_note
```

See [Research progress types](#research-progress-types) for the meaning and
boundary of each type.

Search project memory:

```text
query_memory(query="phase mismatch lemma")
```

Search a specific type:

```text
query_memory(query="phase mismatch lemma", types=["verified_conclusion"])
```

Search across projects:

```text
query_memory(query="polynomial reduction", all_projects=true)
```

### Raw Session History

Use session-record search when exact wording, tool interactions, or source
locations matter:

```text
query_session_records(query="failed path", session_id="session-xxxxxxxxxx")
```

Relevant raw files:

```text
sessions/<session-id>/messages.jsonl
sessions/<session-id>/tool_events.jsonl
sessions/<session-id>/provider_rounds.jsonl
sessions/<session-id>/transcript.md
sessions/<session-id>/tool_events/*.json.gz
sessions/<session-id>/turns/*.json.gz
sessions/<session-id>/artifacts/context_summaries.jsonl
```

The unified session index is stored at:

```text
databases/sessions.sqlite3
```

### Global Knowledge

Search reusable conclusions:

```text
search_knowledge(query="commensurate slope polynomial reduction")
```

Global knowledge files:

```text
knowledge/KNOWLEDGE.md
knowledge/conclusions.sqlite3
knowledge/entries/
knowledge/vectors/
```

Verified research conclusions can be stored in global knowledge for reuse
across projects.

## Verification

Available verification tools:

```text
assess_problem_quality
verify_correctness_assumption
verify_correctness_computation
verify_correctness_logic
verify_overall
```

Use `assess_problem_quality` before treating a candidate problem as ready for
serious solving.

Use intermediate verification for lemmas, reductions, computations, and partial
claims.

Use final verification for project-level results:

```text
verify_overall(scope="final")
```

`verify_overall` checks assumption use, computation, and logic. The review count
per dimension is configurable in:

```text
config/settings.json
```

```json
{
  "agent": {
    "verification_dimension_review_count": 1
  }
}
```

Verification input is bounded so oversized claims or proofs are trimmed before
review.

## Files and Paths

Each project lives under:

```text
projects/<project-slug>/
```

Common project files:

```text
workspace/problem.md
workspace/blueprint.md
workspace/blueprint_verified.md
references/notes/
references/papers/
references/surveys/
memory/research_log.jsonl
memory/research_log.md
memory/by_type/
```

Read runtime files:

```text
read_runtime_file(relative_path="workspace/problem.md")
read_runtime_file(relative_path="memory/research_log.md")
read_runtime_file(relative_path="knowledge/KNOWLEDGE.md")
read_runtime_file(relative_path="sessions/<session-id>/messages.jsonl")
```

When a project is active, project-relative paths are preferred:

```text
workspace/problem.md
memory/research_log.md
references/notes/source.md
```

The project filesystem MCP is scoped to the active project by default:

```text
mcp_filesystem_read_file(path="workspace/problem.md")
mcp_filesystem_write_file(path="workspace/notes.md", content="...")
```

## Skills, Tools, Agents, and MCP

### Skills

List skills:

```bash
python -m moonshine skills
```

Shell commands:

```text
/skills
/skills show <skill-slug>
```

Runtime skill folders:

```text
skills/builtin/
skills/installed/
```

Each skill is defined by a `SKILL.md` file. Moonshine loads the skill metadata,
description, allowed tools, file references, and `Usage Hint` section for model
selection.

### Tools

List tools:

```bash
python -m moonshine tools
```

Shell commands:

```text
/tools
/tools show <tool-name>
```

Tool definitions live under:

```text
tools/definitions/
```

Each tool definition can include a `Usage Hint` section. The active model sees
the exposed tool schemas and concise usage guidance.

### Agents

List agents:

```bash
python -m moonshine agent
```

Inspect an agent:

```bash
python -m moonshine agent --show research-control-loop
```

Run with an explicit agent:

```bash
python -m moonshine shell --mode research \
  --project my_research_project \
  --agent research-control-loop
```

Runtime agent folders:

```text
agents/builtin/
agents/installed/
```

### MCP

MCP descriptors connect external tools to Moonshine. The built-in filesystem
descriptor gives project-scoped file access. Tavily adds live web search and
page extraction, useful for recent references, literature checks, and reading
web pages during research.

List MCP descriptors:

```bash
python -m moonshine mcp
```

Inspect one descriptor:

```bash
python -m moonshine mcp --show filesystem
python -m moonshine mcp --show tavily
```

Enable Tavily web search:

```bash
python -m moonshine mcp --set-tavily-key
python -m moonshine mcp --enable-tavily
```

After enabling Tavily, restart Moonshine so the Tavily MCP tools can be
registered for new sessions.

Shell commands:

```text
/mcp
/mcp show filesystem
/mcp tavily set-key <api-key>
/mcp tavily enable
/mcp tavily disable
```

## Python Tools

Moonshine includes project-local Python tools for reproducible checks.

Run a Python script inside the active project:

```text
run_python_script(path="workspace/check.py", args=["--case", "small"])
```

Install a missing package into the current Moonshine Python environment:

```text
install_python_package(packages=["sympy"])
```

The Python runner is bounded by timeout and project path checks. Package
installation accepts package requirement strings and uses the same Python
interpreter that runs Moonshine.

## Exposure Config

Control which tools and skills are exposed:

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

Rules:

- Empty `*_include` means all discovered items of that kind are eligible.
- Non-empty `*_include` means only those names are eligible.
- `*_exclude` removes names from the eligible set.
- Tool names are callable names such as `query_memory` or `verify_overall`.
- Skill names are skill slugs such as `quality-assessor`.

Example:

```json
{
  "exposure": {
    "tools_include": [
      "query_memory",
      "query_session_records",
      "read_runtime_file",
      "assess_problem_quality",
      "verify_overall"
    ],
    "tools_exclude": [],
    "skills_include": [],
    "skills_exclude": []
  }
}
```

Start a new command or shell after editing exposure settings.

## Runtime Layout

Typical runtime home:

```text
MOONSHINE_HOME/
  AGENTS.md
  CLAUDE.md
  config/
    settings.json
    credentials.json
  memory/
    MEMORY.md
    audit/events.jsonl
    feedback/
    projects/
    references/
    user/
  knowledge/
    KNOWLEDGE.md
    conclusions.sqlite3
    entries/
    vectors/
  databases/
    sessions.sqlite3
  sessions/
    <session-id>/
      session.json
      messages.jsonl
      transcript.md
      tool_events.jsonl
      provider_rounds.jsonl
      provider_trace.md
      turn_events.jsonl
      tool_events/
      turns/
      artifacts/context_summaries.jsonl
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

Run the main test suite from the repository root:

```bash
python -m unittest discover -s moonshine/tests -t . -p "test_architecture.py"
```

Run all tests:

```bash
python -m unittest discover -s moonshine/tests -t .
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

Search sessions:

```bash
python -m moonshine sessions --search "keyword"
```

List tools:

```bash
python -m moonshine tools
```

List skills:

```bash
python -m moonshine skills
```

List MCP descriptors:

```bash
python -m moonshine mcp
```

Check dependencies:

```bash
python -m moonshine init --check-deps
```

If a provider call fails, run:

```bash
python -m moonshine provider --show
```

Then check:

- provider type
- model name
- base URL or Azure endpoint
- API key environment name
- whether the key was stored with `--set-api-key`
- whether the model supports the configured reasoning or structured-output mode

## Status

Moonshine is experimental software for local, inspectable, project-based agent
workflows in autonomous mathematical research, conjecture generation, and
long-running theory exploration.
