"""Refactored Moonshine architecture tests."""

from __future__ import annotations

import io
import gzip
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

import moonshine.utils as moonshine_utils
from moonshine.agent_runtime.extraction import ExtractedItems
from moonshine.app import MoonshineApp
from moonshine.agent_runtime.model_metadata import DEFAULT_CONTEXT_WINDOW_TOKENS, resolve_model_context_window
from moonshine.moonshine_cli.dependencies import DependencyInstallResult, REQUIRED_RUNTIME_DEPENDENCIES
from moonshine.moonshine_cli.main import main as cli_main
from moonshine.moonshine_cli.config import AppConfig
from moonshine.moonshine_constants import (
    DEFAULT_AGENT_RULES_MD,
    default_config,
    packaged_builtin_skills_dir,
    packaged_tool_definitions_dir,
)
from moonshine.providers import BaseProvider, AzureOpenAIChatCompletionsProvider, OpenAIChatCompletionsProvider, ProviderResponse, ProviderStreamEvent, ProviderToolCall
from moonshine.run_agent import AgentEvent, main as run_agent_main, render_agent_events
from moonshine.skills.skill_document import parse_skill_document, validate_skill_document
from moonshine.storage.knowledge_vector_store import SQLiteVectorBackend
from moonshine.structured_tasks import get_structured_task, list_structured_tasks
from moonshine.utils import atomic_write, read_json, read_jsonl, read_text


class ScriptedProvider(object):
    """Small scripted provider used to test agent-loop recovery."""

    def __init__(self, scripted_responses):
        self.scripted_responses = list(scripted_responses)
        self.calls = []

    def stream_generate(self, *, system_prompt, messages, tool_schemas=None):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": [dict(item) for item in messages],
                "tool_schemas": list(tool_schemas or []),
            }
        )
        if not self.scripted_responses:
            raise AssertionError("ScriptedProvider ran out of scripted responses")
        step = self.scripted_responses.pop(0)
        for chunk in step.get("chunks", []):
            yield ProviderStreamEvent(type="text_delta", text=chunk)
        yield ProviderStreamEvent(type="response", response=step.get("response", ProviderResponse()))


class ResearchWorkflowProvider(ScriptedProvider):
    """Script provider plus strict structured responses for secondary calls."""

    def __init__(self, scripted_responses, structured_responses, archive_responses=None):
        super().__init__(scripted_responses)
        self.structured_responses = list(structured_responses)
        self.archive_responses = list(archive_responses or [])
        self.structured_calls = []

    def generate_structured(self, *, system_prompt, messages, response_schema, schema_name):
        self.structured_calls.append(
            {
                "system_prompt": system_prompt,
                "messages": [dict(item) for item in messages],
                "response_schema": dict(response_schema),
                "schema_name": schema_name,
            }
        )
        if schema_name == "research_turn_archive":
            if self.archive_responses:
                return json.loads(json.dumps(self.archive_responses.pop(0)))
            return {"records": []}
        if not self.structured_responses:
            raise AssertionError("ResearchWorkflowProvider ran out of structured responses")
        return json.loads(self.structured_responses.pop(0))


class ArchiveOnlyProvider(object):
    """Provider used to test research-log archival without changing the main loop shape."""

    def __init__(self, archive_payload):
        self.archive_payload = dict(archive_payload)
        self.calls = []

    def generate_structured(self, *, system_prompt, messages, response_schema, schema_name):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": [dict(item) for item in messages],
                "response_schema": dict(response_schema),
                "schema_name": schema_name,
            }
        )
        return json.loads(json.dumps(self.archive_payload))


class OverflowThenRecoverProvider(object):
    """Raise a context overflow once, then return a normal response."""

    def __init__(self):
        self.calls = []
        self.first_call = True

    def stream_generate(self, *, system_prompt, messages, tool_schemas=None):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": [dict(item) for item in messages],
                "tool_schemas": list(tool_schemas or []),
            }
        )
        if self.first_call:
            self.first_call = False
            raise RuntimeError("context length exceeded for this provider request")
        yield ProviderStreamEvent(type="text_delta", text="Recovered after overflow.")
        yield ProviderStreamEvent(type="response", response=ProviderResponse(content="Recovered after overflow."))


class SkillExtractionProvider(object):
    """Return scripted JSON payloads for internal memory-extraction skills."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate(self, *, system_prompt, messages, tool_schemas=None):
        self.calls.append(
            {
                "method": "generate",
                "system_prompt": system_prompt,
                "messages": [dict(item) for item in messages],
                "tool_schemas": list(tool_schemas or []),
            }
        )
        if not self.responses:
            raise AssertionError("SkillExtractionProvider ran out of scripted responses")
        return ProviderResponse(content=self.responses.pop(0))

    def generate_structured(self, *, system_prompt, messages, response_schema, schema_name):
        self.calls.append(
            {
                "method": "generate_structured",
                "system_prompt": system_prompt,
                "messages": [dict(item) for item in messages],
                "response_schema": dict(response_schema),
                "schema_name": schema_name,
            }
        )
        if not self.responses:
            raise AssertionError("SkillExtractionProvider ran out of scripted responses")
        return json.loads(self.responses.pop(0))


class CountingSummaryProvider(object):
    """Provider that records normal summary calls and returns a scripted answer for streams."""

    model = "counting-summary-test"
    max_context_tokens = 1000000

    def __init__(self):
        self.calls = []
        self.stream_calls = []

    def generate(self, *, system_prompt, messages, tool_schemas=None):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": [dict(item) for item in messages],
                "tool_schemas": list(tool_schemas or []),
            }
        )
        return ProviderResponse(content="Preserved research summary with definitions, claims, failed paths, and next checks.")

    def stream_generate(self, *, system_prompt, messages, tool_schemas=None):
        self.stream_calls.append(
            {
                "system_prompt": system_prompt,
                "messages": [dict(item) for item in messages],
                "tool_schemas": list(tool_schemas or []),
            }
        )
        yield ProviderStreamEvent(type="text_delta", text="Final answer.")
        yield ProviderStreamEvent(type="response", response=ProviderResponse(content="Final answer."))


class SequencedSummaryProvider(object):
    """Provider that returns distinct summaries for each compression call."""

    model = "sequenced-summary-test"
    max_context_tokens = 1000000

    def __init__(self):
        self.calls = []
        self.counter = 0

    def generate(self, *, system_prompt, messages, tool_schemas=None):
        self.counter += 1
        self.calls.append(
            {
                "index": self.counter,
                "system_prompt": system_prompt,
                "messages": [dict(item) for item in messages],
                "tool_schemas": list(tool_schemas or []),
            }
        )
        return ProviderResponse(content="Summary batch %s." % self.counter)


class PessimisticVerificationProvider(object):
    """Return scripted structured verifier reviews."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def generate_structured(self, *, system_prompt, messages, response_schema, schema_name):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "messages": [dict(item) for item in messages],
                "response_schema": dict(response_schema),
                "schema_name": schema_name,
            }
        )
        if not self.responses:
            raise AssertionError("PessimisticVerificationProvider ran out of scripted responses")
        return json.loads(self.responses.pop(0))


class SemanticEmbeddingProvider(object):
    """Tiny deterministic embedding provider for hybrid knowledge-search tests."""

    name = "semantic-test"

    def embed_texts(self, texts):
        vectors = []
        for text in texts:
            lowered = text.lower()
            if "localization" in lowered or "local criterion" in lowered or "maximal ideal" in lowered:
                vectors.append([1.0, 0.0, 0.0])
            elif "tensor" in lowered or "homological" in lowered:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 1.0])
        return vectors


class MoonshineArchitectureTestCase(unittest.TestCase):
    """Exercise the new Moonshine architecture."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app = MoonshineApp(home=self.temp_dir.name)
        self.state = self.app.start_shell_state(mode="research", project_slug="anderson_conjecture")

    def _build_agent_state(self, **kwargs):
        """Build a turn state through the event preflight generator."""
        events = self.app.agent._build_state_events(**kwargs)
        while True:
            try:
                next(events)
            except StopIteration as stop:
                return stop.value

    def test_app_config_defaults_come_from_single_default_config_source(self):
        self.assertEqual(AppConfig().to_dict(), default_config())

    def test_default_agent_rules_template_describes_executable_closure(self):
        self.assertIn("You are Moonshine", DEFAULT_AGENT_RULES_MD)
        self.assertIn("Let actual tool calls carry memory, knowledge, file, and research-state updates.", DEFAULT_AGENT_RULES_MD)
        self.assertIn("For live or recent information, prefer live-search tools", DEFAULT_AGENT_RULES_MD)

    def _verifier_review(self, reviewer_id="reviewer", verdict="correct", **overrides):
        payload = {
            "reviewer_id": reviewer_id,
            "review_focus": "Audit the proof pessimistically.",
            "verdict": verdict,
            "logical_chain_complete": verdict == "correct",
            "theorem_use_valid": verdict == "correct",
            "assumptions_explicit": verdict == "correct",
            "calculations_valid": verdict == "correct",
            "premise_conclusion_match": verdict == "correct",
            "critical_errors": [],
            "gaps": [],
            "hidden_assumptions": [],
            "citation_issues": [],
            "calculation_issues": [],
            "repair_hints": [],
            "rationale": "Scripted verifier response.",
            "confidence": 0.9 if verdict == "correct" else 0.2,
        }
        payload.update(overrides)
        return payload

    def _dimension_review(self, reviewer_id="reviewer", dimension="assumption", verdict="correct", errors=None, **overrides):
        payload = {
            "reviewer_id": reviewer_id,
            "dimension": dimension,
            "review_focus": "Check only the assigned verification dimension.",
            "verdict": verdict,
            "error_count": 0 if verdict == "correct" and not errors else len(list(errors or [])) or (0 if verdict == "correct" else 1),
            "errors": list(errors or []),
            "rationale": "Scripted multidimensional verifier response.",
            "confidence": 0.9 if verdict == "correct" else 0.2,
        }
        payload.update(overrides)
        return payload

    def test_explicit_memory_updates_index(self):
        self.app.execute_command("/memory write Prioritize Krull dimension when studying Noetherian rings.", self.state)

        explicit_text = self.app.memory.dynamic_store.read_file("feedback-explicit")
        index_text = self.app.paths.memory_index_file.read_text(encoding="utf-8")

        self.assertIn("Krull dimension", explicit_text)
        self.assertIn("Explicit Memory Request", index_text)

    def test_session_search_and_trace_files_work(self):
        self.app.ask("The Anderson conjecture project should focus on local criteria.", self.state)
        self.app.ask("I prefer algebraic methods over geometric ones.", self.state)

        results = self.app.session_store.search_messages("algebraic methods", project_slug="anderson_conjecture", limit=3)
        self.assertTrue(results)
        self.assertIn("algebraic methods", results[0]["content"])
        self.assertTrue(self.app.paths.session_messages_file(self.state.session_id).exists())
        self.assertTrue(self.app.paths.session_transcript_file(self.state.session_id).exists())

    def test_session_search_includes_assistant_reasoning_content(self):
        self.app.session_store.append_message(
            self.state.session_id,
            "assistant",
            "Visible answer without the special token.",
            metadata={"reasoning_content": "REASONING_SEARCH_SENTINEL appears only in reasoning metadata."},
        )

        results = self.app.session_store.search_messages(
            "REASONING_SEARCH_SENTINEL",
            project_slug="anderson_conjecture",
            limit=3,
        )

        self.assertTrue(results)
        self.assertEqual(results[0]["metadata"]["reasoning_content"], "REASONING_SEARCH_SENTINEL appears only in reasoning metadata.")

    def test_query_session_records_searches_plain_and_archived_raw_records(self):
        self.app.ask("Record RAW_RECORD_SENTINEL in the raw session transcript.", self.state)
        archive_dir = self.app.paths.session_provider_round_archives_dir(self.state.session_id)
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / "manual-round.json.gz"
        with gzip.open(archive_path, "wt", encoding="utf-8") as handle:
            json.dump({"messages": [{"content": "ARCHIVE_SENTINEL appears only in the archived provider round."}]}, handle)

        runtime = self.app.agent._build_runtime(
            mode="chat",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )
        plain_payload = self.app.tool_manager.dispatch(
            "query_session_records",
            {"query": "RAW_RECORD_SENTINEL", "limit": 5},
            runtime,
        )
        archive_payload = self.app.tool_manager.dispatch(
            "query_session_records",
            {"query": "ARCHIVE_SENTINEL", "limit": 5},
            runtime,
        )

        self.assertTrue(plain_payload["hits"])
        self.assertIn("messages", plain_payload["raw_record_locations"])
        self.assertIn("provider_round_archives", plain_payload["raw_record_locations"])
        self.assertTrue(any("RAW_RECORD_SENTINEL" in item["excerpt"] for item in plain_payload["hits"]))
        self.assertTrue(any(item["source"] == "provider_round_archive" for item in archive_payload["hits"]))
        self.assertTrue(any("ARCHIVE_SENTINEL" in item["excerpt"] for item in archive_payload["hits"]))

    def test_query_session_records_searches_reasoning_content_in_raw_messages(self):
        self.app.session_store.append_message(
            self.state.session_id,
            "assistant",
            "Visible answer without raw reasoning token.",
            metadata={"reasoning_content": "RAW_REASONING_RECORD_SENTINEL appears only in metadata."},
        )
        runtime = self.app.agent._build_runtime(
            mode="chat",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )

        payload = self.app.tool_manager.dispatch(
            "query_session_records",
            {"query": "RAW_REASONING_RECORD_SENTINEL", "limit": 5},
            runtime,
        )

        self.assertTrue(payload["hits"])
        self.assertTrue(any("RAW_REASONING_RECORD_SENTINEL" in item["excerpt"] for item in payload["hits"]))

    def test_knowledge_layer_accepts_manual_entries(self):
        response = self.app.execute_command(
            "/knowledge add Nakayama lemma | If M = IM and I lies in the Jacobson radical, then M = 0 | Standard proof.",
            self.state,
        )
        results = self.app.memory.knowledge_store.search("Nakayama", project_slug="anderson_conjecture", limit=3)
        self.assertTrue(results)
        self.assertEqual(results[0]["title"], "Nakayama lemma")
        self.assertIn("Stored knowledge item", response)
        self.assertTrue(self.app.paths.knowledge_index_file.exists())
        self.assertTrue(self.app.memory.knowledge_store.entry_path(results[0]["id"]).exists())

    def test_knowledge_layer_uses_vector_index_for_semantic_search(self):
        self.app.memory.knowledge_store.vector_index.backend = SQLiteVectorBackend(self.app.paths)
        self.app.memory.knowledge_store.vector_index.embedding_provider = SemanticEmbeddingProvider()

        self.app.memory.knowledge_store.add_conclusion(
            title="Local Criterion",
            statement="Reduce the global claim to maximal ideals.",
            proof_sketch="Check the statement after passing to local rings.",
            status="partial",
            project_slug="anderson_conjecture",
            tags=["commutative-algebra"],
            source_type="test",
            source_ref=self.state.session_id,
        )
        self.app.memory.knowledge_store.add_conclusion(
            title="Tensor Warning",
            statement="Tensor products may not preserve the needed finiteness condition.",
            proof_sketch="Use a separate homological check.",
            status="partial",
            project_slug="anderson_conjecture",
            tags=["homological"],
            source_type="test",
            source_ref=self.state.session_id,
        )

        results = self.app.memory.knowledge_store.search(
            "localization argument",
            project_slug="anderson_conjecture",
            limit=2,
        )
        self.assertTrue(results)
        self.assertEqual(results[0]["title"], "Local Criterion")
        self.assertEqual(results[0]["retrieval"]["vector_backend"], "sqlite")
        self.assertGreater(results[0]["retrieval"]["vector_score"], 0.9)
        self.assertEqual(results[0]["retrieval"]["fts_score"], 0.0)
        self.assertTrue(self.app.paths.knowledge_vector_sqlite_db.exists())

        tagged = self.app.memory.knowledge_store.search(
            "localization argument",
            project_slug="anderson_conjecture",
            tags=["commutative-algebra"],
            limit=2,
        )
        blocked = self.app.memory.knowledge_store.search(
            "localization argument",
            project_slug="anderson_conjecture",
            tags=["homological"],
            limit=2,
        )
        self.assertEqual(tagged[0]["title"], "Local Criterion")
        self.assertFalse(blocked)

    def test_auto_extraction_creates_preference_and_project_context_entries(self):
        chat_state = self.app.start_shell_state(mode="chat", project_slug="anderson_conjecture")
        self.app.ask("I prefer algebraic methods. The current project studies local conditions for Noetherian rings.", chat_state)
        preference_text = self.app.memory.dynamic_store.read_file("user-preferences")
        project_text = self.app.memory.dynamic_store.read_file("project-context", project_slug="anderson_conjecture")

        self.assertIn("algebraic methods", preference_text)
        self.assertIn("Noetherian rings", project_text)
        self.assertFalse(self.app.paths.project_dir("anderson_conjecture").joinpath("memory", "progress.md").exists())
        self.assertFalse(self.app.paths.project_dir("anderson_conjecture").joinpath("memory", "decisions.md").exists())

    def test_run_agent_module_supports_one_shot_prompt(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            exit_code = run_agent_main(
                [
                    "--home",
                    self.temp_dir.name,
                    "--mode",
                    "research",
                    "--project",
                    "anderson_conjecture",
                    "--interactive",
                    "--prompt",
                    "Remember: prioritize Krull dimension.",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Krull dimension", stdout.getvalue())

    def test_run_agent_research_prompt_defaults_to_autopilot(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            exit_code = run_agent_main(
                [
                    "--home",
                    self.temp_dir.name,
                    "--mode",
                    "research",
                    "--project",
                    "anderson_conjecture",
                    "--max-iterations",
                    "1",
                    "--prompt",
                    "Study this research topic autonomously.",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Research autopilot iteration 1/1.", stdout.getvalue())

    def test_cli_research_ask_defaults_to_autopilot(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            exit_code = cli_main(
                [
                    "--home",
                    self.temp_dir.name,
                    "ask",
                    "--mode",
                    "research",
                    "--project",
                    "anderson_conjecture",
                    "--max-iterations",
                    "1",
                    "Study this research topic autonomously.",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Research autopilot iteration 1/1.", stdout.getvalue())

    def test_cli_ask_can_resume_existing_session_with_session_flag(self):
        resumed_app = MoonshineApp(home=self.temp_dir.name)
        state = resumed_app.start_shell_state(mode="research", project_slug="anderson_conjecture")
        resumed_app.session_store.append_message(state.session_id, "user", "CLI_RESUME_SESSION_SENTINEL")
        resumed_app.close_session(state)

        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout):
            exit_code = cli_main(
                [
                    "--home",
                    self.temp_dir.name,
                    "ask",
                    "--mode",
                    "research",
                    "--session",
                    state.session_id,
                    "--interactive",
                    "Continue from the resumed session.",
                ]
            )

        self.assertEqual(exit_code, 0)
        messages = resumed_app.session_store.get_all_messages(state.session_id)
        self.assertTrue(any("Continue from the resumed session." in item["content"] for item in messages))
        self.assertTrue(any("CLI_RESUME_SESSION_SENTINEL" in item["content"] for item in messages))

    def test_cli_without_subcommand_starts_shell(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch("builtins.input", side_effect=["/exit"]):
            exit_code = cli_main(["--home", self.temp_dir.name])

        self.assertEqual(exit_code, 0)
        self.assertIn("Type /help for commands.", stdout.getvalue())

    def test_cli_research_shell_defaults_to_autopilot(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch(
            "builtins.input",
            side_effect=["Study this research topic autonomously.", "/exit"],
        ):
            exit_code = cli_main(
                [
                    "--home",
                    self.temp_dir.name,
                    "shell",
                    "--mode",
                    "research",
                    "--project",
                    "anderson_conjecture",
                    "--max-iterations",
                    "1",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Research autopilot iteration 1/1.", stdout.getvalue())

    def test_run_agent_research_terminal_defaults_to_autopilot(self):
        stdout = io.StringIO()
        with mock.patch("sys.stdout", stdout), mock.patch(
            "builtins.input",
            side_effect=["Study this research topic autonomously.", "/exit"],
        ):
            exit_code = run_agent_main(
                [
                    "--home",
                    self.temp_dir.name,
                    "--mode",
                    "research",
                    "--project",
                    "anderson_conjecture",
                    "--max-iterations",
                    "1",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertIn("Research autopilot iteration 1/1.", stdout.getvalue())

    def test_research_mode_without_project_auto_creates_project(self):
        state = self.app.start_shell_state(mode="research", project_slug=None)

        self.assertTrue(state.auto_project_pending)
        self.app.ask(
            "Study local criteria for finiteness over Noetherian rings and design a serious research problem.",
            state,
        )

        self.assertFalse(state.auto_project_pending)
        self.assertNotEqual(state.project_slug, "general")
        self.assertTrue(self.app.paths.project_dir(state.project_slug).exists())
        self.assertTrue(self.app.paths.project_reference_index_file(state.project_slug).exists())
        self.assertEqual(
            self.app.session_store.get_session_meta(state.session_id)["project_slug"],
            state.project_slug,
        )

    def test_research_mode_prompt_uses_policy_and_agent_without_inline_workflow_snapshot(self):
        context = self.app.context_manager.build_startup_context(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )
        state = self._build_agent_state(
            user_message="Design a research problem.",
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )
        system_prompt = state.system_prompt

        self.assertIn("You are Moonshine", system_prompt)
        self.assertIn("You are working inside project `anderson_conjecture`", system_prompt)
        self.assertIn("current multi-turn conversation as the live research context", system_prompt)
        self.assertNotIn("projects/anderson_conjecture/memory/research_workflow.json", system_prompt)
        self.assertNotIn("projects/anderson_conjecture/memory/research_state.json", system_prompt)
        self.assertNotIn("Research runtime packet", system_prompt)
        self.assertIn("Available tools (short descriptions and usage guidance", system_prompt)
        self.assertIn("query_session_records", system_prompt)
        self.assertIn("Usage:", system_prompt)
        self.assertIn("Available skills (short descriptions and usage guidance", system_prompt)
        self.assertIn("Active agent instructions:", system_prompt)
        self.assertIn("autonomous mathematical researcher", system_prompt)
        self.assertIn("General Working Pattern", system_prompt)
        self.assertIn("Stage 1: Problem Design", system_prompt)
        self.assertIn("Stage 2: Problem Solving", system_prompt)
        self.assertNotIn("Runtime Prompt", system_prompt)
        self.assertNotIn("moonshine:prompt", system_prompt)
        self.assertNotIn("Advance the project from the following saved research state", system_prompt)
        self.assertNotIn("Stage Activity Palette", system_prompt)
        self.assertFalse(state.research_workflow_snapshot)
        self.assertFalse(context.project_rules)

    def test_research_autonomous_prompt_guides_multi_turn_continuation_without_state_files(self):
        workflow_state = self.app.agent.research_workflow.load_state("anderson_conjecture")
        workflow_state.next_action = "Check the d=1 genericity lemma."
        workflow_state.last_summary = "The blueprint draft exists and a verification target remains."

        prompt = self.app.agent.research_workflow.build_autonomous_prompt(workflow_state)

        self.assertIn("Continue focusing on the current research progress", prompt)
        self.assertIn("multi-turn conversation", prompt)
        self.assertIn("Continuation contract", prompt)
        self.assertNotIn("projects/anderson_conjecture/memory/research_workflow.json", prompt)
        self.assertNotIn("projects/anderson_conjecture/memory/research_state.json", prompt)
        self.assertNotIn("projects/anderson_conjecture/memory/ledger.jsonl", prompt)
        self.assertIn("Next concrete action hint: Check the d=1 genericity lemma.", prompt)
        self.assertNotIn("commit_turn", prompt)
        self.assertIn("verify_overall", prompt)
        self.assertNotIn("Formal blueprint draft", prompt)
        self.assertNotIn("Recent research artifacts", prompt)

    def test_research_mode_uses_research_control_loop_by_default(self):
        state = self.app.start_shell_state(mode="research", project_slug="neural-network-functions")

        self.assertEqual(state.agent_slug, "research-control-loop")
        meta = self.app.session_store.get_session_meta(state.session_id)
        self.assertEqual(meta.get("agent_slug"), "research-control-loop")

    def test_selected_domain_agent_is_injected_when_explicitly_requested(self):
        system_prompt = self._build_agent_state(
            user_message="Continue the neural network function research.",
            mode="research",
            project_slug="neural-network-functions",
            session_id=self.state.session_id,
            agent_slug="neural-network-functions-researcher",
        ).system_prompt

        self.assertIn("Active agent instructions:", system_prompt)
        self.assertIn("neural network function problems", system_prompt)
        self.assertIn("$problem-generator-neural-network-functions", system_prompt)
        self.assertIn("$direct-proving-neural-network-functions", system_prompt)
        self.assertIn("$construct-counterexamples-neural-network-functions", system_prompt)
        self.assertIn("Domain Working Principle", system_prompt)
        self.assertNotIn("Runtime Prompt", system_prompt)
        self.assertNotIn("moonshine:prompt", system_prompt)

    def test_skill_index_is_agent_specific_for_domain_agent(self):
        neural_state = self._build_agent_state(
            user_message="Continue the neural network function research.",
            mode="research",
            project_slug="neural-network-functions",
            session_id=self.state.session_id,
            agent_slug="neural-network-functions-researcher",
        )
        generic_state = self._build_agent_state(
            user_message="Continue the research project.",
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            agent_slug="research-control-loop",
        )
        core_state = self._build_agent_state(
            user_message="Continue the core research project.",
            mode="chat",
            project_slug="general",
            session_id=self.state.session_id,
            agent_slug="moonshine-core",
        )

        self.assertIn("problem-generator-neural-network-functions", neural_state.system_prompt)
        self.assertIn("direct-proving-neural-network-functions", neural_state.system_prompt)
        self.assertIn("construct-counterexamples-neural-network-functions", neural_state.system_prompt)
        self.assertNotIn("- problem-generator:", neural_state.system_prompt)
        self.assertNotIn("- direct-proving:", neural_state.system_prompt)
        self.assertNotIn("- construct-counterexamples:", neural_state.system_prompt)

        self.assertIn("- problem-generator:", generic_state.system_prompt)
        self.assertIn("- direct-proving:", generic_state.system_prompt)
        self.assertIn("- construct-counterexamples:", generic_state.system_prompt)
        self.assertNotIn("problem-generator-neural-network-functions", generic_state.system_prompt)
        self.assertNotIn("direct-proving-neural-network-functions", generic_state.system_prompt)
        self.assertNotIn("construct-counterexamples-neural-network-functions", generic_state.system_prompt)

        self.assertIn("- problem-generator:", core_state.system_prompt)
        self.assertNotIn("problem-generator-neural-network-functions", core_state.system_prompt)

    def test_domain_agent_does_not_reintroduce_replaced_generic_skill_via_include(self):
        domain_index = self.app.skill_manager.build_prompt_index(
            limit=128,
            include=["direct-proving"],
            agent_slug="neural-network-functions-researcher",
        )
        generic_index = self.app.skill_manager.build_prompt_index(
            limit=128,
            include=["direct-proving"],
            agent_slug="research-control-loop",
        )

        self.assertEqual(domain_index, "")
        self.assertIn("- direct-proving:", generic_index)

    def test_skill_definition_loader_respects_agent_specific_skill_exposure(self):
        neural_runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="neural-network-functions",
            session_id=self.state.session_id,
            agent_slug="neural-network-functions-researcher",
        )
        generic_runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            agent_slug="research-control-loop",
        )

        loaded_domain = self.app.tool_manager.dispatch(
            "load_skill_definition",
            {"slug": "direct-proving-neural-network-functions"},
            neural_runtime,
        )
        self.assertEqual(loaded_domain["slug"], "direct-proving-neural-network-functions")
        with self.assertRaises(KeyError):
            self.app.tool_manager.dispatch(
                "load_skill_definition",
                {"slug": "direct-proving"},
                neural_runtime,
            )
        loaded_generic = self.app.tool_manager.dispatch(
            "load_skill_definition",
            {"slug": "direct-proving"},
            generic_runtime,
        )
        self.assertEqual(loaded_generic["slug"], "direct-proving")
        with self.assertRaises(KeyError):
            self.app.tool_manager.dispatch(
                "load_skill_definition",
                {"slug": "direct-proving-neural-network-functions"},
                generic_runtime,
            )

    def test_start_shell_state_persists_selected_agent_slug(self):
        state = self.app.start_shell_state(
            mode="chat",
            project_slug="general",
            agent_slug="neural-network-functions-researcher",
        )

        self.assertEqual(state.agent_slug, "neural-network-functions-researcher")
        meta = self.app.session_store.get_session_meta(state.session_id)
        self.assertEqual(meta.get("agent_slug"), "neural-network-functions-researcher")

    def test_start_shell_state_can_resume_existing_session_history(self):
        provider = ScriptedProvider(
            [
                {
                    "chunks": ["Continued the resumed session."],
                    "response": ProviderResponse(content="Continued the resumed session."),
                }
            ]
        )
        self.app.agent.provider = provider
        original = self.app.start_shell_state(mode="research", project_slug="anderson_conjecture")
        self.app.session_store.append_message(original.session_id, "user", "ORIGINAL_SESSION_CONTEXT_SENTINEL")
        resumed = self.app.start_shell_state(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=original.session_id,
        )

        self.assertEqual(resumed.session_id, original.session_id)
        self.assertEqual(resumed.project_slug, "anderson_conjecture")
        list(self.app.ask_stream("Continue from the resumed context.", resumed))
        rendered_messages = "\n".join(item["content"] for item in provider.calls[0]["messages"])
        self.assertIn("ORIGINAL_SESSION_CONTEXT_SENTINEL", rendered_messages)

    def test_resume_existing_session_rejects_project_mismatch(self):
        original = self.app.start_shell_state(mode="research", project_slug="anderson_conjecture")

        with self.assertRaises(ValueError):
            self.app.start_shell_state(
                mode="research",
                project_slug="neural-network-functions",
                session_id=original.session_id,
            )

    def test_selected_agent_body_is_injected_into_system_prompt(self):
        system_prompt = self._build_agent_state(
            user_message="Study neural network functions.",
            mode="chat",
            project_slug="general",
            session_id=self.state.session_id,
            agent_slug="neural-network-functions-researcher",
        ).system_prompt

        self.assertIn("Active agent instructions:", system_prompt)
        self.assertIn("Neural-Network-Functions Researcher", system_prompt)
        self.assertIn("$understanding-problems-neural-network-functions", system_prompt)

    def test_research_mode_skips_generic_post_turn_auto_extract(self):
        research_state = self.app.start_shell_state(mode="research", project_slug="anderson_conjecture")

        with mock.patch.object(self.app.memory, "submit_auto_extract", wraps=self.app.memory.submit_auto_extract) as submit_auto_extract:
            self.app.ask("Design a research problem about local criteria for finiteness.", research_state)

        self.assertFalse(submit_auto_extract.called)

    def test_research_mode_skips_legacy_dynamic_memory_extraction_hooks(self):
        research_state = self.app.start_shell_state(mode="research", project_slug="anderson_conjecture")
        self.app.session_store.append_message(
            research_state.session_id,
            "user",
            "Decision: continue the proof. Next step: record progress in the main research branch.",
        )
        self.app.session_store.append_message(research_state.session_id, "assistant", "Progress noted in the research conversation.")

        pre_result = self.app.memory.extract_pre_compress(
            session_id=research_state.session_id,
            project_slug="anderson_conjecture",
            window_text="Decision: continue. Next step: record progress.",
        )
        end_result = self.app.memory.extract_session_end(
            session_id=research_state.session_id,
            project_slug="anderson_conjecture",
        )

        self.assertEqual(pre_result["entries"], 0)
        self.assertEqual(end_result["entries"], 0)
        self.assertFalse(self.app.paths.project_dir("anderson_conjecture").joinpath("memory", "progress.md").exists())
        self.assertFalse(self.app.paths.project_dir("anderson_conjecture").joinpath("memory", "decisions.md").exists())

    def test_research_mode_memory_overview_exposes_research_log_files_only(self):
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )

        payload = self.app.tool_manager.dispatch("memory_overview", {}, runtime)
        paths = [item["path"].replace("\\", "/") for item in payload["files"]]

        self.assertTrue(any(path.endswith("/memory/research_log.jsonl") for path in paths))
        self.assertTrue(any(path.endswith("/memory/research_log.md") for path in paths))
        self.assertFalse(any(path.endswith("/memory/progress.md") for path in paths))
        self.assertFalse(any(path.endswith("/memory/decisions.md") for path in paths))

    def test_record_solve_attempt_tool_result_visible_context_is_compact(self):
        provider_messages = []

        self.app.agent.record_tool_result_message(
            provider_messages,
            {
                "name": "record_solve_attempt",
                "call_id": "call-solve-attempt",
                "arguments": {"title": "Localization branch", "summary": "Long solve-attempt text that should not stay visible."},
                "output": {
                    "id": "artifact-1",
                    "artifact_type": "solve_attempt",
                    "channel": "solve_steps",
                    "artifact_path": "projects/anderson_conjecture/memory/research_state/artifacts/artifact-1.md",
                },
                "error": None,
            },
        )

        payload = json.loads(provider_messages[0]["content"])
        self.assertEqual(payload["name"], "record_solve_attempt")
        self.assertEqual(payload["output"]["status"], "recorded")
        self.assertEqual(payload["output"]["artifact_type"], "solve_attempt")
        self.assertEqual(payload["output"]["channel"], "solve_steps")
        self.assertNotIn("summary", payload["output"])

    def test_startup_context_defaults_to_user_profile_and_preferences_only(self):
        self.app.memory.write_manual_entry(
            alias="user-profile",
            title="User Background",
            body="The user works mainly on mathematical research about neural network functions.",
            summary="Works mainly on mathematical research about neural network functions.",
        )
        self.app.memory.write_manual_entry(
            alias="user-preferences",
            title="User Preference",
            body="The user prefers concise mathematical writing with precise notation.",
            summary="Prefers concise mathematical writing with precise notation.",
        )
        self.app.memory.write_manual_entry(
            alias="reference-resources",
            title="Unrelated News Memory",
            body="An unrelated market news summary that should not be injected by default.",
            summary="Unrelated market news summary.",
        )
        self.app.memory.write_manual_entry(
            alias="project-context",
            project_slug="anderson_conjecture",
            title="Project Context",
            body="A branch-specific historical context note that should be retrieved on demand.",
            summary="Branch-specific historical context note.",
        )

        context = self.app.context_manager.build_startup_context(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )
        prompt_text = context.to_prompt_text()

        self.assertIn("User profile:", prompt_text)
        self.assertIn("User preferences:", prompt_text)
        self.assertIn("neural network functions", prompt_text)
        self.assertIn("concise mathematical writing", prompt_text)
        self.assertNotIn("Standing memory index:", prompt_text)
        self.assertNotIn("Project background:", prompt_text)
        self.assertNotIn("Unrelated News Memory", prompt_text)
        self.assertEqual(context.project_context_summary, "")

    def test_research_workflow_state_is_project_persisted(self):
        state_path = self.app.paths.project_research_workflow_file("anderson_conjecture")
        runtime_state_path = self.app.paths.project_research_runtime_state_file("anderson_conjecture")
        workflow_state = self.app.agent.research_workflow.load_state("anderson_conjecture")

        self.assertTrue(state_path.exists())
        self.assertTrue(runtime_state_path.exists())
        self.assertTrue(self.app.paths.project_scratchpad_file("anderson_conjecture").exists())
        self.assertTrue(self.app.paths.project_agents_file("anderson_conjecture").exists())
        self.assertTrue(self.app.paths.global_agents_file.exists())
        project_agents_text = self.app.paths.project_agents_file("anderson_conjecture").read_text(encoding="utf-8")
        self.assertIn("# Project AGENTS: anderson_conjecture", project_agents_text)
        self.assertIn("Keep this file brief and local to the project.", project_agents_text)
        self.assertNotIn("For live or recent information, prefer live-search tools", project_agents_text)
        self.assertEqual(workflow_state.node, "literature_scan")

    def test_placeholder_problem_draft_does_not_reverse_sync_active_problem(self):
        workflow = self.app.agent.research_workflow
        state = workflow.load_state("anderson_conjecture")
        state.active_problem = ""
        state.workspace_hashes = {}

        changes = workflow._sync_state_from_canonical_workspace(state)

        self.assertTrue(changes["problem_placeholder_skipped"])
        self.assertEqual(state.active_problem, "")
        self.assertNotIn("Use this file as the formal current problem statement", state.active_problem)

    def test_commit_turn_updates_runtime_state_without_scratchpad_write(self):
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )

        payload = self.app.tool_manager.dispatch(
            "commit_turn",
            {
                "title": "Branch alpha checkpoint",
                "summary": "Established the active branch decomposition and recorded the next local lemma.",
                "branch_id": "branch-alpha",
                "current_focus": "Prove the one-dimensional decomposition lemma.",
                "current_claim": "A local reduction suffices in dimension one.",
                "blocker": "Need a clean statement separating the generic and singular cases.",
                "next_action": "Write the local lemma in the research response and test the singular case.",
                "scratchpad": "# Research Scratchpad\n\n## Active Branch\nbranch-alpha\n\n## Local Lemma\nReduce to the dimension-one case first.\n",
                "tags": ["checkpoint", "branch-alpha"],
            },
            runtime,
        )

        workflow_state = self.app.agent.research_workflow.load_state("anderson_conjecture")
        runtime_state = read_json(self.app.paths.project_research_runtime_state_file("anderson_conjecture"), default={})
        scratchpad_text = self.app.paths.project_scratchpad_file("anderson_conjecture").read_text(encoding="utf-8")

        self.assertEqual(payload["kind"], "turn_commit")
        self.assertEqual(workflow_state.active_branch_id, "branch-alpha")
        self.assertIn("one-dimensional", workflow_state.current_focus.lower())
        self.assertEqual(runtime_state.get("active_branch_id"), "branch-alpha")
        self.assertNotIn("scratchpad.md", "\n".join(payload["updated_files"]))
        self.assertNotIn("branch-alpha", scratchpad_text)
        self.assertEqual(read_jsonl(self.app.paths.project_research_ledger_file("anderson_conjecture")), [])

    def test_research_index_drives_query_memory_with_precise_slices(self):
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )
        long_prefix = "irrelevant prefix " * 300
        long_suffix = " irrelevant suffix" * 300
        sentinel = "PRECISE_INDEX_SENTINEL local obstruction lives in the middle of the branch note."

        self.app.tool_manager.dispatch(
            "record_research_artifact",
            {
                "artifact_type": "solve_attempt",
                "title": "Indexed branch attempt",
                "summary": "A long branch attempt with a precise middle obstruction.",
                "content": long_prefix + sentinel + long_suffix,
                "stage": "problem_solving",
                "focus_activity": "solver_branching",
                "metadata": {"branch_id": "branch-index", "claim": "Indexed claim"},
            },
            runtime,
        )
        payload = self.app.tool_manager.dispatch(
            "query_memory",
            {
                "query": "PRECISE_INDEX_SENTINEL obstruction",
                "project_slug": "anderson_conjecture",
                "prefer_raw": True,
            },
            runtime,
        )

        self.assertTrue(self.app.paths.project_research_index_file("anderson_conjecture").exists())
        self.assertTrue(payload["research_hits"])
        self.assertEqual(payload["research_hits"][0]["retrieval_mode"], "research_index")
        self.assertIn("PRECISE_INDEX_SENTINEL", payload["research_hits"][0]["exact_excerpt"])
        self.assertTrue(
            any("Exact Slice" in str(item.get("window_excerpt", "")) for item in payload["compressed_windows"])
        )

    def test_runtime_packet_injects_problem_log_and_index_slices(self):
        workflow = self.app.agent.research_workflow
        state = workflow.load_state("anderson_conjecture")
        state.stage = "problem_solving"
        state.node = "solver_branching"
        state.active_branch_id = "branch-runtime"
        state.current_claim = "Runtime packet claim for the indexed local slice."
        state.current_focus = "Use RUNTIME_PACKET_SENTINEL to continue the active proof branch."
        state.next_action = "Continue from RUNTIME_PACKET_SENTINEL without rereading the whole archive."
        workflow.commit_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            title="Runtime packet seed",
            summary="Seed claim registry.",
            branch_id="branch-runtime",
            current_claim=state.current_claim,
            current_focus=state.current_focus,
        )
        workflow.research_log.append_records(
            "anderson_conjecture",
            [
                {
                    "type": "research_note",
                    "title": "Runtime packet branch note",
                    "content": "RUNTIME_PACKET_SENTINEL appears in the live branch note.",
                    "session_id": self.state.session_id,
                }
            ],
        )
        refreshed = workflow.load_state("anderson_conjecture")

        packet = workflow.build_runtime_packet(refreshed)

        self.assertIn("research_log_index.sqlite", packet)
        self.assertIn("research_log.jsonl", packet)
        self.assertIn("verification.jsonl", packet)
        self.assertNotIn("research_workflow.json", packet)
        self.assertNotIn("research_state.json", packet)
        self.assertNotIn("### scratchpad.md", packet)
        self.assertIn("RUNTIME_PACKET_SENTINEL", packet)
        self.assertIn("Indexed retrieval slices", packet)

    def test_branch_claim_registry_and_duplicate_verification_digest(self):
        workflow = self.app.agent.research_workflow
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )
        claim = "The indexed branch claim has already passed verification."
        workflow.commit_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            title="Claim registry seed",
            summary="Register a current claim on the active branch.",
            branch_id="branch-verify",
            current_claim=claim,
            current_focus="Verify the branch claim once.",
        )
        result = {
            "tool": "verify_overall",
            "status": "completed",
            "passed": True,
            "overall_verdict": "correct",
            "claim": claim,
            "project_slug": "anderson_conjecture",
            "scope": "intermediate",
            "blueprint_path": "",
            "failed_reviewers": [],
            "critical_errors": [],
            "gaps": [],
            "repair_targets": [],
            "summary": "Overall verification passed.",
        }

        workflow.observe_tool_result(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            tool_name="verify_overall",
            arguments={"claim": claim, "scope": "intermediate"},
            output=result,
        )
        workflow.observe_tool_result(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            tool_name="verify_overall",
            arguments={"claim": claim, "scope": "intermediate"},
            output=result,
        )

        state = workflow.load_state("anderson_conjecture")
        verification_rows = read_jsonl(self.app.paths.project_research_verification_file("anderson_conjecture"))
        claim_hash = workflow._claim_hash(claim)

        self.assertTrue(any(item.get("branch_id") == "branch-verify" for item in state.branch_states))
        self.assertIn(claim_hash, state.verified_claim_hashes)
        self.assertEqual(len([item for item in verification_rows if item.get("claim_hash") == claim_hash]), 1)
        self.assertTrue(all(item.get("verification_key") for item in verification_rows if item.get("claim_hash") == claim_hash))

    def test_real_turn_without_commit_relies_on_archival_for_workspace_problem(self):
        active_problem = "REAL_RUN_PROBLEM_SENTINEL: prove the stable scripted problem."
        provider = ResearchWorkflowProvider(
            [
                {
                    "chunks": ["A realistic turn writes sections but forgets commit_turn."],
                    "response": ProviderResponse(
                        content=(
                            "A realistic turn writes sections but forgets commit_turn.\n\n"
                            "Selected problem: %s\n\n"
                            "## Problem Review\n"
                            "- Review Status: passed\n"
                            "- Impact: 0.80\n"
                            "- Feasibility: 0.80\n"
                            "- Novelty: 0.70\n"
                            "- Richness: 0.70\n"
                            "- Overall: 0.76\n"
                            "- Rationale: The realistic scripted problem is ready.\n\n"
                            "## Branch Update\n"
                            "branch-real-run: work on the local reduction branch.\n\n"
                            "REAL_RUN_SCRATCHPAD_SENTINEL: next check the local reduction.\n"
                        )
                        % active_problem,
                    ),
                },
            ],
            [],
            archive_responses=[
                {
                    "records": [
                        {
                            "type": "problem",
                            "title": "Selected scripted problem",
                            "content": active_problem,
                        },
                        {
                            "type": "research_note",
                            "title": "Local reduction branch note",
                            "content": "REAL_RUN_SCRATCHPAD_SENTINEL: next check the local reduction.",
                        },
                    ]
                }
            ],
        )
        self.app.agent.provider = provider

        events = list(self.app.ask_stream("Run one realistic research turn without explicit commit.", self.state))
        workflow_payload = read_json(self.app.paths.project_research_workflow_file("anderson_conjecture"), default={})
        runtime_state = read_json(self.app.paths.project_research_runtime_state_file("anderson_conjecture"), default={})
        problem_text = self.app.paths.project_problem_draft_file("anderson_conjecture").read_text(encoding="utf-8")
        scratchpad_text = self.app.paths.project_scratchpad_file("anderson_conjecture").read_text(encoding="utf-8")

        self.assertTrue(any(event.type == "final" for event in events))
        self.assertIn("REAL_RUN_PROBLEM_SENTINEL", problem_text)
        self.assertNotIn("REAL_RUN_SCRATCHPAD_SENTINEL", scratchpad_text)
        self.assertNotIn("REAL_RUN_PROBLEM_SENTINEL", workflow_payload.get("active_problem", ""))
        self.assertNotIn("REAL_RUN_PROBLEM_SENTINEL", runtime_state.get("active_problem", ""))
        self.assertEqual(read_jsonl(self.app.paths.project_research_ledger_file("anderson_conjecture")), [])
        self.assertFalse(any("branch-real-run" in str(item.get("branch_id") or "") for item in list(workflow_payload.get("branch_states") or [])))

    def test_verification_key_allows_reverify_after_blueprint_changes(self):
        workflow = self.app.agent.research_workflow
        claim = "The same claim should be reverified when the blueprint changes."
        first_proof = "Proof version one."
        second_proof = "Proof version two with a changed argument."
        workflow.commit_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            title="Verification key seed",
            summary="Seed the first blueprint.",
            branch_id="branch-key",
            current_claim=claim,
            blueprint_draft="# Theorem\n\n## Statement\n%s\n\n## Proof\n%s\n" % (claim, first_proof),
        )
        result = {
            "tool": "verify_overall",
            "status": "completed",
            "passed": True,
            "overall_verdict": "correct",
            "claim": claim,
            "project_slug": "anderson_conjecture",
            "scope": "intermediate",
            "blueprint_path": "projects/anderson_conjecture/workspace/blueprint.md",
            "failed_reviewers": [],
            "critical_errors": [],
            "gaps": [],
            "repair_targets": [],
            "summary": "Overall verification passed.",
        }
        workflow.observe_tool_result(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            tool_name="verify_overall",
            arguments={"claim": claim, "scope": "intermediate", "proof": first_proof},
            output=result,
        )
        workflow.observe_tool_result(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            tool_name="verify_overall",
            arguments={"claim": claim, "scope": "intermediate", "proof": first_proof},
            output=result,
        )
        workflow.commit_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            title="Changed proof",
            summary="Change the blueprint so the same claim needs a fresh verification key.",
            branch_id="branch-key",
            current_claim=claim,
            blueprint_draft="# Theorem\n\n## Statement\n%s\n\n## Proof\n%s\n" % (claim, second_proof),
        )
        workflow.observe_tool_result(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            tool_name="verify_overall",
            arguments={"claim": claim, "scope": "intermediate", "proof": second_proof},
            output={**result, "summary": "Overall verification passed after proof change."},
        )

        rows = [
            item
            for item in read_jsonl(self.app.paths.project_research_verification_file("anderson_conjecture"))
            if item.get("claim_hash") == workflow._claim_hash(claim)
        ]
        keys = {item.get("verification_key") for item in rows}

        self.assertEqual(len(rows), 2)
        self.assertEqual(len(keys), 2)

    def test_blueprint_section_after_verification_invalidates_final_gate(self):
        workflow = self.app.agent.research_workflow
        state = workflow.load_state("anderson_conjecture")
        state.stage = "problem_solving"
        state.node = "pessimistic_verification"
        state.active_problem = "A final gate invalidation problem."
        atomic_write(
            self.app.paths.project_blueprint_file("anderson_conjecture"),
            "# Theorem\n\n## Statement\nA final gate invalidation problem.\n\n## Proof\nOld proof.\n",
        )
        state.final_verification_gate = {
            "has_complete_answer": True,
            "ready_for_final_verification": True,
            "blueprint_path": "projects/anderson_conjecture/workspace/blueprint.md",
            "reason": "Old verification passed.",
        }
        state.verification = {"verdict": "verified", "critical_errors": [], "rationale": "Old verification passed."}
        workflow.save_state(state, mirror_progress=False, checkpoint_reason="seed_verified_blueprint")
        provider = ScriptedProvider(
            [
                {
                    "chunks": ["The proof changed without a verifier call."],
                    "response": ProviderResponse(
                        content=(
                            "The proof changed without a verifier call.\n\n"
                            "## Blueprint Draft\n"
                            "# Theorem\n\n"
                            "## Statement\n"
                            "A final gate invalidation problem.\n\n"
                            "## Proof\n"
                            "NEW_BLUEPRINT_SENTINEL changed proof.\n"
                        ),
                    ),
                },
            ]
        )
        self.app.agent.provider = provider

        list(self.app.ask_stream("Revise the blueprint after verification.", self.state))
        refreshed = workflow.load_state("anderson_conjecture")

        self.assertFalse(refreshed.final_verification_gate["ready_for_final_verification"])
        self.assertEqual(refreshed.verification["verdict"], "not_checked")
        self.assertIn(
            "NEW_BLUEPRINT_SENTINEL",
            self.app.paths.project_blueprint_file("anderson_conjecture").read_text(encoding="utf-8"),
        )

    def test_autopilot_policy_distinguishes_hard_soft_and_consolidation(self):
        workflow = self.app.agent.research_workflow
        state = workflow.load_state("anderson_conjecture")
        state.blocker = "Need user decision about whether to use the unpublished external dataset."

        hard_policy = workflow.autopilot_policy(state)

        state.blocker = "The current estimate is too weak in the singular case."
        soft_policy = workflow.autopilot_policy(state)
        repeated_policy = workflow.autopilot_policy(state, stagnant_count=2)

        self.assertTrue(hard_policy["should_stop"])
        self.assertEqual(hard_policy["blocker_kind"], "hard")
        self.assertEqual(soft_policy["action"], "consolidate")
        self.assertEqual(repeated_policy["action"], "consolidate")

    def test_p4_migration_imports_legacy_state_and_archives_fragments(self):
        workflow = self.app.agent.research_workflow
        project = "anderson_conjecture"
        legacy_record = {
            "id": "legacy-verification-record",
            "artifact_type": "verification_report",
            "title": "Verification: legacy claim",
            "summary": "Legacy verification passed.",
            "stage": "problem_solving",
            "focus_activity": "pessimistic_verification",
            "status": "correct",
            "review_status": "passed",
            "metadata": {"claim": "Legacy claim", "tool": "verify_overall"},
            "created_at": "2026-01-01T00:00:00Z",
        }
        moonshine_utils.append_jsonl(self.app.paths.project_research_records_file(project), legacy_record)
        moonshine_utils.append_jsonl(
            self.app.paths.project_research_channel_file(project, "failed_paths"),
            {
                "channel": "failed_paths",
                "activity": "correction",
                "content": "Legacy failed path content.",
                "metadata": {"title": "Legacy failed path"},
                "created_at": "2026-01-02T00:00:00Z",
            },
        )
        nested = self.app.paths.project_dir(project) / "projects" / "nested_project"
        nested.mkdir(parents=True, exist_ok=True)
        atomic_write(nested / "note.md", "nested recursive project")
        atomic_write(self.app.paths.project_workspace_dir(project) / "blueprint_v2.md", "# Fragment\n\nold version")

        payload = workflow.ensure_project_migrated(project)
        verification_rows = read_jsonl(self.app.paths.project_research_verification_file(project))
        archive_dir = self.app.paths.project_research_archive_dir(project)

        self.assertEqual(payload["imported_records"], 0)
        self.assertEqual(payload["imported_channels"], 0)
        self.assertTrue(any(item.get("claim_hash") == workflow._claim_hash("Legacy claim") for item in verification_rows))
        self.assertFalse((self.app.paths.project_dir(project) / "projects").exists())
        self.assertFalse((self.app.paths.project_workspace_dir(project) / "blueprint_v2.md").exists())
        self.assertTrue((archive_dir / "recursive_projects").exists())
        self.assertTrue((archive_dir / "version_fragments").exists())

    def test_refresh_after_turn_ignores_scratchpad_section_without_turn_ledger(self):
        workflow = self.app.agent.research_workflow

        payload = workflow.refresh_after_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            user_message="Continue the proof branch.",
            assistant_message=(
                "We made local progress on the current branch.\n\n"
                "## Scratchpad\n\n"
                "# Research Scratchpad\n\n"
                "## Active Branch\n"
                "The local criterion branch now reduces to checking the singular case.\n"
            ),
        )

        scratchpad_text = self.app.paths.project_scratchpad_file("anderson_conjecture").read_text(encoding="utf-8")

        self.assertNotIn("scratchpad_updated", payload["capture"])
        self.assertNotIn("scratchpad.md", "\n".join(payload["updated_files"]))
        self.assertNotIn("singular case", scratchpad_text)
        self.assertNotIn("auto_commit", payload)
        self.assertNotIn("ledger_entry", payload)
        self.assertEqual(read_jsonl(self.app.paths.project_research_ledger_file("anderson_conjecture")), [])

    def test_research_artifacts_drive_stage_transition(self):
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )

        self.app.tool_manager.dispatch(
            "record_research_artifact",
            {
                "artifact_type": "candidate_problem",
                "title": "Local Criterion Problem",
                "summary": "Candidate problem for a local finiteness criterion.",
                "content": "Prove a local criterion for a finiteness property over Noetherian rings.",
                "set_as_active": True,
            },
            runtime,
        )
        blocked = self.app.tool_manager.dispatch(
            "record_research_artifact",
            {
                "artifact_type": "stage_transition",
                "title": "Attempt to enter solving",
                "summary": "Try to move into problem solving before review is saved.",
                "metadata": {
                    "target_stage": "problem_solving",
                    "reason": "The problem feels ready.",
                    "next_action": "Decompose the active problem.",
                },
            },
            runtime,
        )
        blocked_state = self.app.agent.research_workflow.load_state("anderson_conjecture")

        self.assertFalse(blocked["applied"]["approved"])
        self.assertEqual(blocked_state.stage, "problem_design")
        self.assertIn("dedicated quality-assessor review", blocked_state.transition_status["reason"])

        self.app.tool_manager.dispatch(
            "record_research_artifact",
            {
                "artifact_type": "problem_review",
                "title": "Review: Local Criterion Problem",
                "summary": "Passed quality review for entering problem solving.",
                "review_status": "passed",
                "set_as_active": True,
                "metadata": {
                    "skill_slug": "quality-assessor",
                    "quality_scores": {
                        "impact": 0.82,
                        "feasibility": 0.76,
                        "novelty": 0.74,
                        "richness": 0.71,
                        "overall": 0.78,
                        "rationale": "Strong enough to justify solving work.",
                    }
                },
            },
            runtime,
        )
        approved = self.app.tool_manager.dispatch(
            "record_research_artifact",
            {
                "artifact_type": "stage_transition",
                "title": "Enter problem solving",
                "summary": "The active problem now has a passed review and can move into solving.",
                "metadata": {
                    "target_stage": "problem_solving",
                    "reason": "The saved review passed the threshold.",
                    "next_action": "Decompose the selected problem into subgoals.",
                },
            },
            runtime,
        )
        approved_state = self.app.agent.research_workflow.load_state("anderson_conjecture")
        records = read_jsonl(self.app.paths.project_research_log_file("anderson_conjecture"))

        self.assertTrue(approved["applied"]["approved"])
        self.assertEqual(approved_state.stage, "problem_solving")
        self.assertEqual(approved_state.node, "problem_decomposition")
        self.assertEqual(approved_state.problem_review["review_status"], "passed")
        self.assertTrue(any(item["type"] == "problem" for item in records))
        self.assertTrue(any(item["type"] == "verification" for item in records))
        self.assertTrue(any(item["type"] == "research_note" for item in records))

    def test_workflow_update_cannot_enter_problem_solving_without_quality_assessor_pass(self):
        workflow = self.app.agent.research_workflow
        state = workflow.load_state("anderson_conjecture")
        state.stage = "problem_design"
        state.node = "problem_refinement"
        state.active_problem = "Prove the scripted local criterion."
        state.problem_review = {
            "title": "",
            "summary": "",
            "review_status": "not_reviewed",
            "passed": False,
            "quality_scores": {
                "impact": 0.82,
                "feasibility": 0.76,
                "novelty": 0.72,
                "richness": 0.70,
                "overall": 0.77,
                "rationale": "Looks promising but has not received a dedicated quality review.",
            },
            "metadata": {},
            "updated_at": "",
        }
        state.quality_scores = dict(state.problem_review["quality_scores"])

        payload = workflow.apply_update(
            state=state,
            update={
                "state_assessment": {
                    "current_focus": "Try to enter solving directly.",
                    "search_sufficiency": "adequate",
                    "reasoning_state": "making_progress",
                    "memory_need": "write_project",
                    "risk_level": "medium",
                    "rationale": "The candidate looks ready, but the review gate has not passed yet.",
                },
                "control_selection": {
                    "selected_skills": [],
                    "selected_tools": [],
                    "trigger_rules_used": [],
                    "selection_rationale": "",
                },
                "activity_status": "checkpointed",
                "recommended_next_activity": "problem_decomposition",
                "stage_decision": "advance_to_problem_solving",
                "active_problem": state.active_problem,
                "candidate_problems": [],
                "quality_scores": dict(state.quality_scores),
                "verification": {
                    "verdict": "not_checked",
                    "critical_errors": [],
                    "rationale": "",
                },
                "open_questions": [],
                "failed_paths": [],
                "research_artifacts": {
                    "immediate_conclusions": [],
                    "toy_examples": [],
                    "counterexamples": [],
                    "big_decisions": [],
                    "special_case_checks": [],
                    "novelty_notes": [],
                    "subgoals": [],
                    "solve_steps": [],
                    "failed_paths": [],
                    "verification_reports": [],
                    "branch_states": [],
                    "events": [],
                },
                "instruction_conflicts": [],
                "branch_updates": [],
                "conclusions_to_store": [],
                "intermediate_verification": {
                    "needed": False,
                    "verdict": "not_needed",
                    "targets": [],
                    "rationale": "",
                },
                "final_verification_gate": {
                    "has_complete_answer": False,
                    "ready_for_final_verification": False,
                    "blueprint_path": "",
                    "reason": "",
                },
                "memory_updates": [],
                "summary": "Tried to jump into solving without a dedicated quality-assessor pass.",
                "next_action": "",
                "controller_rationale": "Direct transition attempt.",
                "confidence": 0.6,
            },
            session_id=self.state.session_id,
        )

        self.assertEqual(payload["stage"], "problem_design")
        self.assertEqual(payload["current_activity"], "quality_evaluation")
        self.assertIn("quality-assessor", payload["next_action"])
        self.assertFalse(state.transition_status["approved"])
        self.assertIn("dedicated quality-assessor review", state.transition_status["reason"])

    def test_query_memory_retrieves_research_state_artifacts(self):
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )

        self.app.tool_manager.dispatch(
            "record_research_artifact",
            {
                "artifact_type": "example",
                "title": "Localization Example",
                "summary": "A toy example over k[x] suggests why localization at maximal ideals is natural.",
                "content": "Take R = k[x]. Localizing at maximal ideals preserves the local finiteness condition in the scripted example.",
                "status": "recorded",
            },
            runtime,
        )
        payload = self.app.tool_manager.dispatch(
            "query_memory",
            {
                "query": "localization example over k[x]",
                "project_slug": "anderson_conjecture",
            },
            runtime,
        )

        self.assertTrue(payload["research_hits"])
        self.assertTrue(any(item["source"] == "research-artifact" for item in payload["compressed_windows"]))
        self.assertIn("Localization Example", json.dumps(payload["research_hits"], ensure_ascii=False))

    def test_query_memory_can_scope_research_retrieval_to_selected_channels(self):
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )

        self.app.tool_manager.dispatch(
            "record_research_artifact",
            {
                "artifact_type": "failed_path",
                "title": "Localization-only path fails",
                "summary": "The localization-only route loses the finiteness hypothesis.",
                "content": "FAILED_PATH_UNIQUE_SENTINEL\nThe localization-only route loses the finiteness hypothesis and must be repaired.",
                "status": "recorded",
            },
            runtime,
        )
        self.app.tool_manager.dispatch(
            "record_research_artifact",
            {
                "artifact_type": "subgoal_plan",
                "title": "Alternative decomposition",
                "summary": "Split the theorem into two local lemmas.",
                "content": "SUBGOAL_UNIQUE_SENTINEL\nSplit the theorem into a finiteness lemma and a localization lemma.",
                "status": "recorded",
            },
            runtime,
        )

        payload = self.app.tool_manager.dispatch(
            "query_memory",
            {
                "query": "current blocker",
                "project_slug": "anderson_conjecture",
                "channels": ["failed_paths"],
                "channel_mode": "recent",
                "limit_per_channel": 2,
            },
            runtime,
        )

        self.assertEqual(payload["types"], ["failed_path"])
        self.assertTrue(payload["research_hits"])
        self.assertTrue(all(item.get("type") == "failed_path" for item in payload["research_hits"]))
        self.assertFalse(any("SUBGOAL_UNIQUE_SENTINEL" in json.dumps(item, ensure_ascii=False) for item in payload["research_hits"]))
        self.assertTrue(any("FAILED_PATH_UNIQUE_SENTINEL" in str(item.get("window_excerpt", "")) for item in payload["compressed_windows"]))

    def test_query_memory_scopes_canonical_solve_steps_channel(self):
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )
        workflow = self.app.agent.research_workflow
        workflow.record_artifact(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            artifact_type="solve_attempt",
            title="Canonical solve attempt",
            summary="The current branch reduces the task to a local construction step.",
            content="CANONICAL_SOLVE_STEP_SENTINEL\nReduce the task to a local construction step.",
            stage="problem_solving",
            focus_activity="solver_branching",
            tags=["solve-attempt"],
        )

        payload = self.app.tool_manager.dispatch(
            "query_memory",
            {
                "query": "branch progress",
                "project_slug": "anderson_conjecture",
                "channels": ["solve_steps"],
                "channel_mode": "recent",
                "limit_per_channel": 3,
            },
            runtime,
        )
        navigation_brief = workflow._navigation_memory_brief(
            "anderson_conjecture",
            "problem_solving",
            limit_per_channel=1,
            token_budget=4000,
        )

        self.assertEqual(payload["types"], ["research_note"])
        self.assertTrue(payload["research_hits"])
        self.assertTrue(all(item.get("type") == "research_note" for item in payload["research_hits"]))
        self.assertTrue(
            any(
                "CANONICAL_SOLVE_STEP_SENTINEL" in str(item.get("window_excerpt", ""))
                for item in payload["compressed_windows"]
            )
        )
        self.assertIn("`solve_steps`", navigation_brief)

    def test_store_conclusion_is_not_exposed_in_research_mode(self):
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )

        with self.assertRaises(RuntimeError):
            self.app.tool_manager.dispatch(
                "store_conclusion",
                {
                    "title": "Candidate Local Criterion",
                    "statement": "A candidate local criterion reduces the global claim to maximal ideals.",
                    "proof_sketch": "Only a sketch exists so far.",
                    "project_slug": "anderson_conjecture",
                    "status": "verified",
                },
                runtime,
            )
        with self.assertRaises(RuntimeError):
            self.app.tool_manager.dispatch(
                "add_knowledge",
                {
                    "title": "Candidate Local Criterion",
                    "statement": "A candidate local criterion reduces the global claim to maximal ideals.",
                    "proof_sketch": "Only a sketch exists so far.",
                    "project_slug": "anderson_conjecture",
                },
                runtime,
            )

    def test_research_mode_completes_tool_assisted_adaptive_workflow(self):
        active_problem = "The finiteness criterion reduces to checks at maximal ideals."
        blueprint_text = (
            "# theorem local-criterion\n\n"
            "## statement\n"
            "%s\n\n"
            "## proof\n"
            "Assume each maximal localization satisfies the target finiteness condition. "
            "By the scripted localization lemma, the obstruction is local and therefore vanishes globally. "
            "This yields the desired criterion."
        ) % active_problem
        provider = ResearchWorkflowProvider(
            [
                {
                    "response": ProviderResponse(
                        content="",
                        tool_calls=[
                            ProviderToolCall(
                                name="query_memory",
                                arguments={"query": "local criterion Noetherian rings", "all_projects": True},
                                call_id="call-memory-1",
                            ),
                        ],
                    ),
                },
                {
                    "chunks": ["Candidate problem selected."],
                    "response": ProviderResponse(
                        content=(
                            "Literature scan found a viable local criterion direction.\n\n"
                            "## Candidate Problem\n"
                            "Prove a local criterion for a finiteness property over Noetherian rings.\n\n"
                            "Selected problem: %s\n"
                        )
                        % active_problem,
                    ),
                },
                {
                    "response": ProviderResponse(
                        content="",
                        tool_calls=[
                            ProviderToolCall(
                                name="assess_problem_quality",
                                arguments={
                                    "problem": active_problem,
                                    "context": "Scripted local criterion candidate selected during problem design.",
                                    "project_slug": "anderson_conjecture",
                                },
                                call_id="call-quality-1",
                            ),
                        ],
                    ),
                },
                {
                    "chunks": ["Quality review and blueprint draft prepared."],
                    "response": ProviderResponse(
                        content=(
                            "The candidate is ready for solving.\n\n"
                            "## Stage Transition\n"
                            "Enter problem_solving. The active problem has now passed quality review and is ready for decomposition.\n\n"
                            "Accepted proof text for verification:\n\n"
                            "%s\n"
                        )
                        % blueprint_text,
                    ),
                },
                {
                    "response": ProviderResponse(
                        content="",
                        tool_calls=[
                            ProviderToolCall(
                                name="verify_overall",
                                arguments={
                                    "claim": active_problem,
                                    "proof": blueprint_text,
                                    "project_slug": "anderson_conjecture",
                                    "scope": "final",
                                    "blueprint_path": "projects/anderson_conjecture/workspace/blueprint.md",
                                },
                                call_id="call-verify-1",
                            ),
                        ],
                    ),
                },
                {
                    "chunks": ["The proof blueprint passed verification. The final result is the verified reduction to maximal ideals."],
                    "response": ProviderResponse(
                        content=(
                            "The proof blueprint passed verification.\n\n"
                            "Final result: Verified Reduction to Maximal Ideals. "
                            "The finiteness criterion reduces to checks at maximal ideals, by the verified localization blueprint."
                        )
                    ),
                },
            ],
            [
                json.dumps(
                    {
                        "reviewer_id": "quality-assessor",
                        "review_status": "passed",
                        "impact": 0.82,
                        "feasibility": 0.76,
                        "novelty": 0.72,
                        "richness": 0.70,
                        "overall": 0.77,
                        "strengths": ["The scripted candidate is precise enough to attack."],
                        "weaknesses": [],
                        "required_refinements": [],
                        "rationale": "The scripted candidate is ready for solving.",
                        "confidence": 0.88,
                    }
                ),
                json.dumps(self._dimension_review(reviewer_id="assumption-usage-reviewer", dimension="assumption", verdict="correct")),
                json.dumps(self._dimension_review(reviewer_id="calculation-consistency-reviewer", dimension="computation", verdict="correct")),
                json.dumps(self._dimension_review(reviewer_id="logical-chain-reviewer", dimension="logic", verdict="correct")),
            ],
            archive_responses=[
                {
                    "records": [
                        {
                            "type": "problem",
                            "title": "Selected local criterion problem",
                            "content": active_problem,
                        }
                    ]
                },
                {"records": []},
                {
                    "records": [
                        {
                            "type": "research_note",
                            "title": "Accepted proof text for verification",
                            "content": blueprint_text,
                        },
                        {
                            "type": "verification",
                            "title": "Final verification passed",
                            "content": "verify_overall(scope=\"final\") passed for the localization blueprint.",
                        },
                        {
                            "type": "verified_conclusion",
                            "title": "Verified Reduction to Maximal Ideals",
                            "content": active_problem,
                        },
                        {
                            "type": "final_result",
                            "title": "Final result",
                            "content": "The finiteness criterion reduces to checks at maximal ideals, by the verified localization blueprint.",
                        },
                    ]
                },
            ],
        )
        self.app.agent.provider = provider

        first_events = list(self.app.ask_stream("Run the autonomous research workflow for the local criterion problem.", self.state))
        list(self.app.ask_stream("Continue with quality evaluation.", self.state))
        final_events = list(self.app.ask_stream("Verify and persist the result.", self.state))

        workflow_payload = json.loads(
            self.app.paths.project_research_workflow_file("anderson_conjecture").read_text(encoding="utf-8")
        )
        provider_rounds = read_jsonl(self.app.paths.session_provider_rounds_file(self.state.session_id))
        tool_events = read_jsonl(self.app.paths.session_tool_events_file(self.state.session_id))
        research_records = read_jsonl(self.app.paths.project_research_log_file("anderson_conjecture"))
        events_channel = read_jsonl(self.app.paths.project_research_channel_file("anderson_conjecture", "events"))
        verification_channel = read_jsonl(self.app.paths.project_research_channel_file("anderson_conjecture", "verification_reports"))
        final_payload = [event.payload for event in final_events if event.type == "final"][-1]
        problem_draft_text = self.app.paths.project_problem_draft_file("anderson_conjecture").read_text(encoding="utf-8")
        blueprint_path = self.app.paths.project_blueprint_file("anderson_conjecture")
        verified_blueprint_path = self.app.paths.project_blueprint_verified_file("anderson_conjecture")

        self.assertTrue(any(event.type == "tool_call" and event.text == "query_memory" for event in first_events))
        self.assertEqual(workflow_payload["status"], "completed")
        self.assertEqual(workflow_payload["stage"], "problem_solving")
        self.assertEqual(workflow_payload["node"], "pessimistic_verification")
        self.assertGreaterEqual(workflow_payload["iteration_count"], 3)
        self.assertTrue(provider_rounds)
        self.assertTrue(tool_events)
        self.assertTrue(research_records)
        self.assertEqual(events_channel, [])
        self.assertEqual(verification_channel, [])
        self.assertFalse(final_payload["render_final"])
        self.assertIn(active_problem, problem_draft_text)
        self.assertNotIn("moonshine:auto-problem-draft", problem_draft_text)
        self.assertIn("Final verification passed", blueprint_path.read_text(encoding="utf-8"))
        self.assertIn("Accepted proof text for verification", blueprint_path.read_text(encoding="utf-8"))
        self.assertIn("verify_overall(scope=\"final\") passed", verified_blueprint_path.read_text(encoding="utf-8"))

        knowledge_hits = self.app.memory.knowledge_store.search(
            "maximal ideals",
            project_slug="anderson_conjecture",
            limit=5,
        )

        self.assertTrue(any(item["title"] == "Verified Reduction to Maximal Ideals" for item in knowledge_hits))
        self.assertTrue(any(item["type"] == "verification" for item in research_records))
        self.assertTrue(any(item["type"] == "verified_conclusion" for item in research_records))
        self.assertTrue(any(item["type"] == "final_result" for item in research_records))

    def test_research_mode_requires_explicit_stage_transition_section(self):
        active_problem = "Study the scripted local criterion problem."
        provider = ResearchWorkflowProvider(
            [
                {
                    "chunks": ["The design work selected and reviewed a concrete problem."],
                    "response": ProviderResponse(
                        content=(
                            "The design work selected and reviewed a concrete problem.\n\n"
                            "## Candidate Problem\n"
                            "%s\n\n"
                            "Selected problem: %s\n"
                        )
                        % (active_problem, active_problem),
                    ),
                },
            ],
            [],
            archive_responses=[
                {
                    "records": [
                        {
                            "type": "problem",
                            "title": "Selected explicit-stage-transition problem",
                            "content": active_problem,
                        }
                    ]
                }
            ],
        )
        self.app.agent.provider = provider

        list(self.app.ask_stream("Design a viable research problem first.", self.state))
        workflow_payload = json.loads(
            self.app.paths.project_research_workflow_file("anderson_conjecture").read_text(encoding="utf-8")
        )

        self.assertEqual(workflow_payload["stage"], "problem_design")
        self.assertEqual(workflow_payload["problem_review"]["review_status"], "not_reviewed")
        self.assertIn("quality-assessor", workflow_payload["next_action"])

    def test_research_mode_does_not_auto_sync_into_problem_solving_from_blueprint_alone(self):
        workflow = self.app.agent.research_workflow
        state = workflow.load_state("anderson_conjecture")
        state.stage = "problem_design"
        state.node = "problem_refinement"
        state.active_problem = "Prove the scripted local criterion."
        state.problem_review = {
            "statement": state.active_problem,
            "summary": "The active problem is ready for solving.",
            "review_status": "passed",
            "quality_scores": {
                "impact": 0.82,
                "feasibility": 0.78,
                "novelty": 0.72,
                "richness": 0.71,
                "overall": 0.78,
                "rationale": "The saved problem is mature enough to solve.",
            },
            "metadata": {"skill_slug": "quality-assessor"},
        }
        workflow.save_state(state, mirror_progress=False, checkpoint_reason="seed")
        atomic_write(
            self.app.paths.project_blueprint_file("anderson_conjecture"),
            (
                "# Theorem\n\n"
                "## Statement\n"
                "Prove the scripted local criterion.\n\n"
                "## Proof\n"
                "A complete-looking scripted blueprint already exists and should move the state into solving. "
                "The argument fixes the local data, isolates the controlling exceptional set, carries the reduction "
                "through the finite-support case, and then lifts the same mechanism back to the full statement. "
                "It also records the final reduction step explicitly so the draft is treated as a real formal "
                "blueprint rather than a skeletal outline.\n"
            ),
        )

        payload = workflow.refresh_after_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            user_message="Continue from the saved state.",
            assistant_message="Continue from the saved blueprint.",
        )
        refreshed = workflow.load_state("anderson_conjecture")

        self.assertEqual(payload["stage"], "problem_design")
        self.assertEqual(refreshed.stage, "problem_design")
        self.assertEqual(refreshed.node, "problem_refinement")

    def test_fresh_project_layout_does_not_create_root_level_workspace(self):
        self.app.ensure_project("fresh_project")

        self.assertTrue(self.app.paths.project_workspace_dir("fresh_project").exists())
        self.assertTrue(self.app.paths.project_problem_draft_file("fresh_project").exists())
        self.assertFalse((self.app.paths.home / "workspace").exists())
        self.assertFalse((self.app.paths.home / "references").exists())

    def test_research_mode_sections_do_not_write_problem_or_blueprint_workspace(self):
        workflow = self.app.agent.research_workflow

        workflow.refresh_after_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            user_message="Continue the project.",
            assistant_message=(
                "## Problem Draft\n\n"
                "# Current Problem Draft\n\n"
                "## Statement\n"
                "Classify zero sets of generic ReLU networks.\n\n"
                "## Blueprint Draft\n\n"
                "# Theorem\n\n"
                "## Statement\n"
                "A scripted theorem draft.\n\n"
                "## Proof\n"
                "A scripted proof draft.\n"
            ),
        )

        self.assertNotIn(
            "Classify zero sets of generic ReLU networks.",
            self.app.paths.project_problem_draft_file("anderson_conjecture").read_text(encoding="utf-8"),
        )
        self.assertNotIn(
            "A scripted proof draft.",
            self.app.paths.project_blueprint_file("anderson_conjecture").read_text(encoding="utf-8"),
        )
        self.assertFalse((self.app.paths.home / "workspace").exists())

    def test_research_mode_tracks_navigation_progress_from_visible_tool_results(self):
        provider = ScriptedProvider(
            [
                {
                    "response": ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                name="load_skill_definition",
                                arguments={"slug": "research-consolidation"},
                                call_id="call-skill-1",
                            ),
                            ProviderToolCall(
                                name="query_memory",
                                arguments={
                                    "query": "maximal-ideal localization route",
                                    "project_slug": "anderson_conjecture",
                                },
                                call_id="call-query-1",
                            ),
                        ]
                    )
                },
                {
                    "chunks": ["I stored the checkpoint and reloaded the relevant memory."],
                    "response": ProviderResponse(content="I stored the checkpoint and reloaded the relevant memory."),
                },
            ]
        )
        self.app.agent.provider = provider

        list(self.app.ask_stream("Continue the research workflow from the current branch.", self.state))

        workflow_payload = json.loads(
            self.app.paths.project_research_workflow_file("anderson_conjecture").read_text(encoding="utf-8")
        )
        research_records = read_jsonl(self.app.paths.project_research_log_file("anderson_conjecture"))

        retrieval_progress = [
            item
            for item in research_records
            if item["type"] == "research_note" and "maximal-ideal localization route" in str(item.get("content") or "")
        ]
        retrieval_notes = [
            item
            for item in research_records
            if item["type"] == "research_note" and "Memory retrieval" in str(item.get("title") or "")
        ]

        self.assertTrue(retrieval_progress)
        self.assertTrue(retrieval_notes)
        self.assertIn("research-consolidation", workflow_payload["selected_skills"])
        self.assertEqual(workflow_payload["node"], "design_checkpoint")
        self.assertIn(
            "maximal-ideal localization route",
            json.dumps(retrieval_notes, ensure_ascii=False),
        )

    def test_research_mode_no_longer_auto_records_skill_side_artifacts(self):
        workflow = self.app.agent.research_workflow
        state = workflow.load_state("anderson_conjecture")
        state.stage = "problem_solving"
        state.node = "solver_branching"
        state.selected_skills = ["construct-counterexamples"]
        workflow.save_state(state, mirror_progress=False, checkpoint_reason="test_seed_skill_context")

        workflow.refresh_after_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            user_message="Search for a counterexample.",
            assistant_message=(
                "A valuation-domain counterexample shows the auxiliary claim needs an extra finiteness hypothesis. "
                "The current formulation fails on this branch."
            ),
        )

        research_records = read_jsonl(self.app.paths.project_research_log_file("anderson_conjecture"))

        self.assertFalse(
            any("construct-counterexamples" in json.dumps(item, ensure_ascii=False) for item in research_records)
        )

    def test_research_mode_auto_records_special_case_check_from_recent_skill(self):
        workflow = self.app.agent.research_workflow
        state = workflow.load_state("anderson_conjecture")
        state.stage = "problem_design"
        state.node = "quality_evaluation"
        state.selected_skills = ["examination-of-special-cases-neural-network-functions"]
        workflow.save_state(state, mirror_progress=False, checkpoint_reason="test_seed_special_case_skill")

        workflow.refresh_after_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            user_message="Test simple cases.",
            assistant_message=(
                "Testing one-neuron ReLU networks shows the current formulation does not survive this low-complexity case "
                "because the claimed closure property already fails there."
            ),
        )

        research_records = read_jsonl(self.app.paths.project_research_log_file("anderson_conjecture"))

        self.assertFalse(
            any(
                "examination-of-special-cases-neural-network-functions" in json.dumps(item, ensure_ascii=False)
                for item in research_records
            )
        )

    def test_research_mode_auto_records_novelty_note_from_recent_skill(self):
        workflow = self.app.agent.research_workflow
        state = workflow.load_state("anderson_conjecture")
        state.stage = "problem_solving"
        state.node = "persistence"
        state.selected_skills = ["record-novelty"]
        workflow.save_state(state, mirror_progress=False, checkpoint_reason="test_seed_novelty_skill")

        workflow.refresh_after_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            user_message="Extract the novelty.",
            assistant_message=(
                "The current solution contributes a new concept of architecture-sensitive closure and a new method "
                "that transfers polynomial structural arguments into a neural-network-function setting."
            ),
        )

        research_records = read_jsonl(self.app.paths.project_research_log_file("anderson_conjecture"))

        self.assertFalse(
            any("record-novelty" in json.dumps(item, ensure_ascii=False) for item in research_records)
        )

    def test_research_mode_live_assessment_refreshes_correction_and_strengthening_attempts(self):
        workflow = self.app.agent.research_workflow
        state = workflow.load_state("anderson_conjecture")
        state.stage = "problem_solving"
        state.node = "correction"
        state.active_problem = "Repair the local criterion proof after a failed verifier audit."
        workflow.save_state(state, mirror_progress=False, checkpoint_reason="test_seed")

        workflow.observe_tool_result(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            tool_name="pessimistic_verify",
            arguments={
                "claim": "Repair the local criterion proof after a failed verifier audit.",
                "proof": "A broken proof sketch.",
                "project_slug": "anderson_conjecture",
                "scope": "intermediate",
            },
            output={
                "tool": "pessimistic_verify",
                "status": "completed",
                "passed": False,
                "overall_verdict": "failed",
                "failure_policy": "any reviewer failure fails the aggregate",
                "review_count": 2,
                "failed_reviewers": ["logic-chain-reviewer"],
                "claim": "Repair the local criterion proof after a failed verifier audit.",
                "project_slug": "anderson_conjecture",
                "reviewed_at": "2026-04-24T12:00:00Z",
                "reviews": [],
                "critical_errors": ["A localization step omits a finiteness hypothesis."],
                "gaps": ["The proof does not justify the reduction to maximal ideals."],
                "hidden_assumptions": [],
                "citation_issues": [],
                "calculation_issues": [],
                "repair_hints": ["State and prove the missing finiteness lemma before reusing the reduction."],
                "summary": "The current solve attempt fails pessimistic verification.",
            },
        )

        correction_state = workflow.load_state("anderson_conjecture")
        correction_state.node = "correction"
        workflow.save_state(correction_state, mirror_progress=False, checkpoint_reason="test_force_correction")

        payload = workflow.refresh_after_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            user_message="Repair the failed proof branch.",
            assistant_message="I will repair the failed branch by isolating the missing finiteness lemma.",
        )
        updated_state = workflow.load_state("anderson_conjecture")

        self.assertEqual(payload["state_assessment"]["reasoning_state"], "repairing")
        self.assertEqual(payload["state_assessment"]["risk_level"], "high")
        self.assertEqual(updated_state.state_assessment["reasoning_state"], "repairing")
        self.assertEqual(updated_state.correction_attempts, 1)
        self.assertIn(
            "Repair the local criterion proof after a failed verifier audit.",
            updated_state.pending_verification_items,
        )

        updated_state.node = "strengthening"
        workflow.save_state(updated_state, mirror_progress=False, checkpoint_reason="test_force_strengthening")
        strengthen_payload = workflow.refresh_after_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            user_message="Try a stronger formulation instead of another local patch.",
            assistant_message="The repeated repair pressure suggests strengthening the assumptions.",
        )
        strengthened_state = workflow.load_state("anderson_conjecture")

        self.assertEqual(strengthen_payload["state_assessment"]["reasoning_state"], "stuck")
        self.assertEqual(strengthened_state.strengthening_attempts, 1)
        self.assertEqual(strengthened_state.correction_attempts, 1)

    def test_autonomous_prompt_discourages_long_status_recaps_and_eta_promises(self):
        workflow = self.app.agent.research_workflow
        state = workflow.load_state("anderson_conjecture")
        state.next_action = "Emit a `## Stage Transition` section to enter problem solving."
        workflow.save_state(state, mirror_progress=False, checkpoint_reason="test_seed_autonomous_prompt")
        prompt = workflow.build_autonomous_prompt(workflow.load_state("anderson_conjecture"))

        self.assertIn("Continue focusing on the current research progress", prompt)
        self.assertIn("Use the existing conversation history, tool results, and current proof text", prompt)
        self.assertIn("Next concrete action hint: Emit a `## Stage Transition` section to enter problem solving.", prompt)
        self.assertNotIn("projects/anderson_conjecture/memory/research_workflow.json", prompt)
        self.assertNotIn("projects/anderson_conjecture/memory/research_state.json", prompt)
        self.assertNotIn("ETA", prompt)
        self.assertNotIn("within 24 h", prompt)

    def test_research_prompt_discourages_timeline_language_without_explicit_request(self):
        workflow = self.app.agent.research_workflow
        prompt = workflow.build_prompt(workflow.load_state("anderson_conjecture"))

        self.assertIn("Work as a professional mathematical researcher", prompt)
        self.assertIn("keep the reply centered on mathematical claims, constructions, checks, experiments, verifier evidence, and retrieved sources", prompt)

    def test_tool_driven_navigation_notes_cover_knowledge_and_reference_reads(self):
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )
        self.app.memory.knowledge_store.add_conclusion(
            title="Local Criterion",
            statement="Reduce the global claim to maximal ideals.",
            proof_sketch="Use local reductions before integrating the final branch.",
            status="partial",
            project_slug="anderson_conjecture",
            source_type="test",
            source_ref=self.state.session_id,
        )
        reference_path = self.app.paths.project_references_dir("anderson_conjecture") / "localization_note.md"
        atomic_write(
            reference_path,
            "# Localization Note\n\nLocalization at maximal ideals preserves the scripted local condition.",
        )

        search_arguments = {"query": "local criterion", "project_slug": "anderson_conjecture"}
        search_output = self.app.tool_manager.dispatch("search_knowledge", search_arguments, runtime)
        self.app.agent.research_workflow.observe_tool_result(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            tool_name="search_knowledge",
            arguments=search_arguments,
            output=search_output,
        )
        self.app.agent.research_workflow.observe_tool_result(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            tool_name="search_knowledge",
            arguments=search_arguments,
            output=search_output,
        )

        read_arguments = {"relative_path": "projects/anderson_conjecture/references/localization_note.md"}
        read_output = self.app.tool_manager.dispatch("read_runtime_file", read_arguments, runtime)
        self.app.agent.research_workflow.observe_tool_result(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            tool_name="read_runtime_file",
            arguments=read_arguments,
            output=read_output,
        )
        self.app.agent.research_workflow.observe_tool_result(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            tool_name="read_runtime_file",
            arguments=read_arguments,
            output=read_output,
        )

        research_records = read_jsonl(self.app.paths.project_research_log_file("anderson_conjecture"))
        knowledge_notes = [
            item
            for item in research_records
            if item["type"] == "research_note" and "Knowledge search" in str(item.get("title") or "")
        ]
        file_notes = [
            item
            for item in research_records
            if item["type"] == "research_note" and "Loaded local reference" in str(item.get("title") or "")
        ]

        self.assertEqual(len(knowledge_notes), 1)
        self.assertEqual(len(file_notes), 1)
        self.assertIn("Local Criterion", json.dumps(knowledge_notes, ensure_ascii=False))
        self.assertIn("localization_note.md", json.dumps(file_notes, ensure_ascii=False))

    def test_research_navigation_auto_load_exposes_research_log_index_not_raw_content(self):
        workflow = self.app.agent.research_workflow
        original_content = (
            "# Localization Branch Plan\n\n"
            "1. Reduce the global statement to maximal ideals.\n"
            "2. Prove the finiteness lemma before localization.\n"
            "3. Reassemble the proof from the local branch.\n\n"
            "UNIQUE_TRAILING_SENTINEL_FOR_FULL_CONTENT"
        )

        workflow.research_log.append_records(
            "anderson_conjecture",
            [
                {
                    "type": "research_note",
                    "title": "Localization branch plan",
                    "content": original_content,
                    "session_id": self.state.session_id,
                }
            ],
        )

        channel_rows = read_jsonl(
            self.app.paths.project_research_channel_file("anderson_conjecture", "subgoals")
        )
        navigation_brief = workflow._navigation_memory_brief(
            "anderson_conjecture",
            "problem_solving",
            limit_per_channel=1,
            token_budget=4000,
        )

        self.assertEqual(channel_rows, [])
        self.assertIn("`research_note`", navigation_brief)
        self.assertIn("Localization branch plan", navigation_brief)
        self.assertIn("research_log_index.sqlite", navigation_brief)
        self.assertIn("Use `query_memory`", navigation_brief)
        self.assertNotIn("UNIQUE_TRAILING_SENTINEL_FOR_FULL_CONTENT", navigation_brief)

    def test_research_turn_archival_writes_simplified_research_log_and_type_views(self):
        workflow = self.app.agent.research_workflow
        provider = ArchiveOnlyProvider(
            {
                "records": [
                    {
                        "type": "problem",
                        "title": "Localized Anderson problem",
                        "content": "Study the localized Anderson criterion after tightening the hypotheses.",
                    },
                    {
                        "type": "failed_path",
                        "title": "Global shortcut fails",
                        "content": "The global shortcut fails because the local obstruction remains unresolved.",
                    },
                ]
            }
        )
        workflow.provider = provider

        payload = workflow.archive_after_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            user_message="Continue the localization branch.",
            assistant_message="We tightened the problem and found that the global shortcut fails.",
        )

        records = read_jsonl(self.app.paths.project_research_log_file("anderson_conjecture"))
        self.assertEqual(payload["archived"], 2)
        self.assertEqual(len(records), 2)
        self.assertEqual({item["type"] for item in records}, {"problem", "failed_path"})
        self.assertIn(
            "Localized Anderson problem",
            self.app.paths.project_research_log_markdown_file("anderson_conjecture").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "Global shortcut fails",
            self.app.paths.project_research_log_type_file("anderson_conjecture", "failed_path").read_text(encoding="utf-8"),
        )
        self.assertTrue(self.app.paths.project_research_log_index_file("anderson_conjecture").exists())
        archive_rounds = [
            item
            for item in self.app.session_store.get_provider_rounds(self.state.session_id)
            if item.get("phase") == "archive"
        ]
        self.assertTrue(archive_rounds)
        self.assertEqual(provider.calls[0]["schema_name"], "research_turn_archive")
        archive_system_prompt = provider.calls[0]["system_prompt"]
        archive_user_prompt = provider.calls[0]["messages"][0]["content"]
        self.assertIn("substantive research progress report", archive_user_prompt)
        self.assertIn("self-contained mini research report", archive_user_prompt)
        self.assertIn("Source refs are for auditing and recovery, not a substitute for content", archive_user_prompt)
        self.assertIn("clear, specific, and reusable form", archive_system_prompt)
        self.assertIn("exact statements, formulas, parameters, proof sketches", archive_system_prompt)
        self.assertIn("as applicable", archive_user_prompt)
        self.assertIn("when present", archive_user_prompt)
        for record_type in [
            "problem",
            "verified_conclusion",
            "verification",
            "final_result",
            "counterexample",
            "failed_path",
            "research_note",
        ]:
            self.assertIn(record_type, archive_system_prompt)
            self.assertIn(record_type, archive_user_prompt)

    def test_research_turn_archival_includes_assistant_reasoning_content(self):
        workflow = self.app.agent.research_workflow
        provider = ArchiveOnlyProvider({"records": []})
        workflow.provider = provider

        workflow.archive_after_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            user_message="Continue the FTC reduction.",
            assistant_message="I will analyze the matrix structure.",
            turn_context=[
                {"kind": "user_input", "content": "Continue the FTC reduction."},
                {
                    "kind": "assistant_output",
                    "content": "I will analyze the matrix structure.",
                    "reasoning_content": "Reasoning sentinel: reduce the FTC integral using M first.",
                    "model_round": 1,
                },
                {
                    "kind": "assistant_tool_calls",
                    "content": "Now checking memory.",
                    "reasoning_content": "Tool-call reasoning sentinel: retrieve prior FTC lemmas.",
                    "tool_calls": [
                        {
                            "call_id": "call-1",
                            "name": "query_memory",
                            "arguments": {"query": "FTC reduction"},
                            "status": "execute",
                        }
                    ],
                },
            ],
        )

        archive_user_prompt = provider.calls[0]["messages"][0]["content"]
        self.assertRegex(
            archive_user_prompt,
            r'"reasoning_content": "Reasoning sentinel: reduce the FTC integral using M first\.",\s+"content": "I will analyze the matrix structure\."',
        )
        self.assertIn("Reasoning sentinel: reduce the FTC integral using M first.", archive_user_prompt)
        self.assertRegex(
            archive_user_prompt,
            r'"reasoning_content": "Tool-call reasoning sentinel: retrieve prior FTC lemmas\.",\s+"content": "Now checking memory\."',
        )
        self.assertIn('"tool_calls"', archive_user_prompt)
        self.assertIn("Tool-call reasoning sentinel: retrieve prior FTC lemmas.", archive_user_prompt)

    def test_update_after_turn_no_longer_writes_legacy_research_channels(self):
        workflow = self.app.agent.research_workflow
        provider = ArchiveOnlyProvider(
            {
                "records": [
                    {
                        "type": "research_note",
                        "title": "Channel-free note",
                        "content": "This turn should be archived in research_log only.",
                    }
                ]
            }
        )
        workflow.provider = provider

        workflow.archive_after_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            user_message="Continue.",
            assistant_message=(
                "## Failed Path\n\n"
                "A deliberately old-style failed path section.\n\n"
                "## Scratchpad\n\n"
                "A workspace scratchpad update."
            ),
        )

        self.assertEqual(
            read_jsonl(self.app.paths.project_research_channel_file("anderson_conjecture", "failed_paths")),
            [],
        )
        self.assertEqual(
            read_jsonl(self.app.paths.project_research_channel_file("anderson_conjecture", "events")),
            [],
        )
        research_records = read_jsonl(self.app.paths.project_research_log_file("anderson_conjecture"))
        self.assertEqual(len(research_records), 1)
        self.assertEqual(research_records[0]["type"], "research_note")

    def test_query_memory_research_mode_returns_research_log_content_directly(self):
        self.app.agent.context_manager.research_log.append_records(
            "anderson_conjecture",
            [
                {
                    "type": "failed_path",
                    "title": "Localization shortcut obstruction",
                    "content": "UNIQUE_RESEARCH_LOG_RETRIEVAL_SENTINEL: the shortcut fails at the localization step.",
                    "session_id": self.state.session_id,
                    "source_refs": ["sessions/%s/messages.jsonl" % self.state.session_id],
                }
            ],
        )

        result = self.app.agent.context_manager.query_memory(
            query="localization shortcut sentinel",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            channels=["failed_path"],
            prefer_raw=True,
        )

        self.assertTrue(result["research_log_hits"])
        self.assertIn("UNIQUE_RESEARCH_LOG_RETRIEVAL_SENTINEL", result["summary"])
        self.assertEqual(result["research_log_hits"][0]["type"], "failed_path")

    def test_query_memory_session_windows_include_reasoning_content(self):
        self.app.session_store.append_message(
            self.state.session_id,
            "assistant",
            "Visible answer without the query-only reasoning token.",
            metadata={"reasoning_content": "QUERY_MEMORY_REASONING_SENTINEL appears only in reasoning metadata."},
        )

        result = self.app.agent.context_manager.query_memory(
            query="QUERY_MEMORY_REASONING_SENTINEL",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            prefer_raw=True,
        )

        self.assertTrue(result["session_hits"] or result["event_hits"])
        rendered = "\n".join(str(item.get("text") or "") for item in result["sources"])
        self.assertRegex(
            rendered,
            r'"reasoning_content": "QUERY_MEMORY_REASONING_SENTINEL appears only in reasoning metadata\.",\s+"content": "Visible answer without the query-only reasoning token\."',
        )
        self.assertIn("QUERY_MEMORY_REASONING_SENTINEL", rendered)

    def test_query_memory_visible_tool_output_deduplicates_equivalent_content(self):
        repeated = "UNIQUE_VISIBLE_QUERY_MEMORY_SENTINEL: " + " ".join(["same-content"] * 20)
        hit = {
            "id": "record-1",
            "key": "research-log:record-1",
            "source": "research-log",
            "type": "failed_path",
            "title": "Repeated research hit",
            "content": repeated,
            "content_inline": "UNIQUE_VISIBLE_QUERY_MEMORY_SENTINEL",
            "metadata": {
                "raw_text": repeated,
                "exact_excerpt": "UNIQUE_VISIBLE_QUERY_MEMORY_SENTINEL",
                "source_path": "projects/anderson_conjecture/memory/research_log.jsonl",
            },
        }
        output = {
            "query": "visible query memory sentinel",
            "project_scope": "anderson_conjecture",
            "summary": "[failed_path] Repeated research hit\nUNIQUE_VISIBLE_QUERY_MEMORY_SENTINEL",
            "compressed_windows": [
                {
                    "key": "research-log:record-1",
                    "source": "research-log",
                    "title": "Repeated research hit",
                    "summary": "UNIQUE_VISIBLE_QUERY_MEMORY_SENTINEL",
                    "window_excerpt": "UNIQUE_VISIBLE_QUERY_MEMORY_SENTINEL",
                }
            ],
            "sources": [hit],
            "research_log_hits": [hit],
            "research_hits": [hit],
        }

        visible = self.app.agent._visible_tool_output(
            {
                "name": "query_memory",
                "call_id": "call-1",
                "arguments": {"query": "visible query memory sentinel"},
                "output": output,
                "error": None,
            }
        )
        rendered = json.dumps(visible, ensure_ascii=False)

        self.assertIn("sources", visible)
        self.assertNotIn("research_log_hits", visible)
        self.assertNotIn("research_hits", visible)
        self.assertNotIn("raw_text", rendered)
        self.assertNotIn("exact_excerpt", rendered)
        self.assertNotIn("window_excerpt", rendered)
        self.assertEqual(rendered.count(repeated), 1)

    def test_query_memory_visible_tool_output_hard_caps_local_context_by_score(self):
        import moonshine.run_agent as run_agent_module

        previous_budget = run_agent_module.QUERY_MEMORY_VISIBLE_TOKEN_BUDGET
        run_agent_module.QUERY_MEMORY_VISIBLE_TOKEN_BUDGET = 220
        try:
            high_content = "prefix " + " ".join(["HIGH_SCORE_CONTEXT"] * 500) + " central theorem " + " ".join(["tail"] * 500)
            low_content = "LOW_SCORE_CONTEXT " + " ".join(["low"] * 800)
            output = {
                "query": "central theorem",
                "project_scope": "anderson_conjecture",
                "summary": "Need the central theorem local context.",
                "sources": [
                    {
                        "id": "low",
                        "key": "research-log:low",
                        "source": "research-log",
                        "title": "Low score hit",
                        "content": low_content,
                        "score": 0.1,
                    },
                    {
                        "id": "high",
                        "key": "research-log:high",
                        "source": "research-log",
                        "title": "High score hit",
                        "content": high_content,
                        "content_inline": "central theorem",
                        "score": 9.0,
                    },
                ],
            }

            visible = self.app.agent._visible_tool_output(
                {
                    "name": "query_memory",
                    "call_id": "call-1",
                    "arguments": {"query": "central theorem"},
                    "output": output,
                    "error": None,
                }
            )
        finally:
            run_agent_module.QUERY_MEMORY_VISIBLE_TOKEN_BUDGET = previous_budget

        rendered = json.dumps(visible, ensure_ascii=False)
        self.assertLessEqual(self.app.agent._estimate_query_memory_visible_tokens(visible), 220)
        self.assertIn("High score hit", rendered)
        self.assertIn("central theorem", rendered)
        self.assertIn("[truncated", rendered)
        self.assertNotIn("LOW_SCORE_CONTEXT", rendered)

    def test_query_memory_research_mode_accepts_types_as_preferred_filter(self):
        self.app.agent.context_manager.research_log.append_records(
            "anderson_conjecture",
            [
                {
                    "type": "failed_path",
                    "title": "Localization shortcut fails",
                    "content": "UNIQUE_RESEARCH_LOG_TYPES_SENTINEL: the shortcut fails at the localization step.",
                    "session_id": self.state.session_id,
                    "source_refs": ["sessions/%s/messages.jsonl" % self.state.session_id],
                }
            ],
        )

        result = self.app.agent.context_manager.query_memory(
            query="localization shortcut types sentinel",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            types=["failed_path"],
            prefer_raw=True,
        )

        self.assertTrue(result["research_log_hits"])
        self.assertIn("UNIQUE_RESEARCH_LOG_TYPES_SENTINEL", result["summary"])
        self.assertEqual(result["research_log_hits"][0]["type"], "failed_path")
        self.assertEqual(result["types"], ["failed_path"])

    def test_verified_conclusion_research_log_syncs_to_global_knowledge(self):
        self.app.agent.context_manager.research_log.append_records(
            "anderson_conjecture",
            [
                {
                    "type": "verified_conclusion",
                    "title": "Verified localization lemma",
                    "content": "UNIQUE_VERIFIED_RESEARCH_LOG_KNOWLEDGE: the localized criterion holds under the scripted hypotheses.",
                    "session_id": self.state.session_id,
                    "source_refs": ["sessions/%s/messages.jsonl" % self.state.session_id],
                }
            ],
        )

        hits = self.app.agent.memory_manager.knowledge_store.search(
            "UNIQUE_VERIFIED_RESEARCH_LOG_KNOWLEDGE",
            project_slug="anderson_conjecture",
            limit=5,
        )

        self.assertTrue(hits)
        self.assertIn("UNIQUE_VERIFIED_RESEARCH_LOG_KNOWLEDGE", hits[0]["statement"])

    def test_workspace_file_excerpt_never_hard_truncates_formal_draft(self):
        workflow = self.app.agent.research_workflow
        draft_text = (
            "# Current Problem Draft\n\n"
            "## Statement\n"
            "A long scripted problem statement that should remain intact in automatic loading.\n\n"
            "## Notes\n"
            "UNIQUE_FORMAL_DRAFT_SENTINEL"
        )
        atomic_write(
            self.app.paths.project_problem_draft_file("anderson_conjecture"),
            draft_text,
        )

        full_excerpt = workflow._workspace_file_excerpt(
            "anderson_conjecture",
            "problem",
            token_budget=4000,
        )
        limited_excerpt = workflow._workspace_file_excerpt(
            "anderson_conjecture",
            "problem",
            token_budget=8,
        )

        self.assertEqual(full_excerpt, draft_text)
        self.assertIn("do not inject a truncated excerpt", limited_excerpt)
        self.assertNotIn("UNIQUE_FORMAL_DRAFT_SENTINEL...", limited_excerpt)

    def test_research_autopilot_iterates_until_verified_completion(self):
        active_problem = "Solve the scripted autonomous research problem."
        blueprint_text = (
            "# theorem scripted-autopilot\n\n"
            "## statement\n"
            "%s\n\n"
            "## proof\n"
            "The scripted autonomous blueprint closes the argument by reducing to a verified local branch and integrating the remaining lemmas."
        ) % active_problem
        provider = ResearchWorkflowProvider(
            [
                {
                    "response": ProviderResponse(
                        content="",
                        tool_calls=[
                            ProviderToolCall(
                                name="query_memory",
                                arguments={"query": "autopilot scripted research problem", "project_slug": "anderson_conjecture"},
                                call_id="call-auto-memory-1",
                            ),
                        ],
                    ),
                },
                {
                    "chunks": ["The first autonomous turn selected a concrete problem."],
                    "response": ProviderResponse(
                        content=(
                            "The first autonomous turn selected a concrete problem.\n\n"
                            "## Candidate Problem\n"
                            "%s\n\n"
                            "## Stage Transition\n"
                            "Enter problem_solving. The active problem is now approved for decomposition and proof work.\n\n"
                            "Accepted proof text:\n"
                            "%s\n"
                        )
                        % (active_problem, blueprint_text),
                    ),
                },
                {
                    "response": ProviderResponse(
                        content="",
                        tool_calls=[
                            ProviderToolCall(
                                name="assess_problem_quality",
                                arguments={
                                    "problem": active_problem,
                                    "context": "Scripted autonomous problem selected during problem design.",
                                    "project_slug": "anderson_conjecture",
                                },
                                call_id="call-auto-quality-1",
                            ),
                        ],
                    ),
                },
                {
                    "chunks": ["The autonomous problem passed quality review and moved to solving."],
                    "response": ProviderResponse(
                        content=(
                            "The autonomous problem passed quality review.\n\n"
                            "## Stage Transition\n"
                            "Enter problem_solving. The active problem has passed the dedicated quality-assessor review.\n\n"
                            "Accepted proof text:\n"
                            "%s\n"
                        )
                        % blueprint_text,
                    ),
                },
                {
                    "response": ProviderResponse(
                        content="",
                        tool_calls=[
                            ProviderToolCall(
                                name="verify_overall",
                                arguments={
                                    "claim": active_problem,
                                    "proof": blueprint_text,
                                    "project_slug": "anderson_conjecture",
                                    "scope": "final",
                                    "blueprint_path": "projects/anderson_conjecture/workspace/blueprint.md",
                                },
                                call_id="call-auto-verify-1",
                            ),
                        ],
                    ),
                },
                {
                    "chunks": ["The second autonomous turn verified the blueprint."],
                    "response": ProviderResponse(content="The second autonomous turn verified the blueprint."),
                },
            ],
            [
                json.dumps(
                    {
                        "reviewer_id": "quality-assessor",
                        "review_status": "passed",
                        "impact": 0.80,
                        "feasibility": 0.80,
                        "novelty": 0.70,
                        "richness": 0.70,
                        "overall": 0.76,
                        "strengths": ["Autopilot test problem is ready for solving."],
                        "weaknesses": [],
                        "required_refinements": [],
                        "rationale": "Autopilot test problem is ready for solving.",
                        "confidence": 0.88,
                    }
                ),
                json.dumps(self._dimension_review(reviewer_id="assumption-usage-reviewer", dimension="assumption", verdict="correct")),
                json.dumps(self._dimension_review(reviewer_id="calculation-consistency-reviewer", dimension="computation", verdict="correct")),
                json.dumps(self._dimension_review(reviewer_id="logical-chain-reviewer", dimension="logic", verdict="correct")),
            ],
            archive_responses=[
                {"records": []},
                {
                    "records": [
                        {
                            "type": "problem",
                            "title": "Selected autonomous problem",
                            "content": active_problem,
                        },
                        {
                            "type": "research_note",
                            "title": "Accepted autonomous proof text",
                            "content": blueprint_text,
                        },
                    ]
                },
                {
                    "records": [
                        {
                            "type": "verification",
                            "title": "Autonomous final verification passed",
                            "content": "verify_overall(scope=\"final\") passed for the scripted autonomous proof.",
                        },
                        {
                            "type": "final_result",
                            "title": "Autonomous final result",
                            "content": "The scripted autonomous blueprint closes the argument by reducing to a verified local branch.",
                        },
                    ]
                },
            ],
        )
        self.app.agent.provider = provider

        events = list(
            self.app.run_research_autopilot_events(
                "Autonomously solve this research problem until verified.",
                self.state,
                max_iterations=3,
            )
        )
        status_texts = [event.text for event in events if event.type == "status"]

        self.assertEqual(len(provider.calls), 6)
        self.assertIn("Research autopilot iteration 1/3.", status_texts)
        self.assertIn("Research autopilot iteration 2/3.", status_texts)
        self.assertIn("Research autopilot iteration 3/3.", status_texts)
        self.assertIn("Research autopilot completed after final verification passed.", status_texts)
        self.assertNotIn("Research autopilot stopped after reaching the iteration budget.", status_texts)
        self.assertIn(
            "Continue focusing on the current research progress",
            provider.calls[4]["messages"][-1]["content"],
        )
        self.assertIn("multi-turn conversation", provider.calls[4]["messages"][-1]["content"])
        self.assertIn("verify_overall", provider.calls[4]["messages"][-1]["content"])
        self.assertNotIn("research_workflow.json", provider.calls[4]["messages"][-1]["content"])
        self.assertNotIn("research_state.json", provider.calls[4]["messages"][-1]["content"])
        self.assertIn(
            "Autonomous final verification passed",
            self.app.paths.project_blueprint_verified_file("anderson_conjecture").read_text(encoding="utf-8"),
        )

    def test_research_verify_overall_without_final_scope_stays_non_final(self):
        active_problem = "Canonical blueprint verification remains non-final without explicit final scope."
        blueprint_text = (
            "# theorem scripted-scope-default\n\n"
            "## statement\n"
            "%s\n\n"
            "## proof\n"
            "This blueprint is audited through the canonical workspace path, but it should stay non-final unless the model explicitly passes scope final."
        ) % active_problem
        provider = ResearchWorkflowProvider(
            [
                {
                    "response": ProviderResponse(
                        content="",
                        tool_calls=[
                            ProviderToolCall(
                                name="verify_overall",
                                arguments={
                                    "claim": active_problem,
                                    "proof": blueprint_text,
                                    "project_slug": "anderson_conjecture",
                                    "blueprint_path": "projects/anderson_conjecture/workspace/blueprint.md",
                                },
                                call_id="call-scope-default-1",
                            ),
                        ],
                    ),
                },
                {
                    "chunks": ["The canonical blueprint verification passed at non-final scope."],
                    "response": ProviderResponse(content="The canonical blueprint verification passed at non-final scope."),
                },
            ],
            [
                json.dumps(self._dimension_review(reviewer_id="assumption-usage-reviewer", dimension="assumption", verdict="correct")),
                json.dumps(self._dimension_review(reviewer_id="calculation-consistency-reviewer", dimension="computation", verdict="correct")),
                json.dumps(self._dimension_review(reviewer_id="logical-chain-reviewer", dimension="logic", verdict="correct")),
            ],
        )
        self.app.agent.provider = provider

        events = list(self.app.ask_stream("Verify the canonical blueprint.", self.state))
        tool_results = [event for event in events if event.type == "tool_result" and event.text == "verify_overall"]

        self.assertTrue(tool_results)
        self.assertNotIn("scope", tool_results[-1].payload["arguments"])
        self.assertEqual(tool_results[-1].payload["output"]["scope"], "intermediate")

    def test_research_autopilot_continues_after_plain_stage_transition_without_workflow_update_gate(self):
        first_turn = (
            "## Stage Transition\n"
            "No stage change: still in problem_solving / proof_integration, but on a repaired branch:\n"
            "- new branch: `value-distribution-repair-v1`\n"
            "- corrected claim: the global zero-count bound is false as stated\n"
            "- next move: prove and verify the depth-1 theorem cleanly.\n\n"
            "Next I should formalize the exact depth-1 theorem and run verification on that repaired claim."
        )
        provider = ScriptedProvider(
            [
                {
                    "chunks": [first_turn],
                    "response": ProviderResponse(content=first_turn),
                },
                {
                    "chunks": ["The second turn continues the repaired proof branch."],
                    "response": ProviderResponse(content="The second turn continues the repaired proof branch."),
                },
            ]
        )
        self.app.agent.provider = provider

        events = list(
            self.app.run_research_autopilot_events(
                "Continue the value-distribution repair.",
                self.state,
                max_iterations=2,
            )
        )
        status_texts = [event.text for event in events if event.type == "status"]

        self.assertEqual(len(provider.calls), 2)
        self.assertIn("Research autopilot iteration 2/2.", status_texts)
        self.assertTrue(any("Preparing next turn context" in text for text in status_texts))
        self.assertTrue(any("Sending model request for round" in text for text in status_texts))
        self.assertTrue(any("Archiving research progress from the completed turn." in text for text in status_texts))
        self.assertNotIn("Research autopilot stopped because no workflow update was produced.", status_texts)
        self.assertIn("Research autopilot stopped after reaching the iteration budget.", status_texts)
        self.assertIn("Continue focusing on the current research progress", provider.calls[1]["messages"][-1]["content"])

    def test_research_skills_are_packaged_and_loadable(self):
        expected = [
            "literature-survey",
            "problem-generator",
            "quality-assessor",
            "propose-subgoal-decomposition",
            "direct-proving",
            "construct-counterexamples",
            "pessimistic-verifier",
            "proof-corrector",
            "research-consolidation",
            "lemma-prover",
        ]

        for slug in expected:
            skill = self.app.skill_manager.get_skill(slug)
            self.assertIsNotNone(skill, slug)
            self.assertIn("Execution Steps", skill.body)

    def test_init_dependency_install_failure_reports_required_libraries(self):
        fake_result = DependencyInstallResult(
            command=["python", "-m", "pip", "install", "-e", "moonshine[all]"],
            exit_code=1,
            output="Could not fetch package index",
            dependencies=list(REQUIRED_RUNTIME_DEPENDENCIES),
            missing_before=list(REQUIRED_RUNTIME_DEPENDENCIES),
        )
        stdout = io.StringIO()
        with mock.patch("moonshine.moonshine_cli.main.install_runtime_dependencies", return_value=fake_result):
            with mock.patch("sys.stdout", stdout):
                exit_code = cli_main(["--home", self.temp_dir.name, "init", "--install-deps"])

        output = stdout.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("Dependency installation failed", output)
        self.assertIn("tiktoken>=0.7.0", output)
        self.assertIn("lancedb>=0.14.0", output)
        self.assertIn("chromadb>=0.5.0", output)
        self.assertIn("langgraph>=0.2.0", output)
        self.assertIn("Could not fetch package index", output)

    def test_ask_stream_emits_incremental_events(self):
        events = list(
            self.app.ask_stream(
                "Remember: prioritize integral extensions after checking Krull dimension.",
                self.state,
            )
        )

        event_types = [event.type for event in events]
        streamed_text = "".join(event.text for event in events if event.type == "text_delta")
        final_events = [event for event in events if event.type == "final"]

        self.assertIn("status", event_types)
        self.assertIn("text_delta", event_types)
        self.assertEqual(len(final_events), 1)
        self.assertIn("integral extensions", streamed_text)

    def test_run_agent_recovers_from_invalid_tool_batch(self):
        self.app.agent.provider = ScriptedProvider(
            [
                {
                    "response": ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                name="totally_unknown_tool",
                                arguments={"title": "Current branch", "summary": "Prefer Krull dimension first."},
                                call_id="call-1",
                            )
                        ]
                    )
                },
                {
                    "response": ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                name="query_memory",
                                arguments={"query": "Krull dimension first", "project_slug": "anderson_conjecture"},
                                call_id="call-2",
                            )
                        ]
                    )
                },
                {
                    "chunks": ["Stored it for later turns."],
                    "response": ProviderResponse(content="Stored it for later turns."),
                },
            ]
        )

        events = list(self.app.ask_stream("Remember Krull dimension first.", self.state))
        event_types = [event.type for event in events]

        self.assertIn("tool_error", event_types)
        self.assertIn("tool_result", event_types)
        self.assertEqual(events[-1].text, "Stored it for later turns.")

    def test_run_agent_retries_when_tool_arguments_violate_schema(self):
        self.app.agent.provider = ScriptedProvider(
            [
                {
                    "response": ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                name="query_memory",
                                arguments={},
                                call_id="call-1",
                            )
                        ]
                    )
                },
                {
                    "response": ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                name="query_memory",
                                arguments={"query": "Krull dimension first", "project_slug": "anderson_conjecture"},
                                call_id="call-2",
                            )
                        ]
                    )
                },
                {
                    "chunks": ["Stored it for later turns."],
                    "response": ProviderResponse(content="Stored it for later turns."),
                },
            ]
        )

        events = list(self.app.ask_stream("Remember Krull dimension first.", self.state))
        tool_errors = [event for event in events if event.type == "tool_error"]

        self.assertTrue(tool_errors)
        self.assertTrue(any("JSON schema" in event.payload.get("error", "") for event in tool_errors))
        self.assertEqual(events[-1].text, "Stored it for later turns.")

    def test_run_agent_nudges_after_empty_response_following_tools(self):
        provider = ScriptedProvider(
            [
                {
                    "chunks": ["I will save that preference."],
                    "response": ProviderResponse(
                        content="I will save that preference.",
                        tool_calls=[
                            ProviderToolCall(
                                name="query_memory",
                                arguments={"query": "local methods", "project_slug": "anderson_conjecture"},
                                call_id="call-1",
                            )
                        ],
                    ),
                },
                {"response": ProviderResponse(content="")},
                {
                    "chunks": ["Stored it and I will reuse it in future turns."],
                    "response": ProviderResponse(content="Stored it and I will reuse it in future turns."),
                },
            ]
        )
        self.app.agent.provider = provider

        events = list(self.app.ask_stream("Remember: prefer local methods.", self.state))
        status_texts = [event.text for event in events if event.type == "status"]

        self.assertTrue(any("nudging the model" in text for text in status_texts))
        self.assertEqual(events[-1].text, "Stored it and I will reuse it in future turns.")
        self.assertEqual(provider.calls[2]["messages"][-1]["role"], "user")

    def test_turn_events_file_records_runtime_decisions(self):
        list(self.app.ask_stream("Remember: prefer finite generation arguments.", self.state))

        turn_events_path = self.app.paths.session_turn_events_file(self.state.session_id)
        self.assertTrue(turn_events_path.exists())
        entries = [
            json.loads(line)
            for line in turn_events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        entry_types = [item["type"] for item in entries]

        self.assertIn("turn_started", entry_types)
        self.assertIn("status", entry_types)
        self.assertIn("turn_completed", entry_types)

    def test_provider_rounds_use_gzip_archive_without_live_markdown_trace(self):
        list(self.app.ask_stream("Remember: prefer finite generation arguments.", self.state))

        rounds_path = self.app.paths.session_provider_rounds_file(self.state.session_id)
        trace_path = self.app.paths.session_provider_trace_file(self.state.session_id)
        self.assertTrue(rounds_path.exists())
        self.assertFalse(trace_path.exists())

        rows = [
            json.loads(line)
            for line in rounds_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(rows)
        self.assertIn("archive_path", rows[0])
        self.assertNotIn("messages", rows[0])
        self.assertNotIn("system_prompt", rows[0])
        archive_path = self.app.paths.home / rows[0]["archive_path"]
        self.assertTrue(archive_path.exists())
        self.assertEqual(archive_path.suffix, ".gz")
        with gzip.open(archive_path, "rt", encoding="utf-8") as handle:
            archived = json.load(handle)
        self.assertIn("messages", archived)
        self.assertIn("response", archived)
        self.assertTrue(any(item["role"] == "user" for item in archived["messages"]))
        loaded_rows = self.app.session_store.get_provider_rounds(self.state.session_id)
        self.assertIn("messages", loaded_rows[0])
        self.assertIn("response", loaded_rows[0])

    def test_memory_entries_capture_session_provenance(self):
        self.app.execute_command("/memory write Preserve local arguments before completion arguments.", self.state)

        entries = self.app.memory.dynamic_store.search("local arguments", limit=1)
        self.assertTrue(entries)
        self.assertEqual(entries[0].source_session_id, self.state.session_id)
        self.assertEqual(entries[0].source_message_role, "user")
        self.assertIn("local arguments", entries[0].source_excerpt)

    def test_dynamic_memory_files_use_structured_metadata_comments(self):
        self.app.execute_command("/memory write Preserve local arguments before completion arguments.", self.state)

        explicit_text = self.app.memory.dynamic_store.read_file("feedback-explicit")
        self.assertIn("<!--", explicit_text)
        self.assertIn('"title": "Explicit Memory Request"', explicit_text)
        self.assertIn('"source_session_id": "%s"' % self.state.session_id, explicit_text)

    def test_dynamic_memory_update_preserves_backslashes_in_replacement_text(self):
        first = self.app.memory.dynamic_store.make_entry(
            alias="project-progress",
            slug="latex-progress-update",
            title="LaTeX Progress Update",
            summary="Initial progress.",
            body="Initial branch note.",
            source="test",
            project_slug="anderson_conjecture",
        )
        self.app.memory.dynamic_store.write_entry(first)
        second = self.app.memory.dynamic_store.make_entry(
            alias="project-progress",
            slug="latex-progress-update",
            title="LaTeX Progress Update",
            summary="Updated progress with \\sqrt{2}.",
            body="The replacement body contains \\sqrt{2} and must not be treated as a regex template.",
            source="test",
            project_slug="anderson_conjecture",
        )
        self.app.memory.dynamic_store.write_entry(second)

        progress_text = self.app.memory.dynamic_store.read_file("project-progress", project_slug="anderson_conjecture")
        self.assertIn("\\sqrt{2}", progress_text)

    def test_record_failed_path_accepts_latex_backslashes(self):
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )

        result = self.app.tool_manager.dispatch(
            "record_failed_path",
            {
                "title": "Exact scalar branch fails",
                "summary": "The coarse box route fails; continue with a = 6^{\\sqrt{2}}.",
                "content": "The failed path records the exact comparison using \\sqrt{2} without regex escaping errors.",
                "next_action": "Use exact scalar comparisons involving \\sqrt{2}.",
                "review_status": "not_applicable",
            },
            runtime,
        )

        self.assertEqual(result["artifact_type"], "failed_path")
        records = read_jsonl(self.app.paths.project_research_log_file("anderson_conjecture"))
        self.assertTrue(any("6^{\\sqrt{2}}" in item.get("content", "") for item in records))

    def test_tool_registry_and_skill_store_load_markdown_definitions(self):
        tool_schema = {
            item["name"]: item
            for item in self.app.tool_registry.schemas()
        }
        skill = self.app.skill_store.get_skill("memory-hygiene")

        self.assertIn("record_solve_attempt", tool_schema)
        self.assertIn("manage_skill", tool_schema)
        self.assertIn("pessimistic_verify", tool_schema)
        self.assertIn("verify_overall", tool_schema)
        self.assertIn("verify_correctness_assumption", tool_schema)
        self.assertIn("verify_correctness_computation", tool_schema)
        self.assertIn("verify_correctness_logic", tool_schema)
        self.assertIn("query_session_records", tool_schema)
        self.assertIn("review_count", tool_schema["verify_overall"]["parameters"]["properties"])
        self.assertIn("review_count", tool_schema["verify_correctness_logic"]["parameters"]["properties"])
        self.assertIn("solve attempt", tool_schema["record_solve_attempt"]["description"].lower())
        self.assertIn("raw records", tool_schema["query_session_records"]["description"].lower())
        self.assertIsNotNone(skill)
        self.assertIn("dynamic memory concise", skill.body)
        self.assertIsNotNone(self.app.skill_manager.get_skill("conclusion-manage"))
        self.assertIsNotNone(self.app.skill_manager.get_skill("query-memory"))
        self.assertIsNotNone(self.app.skill_manager.get_skill("verify-overall"))

    def test_manage_skill_schema_declares_items_for_array_capable_fields(self):
        tool_schema = {
            item["name"]: item
            for item in self.app.tool_registry.schemas()
        }

        parameters = tool_schema["manage_skill"]["parameters"]["properties"]
        array_capable_fields = [
            "summary",
            "execution_steps",
            "tool_calls",
            "file_references",
            "allowed_tools",
            "purpose",
            "workflow",
            "checklist",
            "when_to_use",
            "inputs",
            "output_contract",
            "examples",
            "notes",
            "tags",
        ]

        for field_name in array_capable_fields:
            self.assertEqual(parameters[field_name]["items"], {"type": "string"})

    def test_chat_mode_hides_research_recording_tools_from_model_facing_tools(self):
        chat_schemas = {item["name"] for item in self.app.tool_manager.schemas(mode="chat")}
        research_schemas = {item["name"] for item in self.app.tool_manager.schemas(mode="research")}
        research_tool_schema = {item["name"]: item for item in self.app.tool_manager.schemas(mode="research")}
        chat_index = self.app.tool_manager.build_prompt_index(limit=128, mode="chat")
        research_index = self.app.tool_manager.build_prompt_index(limit=128, mode="research")
        skill_index = self.app.skill_manager.build_prompt_index(limit=128)

        self.assertNotIn("record_solve_attempt", chat_schemas)
        self.assertNotIn("record_solve_attempt", research_schemas)
        self.assertNotIn("record_solve_attempt", chat_index)
        self.assertNotIn("record_solve_attempt", research_index)
        self.assertIn("Available tools (short descriptions and usage guidance", research_index)
        self.assertIn("Usage:", research_index)
        self.assertIn("Use it when", research_index)
        self.assertIn("query_session_records", research_index)
        self.assertIn("Available skills (short descriptions and usage guidance", skill_index)
        self.assertIn("Usage:", skill_index)
        self.assertIn("Use it when", skill_index)
        self.assertNotIn("record_failed_path", chat_schemas)
        self.assertNotIn("record_failed_path", research_schemas)
        self.assertNotIn("commit_turn", chat_schemas)
        self.assertNotIn("commit_turn", research_schemas)
        self.assertNotIn("store_conclusion", research_schemas)
        self.assertNotIn("add_knowledge", research_schemas)
        self.assertNotIn("store_conclusion", research_index)
        self.assertNotIn("add_knowledge", research_index)
        self.assertNotIn("manage_skill", research_schemas)
        query_memory_schema = research_tool_schema["query_memory"]["parameters"]["properties"]
        self.assertIn("types", query_memory_schema)
        self.assertIn("channels", query_memory_schema)
        self.assertIn("failed_path", query_memory_schema["types"]["items"]["enum"])
        self.assertIn("counterexample", query_memory_schema["types"]["description"])
        self.assertIn("Legacy alias", query_memory_schema["channels"]["description"])
        self.assertNotIn("manage_skill", research_index)

    def test_prompt_indexes_include_complete_usage_hint_sections(self):
        custom_skill = self.app.paths.installed_skills_dir / "multi-hint-skill" / "SKILL.md"
        atomic_write(
            custom_skill,
            """---
name: multi-hint-skill
description: Skill with several usage hint lines.
compatibility: Works in tests.
metadata:
  title: Multi Hint Skill
  category: installed
---

# Multi Hint Skill

## Usage Hint
- Use this skill to test full hint loading.
- Use it when line two should remain visible.
- Keep line three visible as well.

## Summary
- Test skill.

## Execution Steps
1. Test.

## Tool Calls
- `query_memory`: Test.

## File References
- `projects/<project_slug>/memory/research_log.jsonl`

## Output Contract
- Test.
""",
        )

        custom_tool = self.app.paths.tool_definitions_dir / "multi_hint_tool.md"
        atomic_write(
            custom_tool,
            """<!--
{
  "name": "multi_hint_tool",
  "handler": "memory_overview",
  "description": "Tool with several usage hint lines.",
  "parameters": {
    "type": "object",
    "additionalProperties": false,
    "properties": {}
  }
}
-->

# Tool: multi_hint_tool

## Usage Hint
- Use this tool to test full hint loading.
- Use it when line two should remain visible.
- Keep line three visible as well.
""",
        )

        reloaded = MoonshineApp(home=self.temp_dir.name)
        skill_index = reloaded.skill_manager.build_prompt_index(limit=128, include=["multi-hint-skill"])
        tool_index = reloaded.tool_manager.build_prompt_index(limit=128, include=["multi_hint_tool"])

        self.assertIn("Use this skill to test full hint loading.", skill_index)
        self.assertIn("Use it when line two should remain visible.", skill_index)
        self.assertIn("Keep line three visible as well.", skill_index)
        self.assertIn("Use this tool to test full hint loading.", tool_index)
        self.assertIn("Use it when line two should remain visible.", tool_index)
        self.assertIn("Keep line three visible as well.", tool_index)

    def test_structured_research_recording_tools_persist_expected_artifact_types(self):
        workflow = self.app.agent.research_workflow
        state = workflow.load_state("anderson_conjecture")
        state.stage = "problem_design"
        state.node = "problem_decomposition"
        state.selected_skills = ["propose-subgoal-decomposition"]
        workflow.save_state(state, mirror_progress=False, checkpoint_reason="test_seed_planning_skill")

        workflow.refresh_after_turn(
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
            user_message="Propose a decomposition plan.",
            assistant_message=(
                "Plan A: reduce the theorem to maximal-ideal checks, then isolate the finiteness lemma, then reassemble the argument."
            ),
        )

        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )

        self.app.tool_manager.dispatch(
            "record_failed_path",
            {
                "title": "Naive completion route fails",
                "summary": "Completion destroys the control needed for the finiteness estimate.",
                "content": "The completed ring argument introduces uncontrollable extra components.",
            },
            runtime,
        )
        self.app.tool_manager.dispatch(
            "record_solve_attempt",
            {
                "title": "Localization branch",
                "summary": "Localization branch remains active after screening the completion route.",
                "next_action": "Push the localization branch through the finiteness lemma.",
            },
            runtime,
        )

        research_log_rows = read_jsonl(self.app.paths.project_research_log_file("anderson_conjecture"))
        channels = {
            "subgoals": read_jsonl(self.app.paths.project_research_channel_file("anderson_conjecture", "subgoals")),
            "failed_paths": read_jsonl(self.app.paths.project_research_channel_file("anderson_conjecture", "failed_paths")),
            "solve_steps": read_jsonl(self.app.paths.project_research_channel_file("anderson_conjecture", "solve_steps")),
        }

        self.assertEqual(channels["subgoals"], [])
        self.assertEqual(channels["failed_paths"], [])
        self.assertEqual(channels["solve_steps"], [])
        self.assertTrue(any(item.get("type") == "failed_path" for item in research_log_rows))
        self.assertTrue(any(item.get("type") == "research_note" for item in research_log_rows))

    def test_pessimistic_verify_passes_only_when_all_reviewers_pass(self):
        provider = PessimisticVerificationProvider(
            [
                json.dumps(self._verifier_review("logic-chain-reviewer")),
                json.dumps(self._verifier_review("theorem-and-assumption-reviewer")),
            ]
        )

        result = self.app.tool_manager.dispatch(
            "pessimistic_verify",
            {
                "claim": "Every maximal localization satisfies the target property.",
                "proof": "A complete proof blueprint with all assumptions stated.",
                "project_slug": "anderson_conjecture",
                "review_count": 2,
            },
            {"provider": provider, "project_slug": "anderson_conjecture"},
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["overall_verdict"], "passed")
        self.assertEqual(len(provider.calls), 2)
        self.assertTrue(all(call["schema_name"] == "pessimistic_verification_review" for call in provider.calls))
        self.assertEqual(result["failed_reviewers"], [])

    def test_pessimistic_verify_fails_if_any_reviewer_objects(self):
        provider = PessimisticVerificationProvider(
            [
                json.dumps(self._verifier_review("logic-chain-reviewer")),
                json.dumps(
                    self._verifier_review(
                        "theorem-and-assumption-reviewer",
                        verdict="wrong",
                        critical_errors=["The cited theorem requires Noetherian hypotheses that were not assumed."],
                        repair_hints=["Add the missing Noetherian assumption or replace the cited theorem."],
                    )
                ),
                json.dumps(self._verifier_review("calculation-and-edge-case-reviewer")),
            ]
        )

        result = self.app.tool_manager.dispatch(
            "pessimistic_verify",
            {
                "claim": "The localization reduction proves the global statement.",
                "proof": "The proof cites a theorem without checking its hypotheses.",
                "review_count": 3,
            },
            {"provider": provider, "project_slug": "anderson_conjecture"},
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["overall_verdict"], "failed")
        self.assertIn("theorem-and-assumption-reviewer", result["failed_reviewers"])
        self.assertIn("Noetherian hypotheses", result["critical_errors"][0])

    def test_pessimistic_verify_fails_conservatively_without_structured_provider(self):
        result = self.app.tool_manager.dispatch(
            "pessimistic_verify",
            {
                "claim": "A candidate lemma should be promoted.",
                "proof": "Only a sketch is available.",
                "review_count": 2,
            },
            {"project_slug": "anderson_conjecture"},
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["overall_verdict"], "failed")
        self.assertEqual(result["review_count"], 2)
        self.assertTrue(all(review["verdict"] == "inconclusive" for review in result["reviews"]))

    def test_verification_input_bounding_uses_total_budget_not_fixed_field_quotas(self):
        from moonshine.tools import verification_tools

        claim = " ".join(["claim"] * 24)
        proof = " ".join(["proof"] * 44)
        context = " ".join(["context"] * 80)

        with mock.patch("moonshine.tools.verification_tools.VERIFICATION_INPUT_TOKEN_BUDGET", 130):
            bounded_claim, bounded_proof, bounded_context = verification_tools._bounded_verification_inputs(
                claim,
                proof,
                context,
            )

        self.assertEqual(bounded_claim, claim)
        self.assertEqual(bounded_proof, proof)
        self.assertLess(moonshine_utils.estimate_token_count(bounded_context), moonshine_utils.estimate_token_count(context))
        self.assertLessEqual(
            moonshine_utils.estimate_token_count(bounded_claim)
            + moonshine_utils.estimate_token_count(bounded_proof)
            + moonshine_utils.estimate_token_count(bounded_context),
            130,
        )

    def test_verify_overall_runs_three_dimensions_and_passes_only_when_all_pass(self):
        provider = PessimisticVerificationProvider(
            [
                json.dumps(self._dimension_review("assumption-usage-reviewer", dimension="assumption", verdict="correct")),
                json.dumps(self._dimension_review("calculation-consistency-reviewer", dimension="computation", verdict="correct")),
                json.dumps(self._dimension_review("logical-chain-reviewer", dimension="logic", verdict="correct")),
            ]
        )

        result = self.app.tool_manager.dispatch(
            "verify_overall",
            {
                "claim": "Every maximal localization satisfies the target property.",
                "proof": "A complete proof blueprint with all assumptions and all calculations written out.",
                "project_slug": "anderson_conjecture",
            },
            {"provider": provider, "project_slug": "anderson_conjecture"},
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["overall_verdict"], "correct")
        self.assertTrue(result["assumption_result"]["passed"])
        self.assertTrue(result["computation_result"]["passed"])
        self.assertTrue(result["logic_result"]["passed"])
        self.assertEqual(result["assumption_result"]["review_count"], 1)
        self.assertEqual(result["computation_result"]["review_count"], 1)
        self.assertEqual(result["logic_result"]["review_count"], 1)
        self.assertEqual(len(provider.calls), 3)
        self.assertTrue(all(call["schema_name"] == "verify_correctness_dimension_review" for call in provider.calls))

    def test_verify_overall_review_count_can_be_overridden(self):
        provider = PessimisticVerificationProvider(
            [
                json.dumps(self._dimension_review("assumption-usage-reviewer", dimension="assumption", verdict="correct")),
                json.dumps(self._dimension_review("premise-coverage-reviewer", dimension="assumption", verdict="correct")),
                json.dumps(self._dimension_review("calculation-consistency-reviewer", dimension="computation", verdict="correct")),
                json.dumps(self._dimension_review("arithmetic-and-transform-reviewer", dimension="computation", verdict="correct")),
                json.dumps(self._dimension_review("logical-chain-reviewer", dimension="logic", verdict="correct")),
                json.dumps(self._dimension_review("gap-and-circularity-reviewer", dimension="logic", verdict="correct")),
            ]
        )

        result = self.app.tool_manager.dispatch(
            "verify_overall",
            {
                "claim": "Every maximal localization satisfies the target property.",
                "proof": "A complete proof blueprint with all assumptions and all calculations written out.",
                "project_slug": "anderson_conjecture",
                "review_count": 2,
            },
            {"provider": provider, "project_slug": "anderson_conjecture"},
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["assumption_result"]["review_count"], 2)
        self.assertEqual(result["computation_result"]["review_count"], 2)
        self.assertEqual(result["logic_result"]["review_count"], 2)
        self.assertEqual(len(provider.calls), 6)

    def test_verify_overall_fails_if_any_dimension_fails(self):
        provider = PessimisticVerificationProvider(
            [
                json.dumps(self._dimension_review("assumption-usage-reviewer", dimension="assumption", verdict="correct")),
                json.dumps(self._dimension_review("calculation-consistency-reviewer", dimension="computation", verdict="incorrect", errors=["A denominator was simplified incorrectly."])),
                json.dumps(self._dimension_review("logical-chain-reviewer", dimension="logic", verdict="correct")),
            ]
        )

        result = self.app.tool_manager.dispatch(
            "verify_overall",
            {
                "claim": "The localization reduction proves the global statement.",
                "proof": "The proof contains a faulty denominator simplification.",
            },
            {"provider": provider, "project_slug": "anderson_conjecture"},
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["overall_verdict"], "incorrect")
        self.assertTrue(result["assumption_result"]["passed"])
        self.assertFalse(result["computation_result"]["passed"])
        self.assertTrue(result["logic_result"]["passed"])
        self.assertIn("A denominator was simplified incorrectly.", result["repair_targets"])
        self.assertIn("calculation", result["repair_targets"])

    def test_store_conclusion_stays_disabled_after_same_round_verification_in_research_mode(self):
        provider = PessimisticVerificationProvider(
            [
                json.dumps(self._dimension_review("assumption-usage-reviewer", dimension="assumption", verdict="correct")),
                json.dumps(self._dimension_review("calculation-consistency-reviewer", dimension="computation", verdict="correct")),
                json.dumps(self._dimension_review("logical-chain-reviewer", dimension="logic", verdict="correct")),
            ]
        )
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )
        runtime["provider"] = provider
        runtime["_tool_results_in_round"] = []

        verify_result = self.app.tool_manager.dispatch(
            "verify_overall",
            {
                "claim": "The localization reduction proves the global statement.",
                "proof": "A complete proof blueprint with all assumptions and all calculations written out.",
                "project_slug": "anderson_conjecture",
                "scope": "intermediate",
            },
            runtime,
        )
        runtime["_tool_results_in_round"].append({"name": "verify_overall", "output": verify_result})

        with self.assertRaises(RuntimeError):
            self.app.tool_manager.dispatch(
                "store_conclusion",
                {
                    "title": "Verified Reduction",
                    "statement": "The localization reduction proves the global statement.",
                    "proof_sketch": "The multidimensional verifier passed.",
                    "project_slug": "anderson_conjecture",
                    "status": "verified",
                },
                runtime,
            )

    def test_verify_overall_prefers_dedicated_verification_provider(self):
        verification_provider = PessimisticVerificationProvider(
            [
                json.dumps(self._dimension_review("assumption-usage-reviewer", dimension="assumption", verdict="correct")),
                json.dumps(self._dimension_review("calculation-consistency-reviewer", dimension="computation", verdict="correct")),
                json.dumps(self._dimension_review("logical-chain-reviewer", dimension="logic", verdict="correct")),
            ]
        )
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )
        runtime["provider"] = ScriptedProvider([])
        runtime["verification_provider"] = verification_provider
        runtime["verification_provider_inherit_from_main"] = False

        result = self.app.tool_manager.dispatch(
            "verify_overall",
            {
                "claim": "The localization reduction proves the global statement.",
                "proof": "A complete proof blueprint with all assumptions and all calculations written out.",
                "project_slug": "anderson_conjecture",
                "scope": "intermediate",
            },
            runtime,
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["overall_verdict"], "correct")
        self.assertEqual(len(verification_provider.calls), 3)

    def test_assess_problem_quality_uses_verification_provider_policy_once(self):
        verification_provider = PessimisticVerificationProvider(
            [
                json.dumps(
                    {
                        "reviewer_id": "quality-assessor",
                        "review_status": "passed",
                        "impact": 0.82,
                        "feasibility": 0.76,
                        "novelty": 0.74,
                        "richness": 0.71,
                        "overall": 0.78,
                        "strengths": ["Precise enough to attack."],
                        "weaknesses": [],
                        "required_refinements": [],
                        "rationale": "The scripted problem is mature enough for problem solving.",
                        "confidence": 0.88,
                    }
                )
            ]
        )
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )
        runtime["provider"] = ScriptedProvider([])
        runtime["verification_provider"] = verification_provider
        runtime["verification_provider_inherit_from_main"] = False

        result = self.app.tool_manager.dispatch(
            "assess_problem_quality",
            {
                "problem": "Prove the scripted local criterion.",
                "project_slug": "anderson_conjecture",
            },
            runtime,
        )
        state = self.app.agent.research_workflow.load_state("anderson_conjecture")

        self.assertTrue(result["passed"])
        self.assertEqual(result["review_count"], 1)
        self.assertEqual(len(verification_provider.calls), 1)
        self.assertEqual(verification_provider.calls[0]["schema_name"], "problem_quality_assessment")
        self.assertEqual(state.problem_review["review_status"], "passed")
        self.assertEqual(state.problem_review["metadata"]["skill_slug"], "quality-assessor")
        self.assertTrue(self.app.agent.research_workflow.can_enter_problem_solving(state)[0])

    def test_single_dimension_verifier_does_not_reenable_store_conclusion_in_research_mode(self):
        provider = PessimisticVerificationProvider(
            [
                json.dumps(self._dimension_review("logical-chain-reviewer", dimension="logic", verdict="correct")),
            ]
        )
        runtime = self.app.agent._build_runtime(
            mode="research",
            project_slug="anderson_conjecture",
            session_id=self.state.session_id,
        )
        runtime["provider"] = provider
        runtime["_tool_results_in_round"] = []

        logic_result = self.app.tool_manager.dispatch(
            "verify_correctness_logic",
            {
                "claim": "The localization reduction proves the global statement.",
                "proof": "A complete-looking proof text.",
                "project_slug": "anderson_conjecture",
            },
            runtime,
        )
        runtime["_tool_results_in_round"].append({"name": "verify_correctness_logic", "output": logic_result})

        with self.assertRaises(RuntimeError):
            self.app.tool_manager.dispatch(
                "store_conclusion",
                {
                    "title": "Still Unverified Reduction",
                    "statement": "The localization reduction proves the global statement.",
                    "proof_sketch": "Only the logic dimension has been checked.",
                    "project_slug": "anderson_conjecture",
                    "status": "verified",
                },
                runtime,
            )

    def test_all_builtin_skills_have_schema_sections_and_real_allowed_tools(self):
        available_tools = {item["name"] for item in self.app.tool_registry.schemas()}
        errors = []

        for skill_file in sorted(packaged_builtin_skills_dir().rglob("SKILL.md")):
            metadata, body = parse_skill_document(skill_file.read_text(encoding="utf-8"))
            errors.extend(validate_skill_document(metadata, body, expected_name=skill_file.parent.name))
            skill = self.app.skill_manager.get_skill(str(metadata.get("name") or skill_file.parent.name))
            self.assertIsNotNone(skill)
            self.assertIn("Summary", skill.sections)
            self.assertIn("Execution Steps", skill.sections)
            self.assertIn("Tool Calls", skill.sections)
            self.assertIn("File References", skill.sections)
            self.assertTrue(skill.output_contract.strip())
            self.assertIn("Usage Hint", skill.sections)
            self.assertIn("Use this skill", skill.usage_hint)
            self.assertIn("Use it when", skill.usage_hint)
            missing_tools = [tool for tool in skill.allowed_tools if tool not in available_tools]
            self.assertEqual(missing_tools, [], "missing allowed tools for %s" % skill.slug)

        self.assertEqual(errors, [])

    def test_prompt_uses_summary_indexes_before_full_definition_loading(self):
        provider = ScriptedProvider(
            [
                {
                    "chunks": ["A compact answer."],
                    "response": ProviderResponse(content="A compact answer."),
                }
            ]
        )
        self.app.agent.provider = provider

        self.app.ask("Plan the next research step.", self.state)
        system_prompt = provider.calls[0]["system_prompt"]

        self.assertIn("You are Moonshine", system_prompt)
        self.assertIn("canonical workspace, and auxiliary tool support", system_prompt)
        self.assertIn("Canonical workspace files and explicit persistence or verification tool calls are the durable state-change boundary.", system_prompt)
        self.assertIn("When a brief summary is not enough, load the full agent, skill, tool, or MCP definition explicitly.", system_prompt)
        self.assertIn("Active agent instructions:", system_prompt)
        self.assertIn("autonomous mathematical researcher", system_prompt)
        self.assertNotIn("Runtime Prompt", system_prompt)
        self.assertNotIn("moonshine:prompt", system_prompt)
        self.assertNotIn("config.yaml core settings", system_prompt)
        self.assertNotIn("MEMORY.md index excerpt", system_prompt)
        self.assertNotIn("Standing memory index:", system_prompt)
        self.assertNotIn("Working rules:", system_prompt)
        self.assertNotIn("Available skills (summaries only)", system_prompt)
        self.assertNotIn("Enabled MCP server descriptors", system_prompt)

    def test_definition_loader_tools_return_full_markdown(self):
        runtime = self.app.agent._build_runtime(
            mode=self.state.mode,
            project_slug=self.state.project_slug,
            session_id=self.state.session_id,
        )

        skill_payload = self.app.tool_manager.dispatch(
            "load_skill_definition",
            {"slug": "memory-hygiene"},
            runtime,
        )
        tool_payload = self.app.tool_manager.dispatch(
            "load_tool_definition",
            {"name": "query_memory"},
            runtime,
        )
        agent_payload = self.app.tool_manager.dispatch(
            "load_agent_definition",
            {},
            runtime,
        )

        self.assertIn("Prefer summaries with traceable sources over raw transcript dumps", skill_payload["body"])
        self.assertIn("Tool Calls", skill_payload["sections"])
        self.assertIn("File References", skill_payload["sections"])
        self.assertIn("Use this skill", skill_payload["usage_hint"])
        self.assertIn("Use it when", skill_payload["usage_hint"])
        self.assertIn("query_memory", skill_payload["tool_calls"])
        self.assertIn("memory/MEMORY.md", skill_payload["file_references"])
        self.assertIn("runtime_notice", skill_payload)
        self.assertIn("memory", tool_payload["description"].lower())
        self.assertIn("Autonomous Mathematical Researcher", agent_payload["body"])
        self.assertIn("Stage 1: Problem Design", agent_payload["body"])
        self.assertNotIn("record_solve_attempt", agent_payload["body"])
        self.assertIn("autonomous mathematical researcher", agent_payload["runtime_body"])
        self.assertNotIn("moonshine:prompt", agent_payload["runtime_body"])

    def test_query_memory_tool_returns_source_labeled_summary(self):
        self.app.ask("We discussed local criteria for Noetherian rings.", self.state)
        self.app.execute_command("/memory write Remember the local criteria discussion for Noetherian rings.", self.state)
        self.app.execute_command(
            "/knowledge add Local Criterion | Local criteria often reduce the global statement to maximal ideals. | Standard reduction sketch.",
            self.state,
        )
        runtime = self.app.agent._build_runtime(
            mode=self.state.mode,
            project_slug=self.state.project_slug,
            session_id=self.state.session_id,
        )
        payload = self.app.tool_manager.dispatch(
            "query_memory",
            {"query": "local criteria"},
            runtime,
        )

        self.assertIn("summary", payload)
        self.assertTrue(payload["compressed_windows"])
        self.assertTrue(payload["dynamic_hits"])
        self.assertTrue(payload["session_hits"])
        self.assertIn("event_hits", payload)
        self.assertTrue(payload["knowledge_hits"])
        self.assertIn("graph_hits", payload)

    def test_query_memory_reconstructs_local_windows_with_tool_results(self):
        provider = ScriptedProvider(
            [
                {
                    "response": ProviderResponse(
                        content="I will store that preference.",
                        tool_calls=[
                            ProviderToolCall(
                                name="query_memory",
                                arguments={"query": "localization branch", "project_slug": "anderson_conjecture"},
                                call_id="call-1",
                            )
                        ],
                    )
                },
                {
                    "chunks": ["Stored it for future turns."],
                    "response": ProviderResponse(content="Stored it for future turns."),
                },
            ]
        )
        self.app.agent.provider = provider

        list(self.app.ask_stream("Continue the localization branch.", self.state))
        runtime = self.app.agent._build_runtime(
            mode=self.state.mode,
            project_slug=self.state.project_slug,
            session_id=self.state.session_id,
        )
        payload = self.app.tool_manager.dispatch(
            "query_memory",
            {"query": "localization branch"},
            runtime,
        )

        rendered = "\n".join(item["window_excerpt"] for item in payload["compressed_windows"])
        self.assertIn("assistant_tool", rendered)
        self.assertIn("tool", rendered)

    def test_conversation_events_are_searchable_via_fts(self):
        provider = ScriptedProvider(
            [
                {
                    "response": ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                name="query_memory",
                                arguments={"query": "localization branch", "project_slug": "anderson_conjecture"},
                                call_id="call-1",
                            )
                        ]
                    )
                },
                {
                    "chunks": ["Stored it for future turns."],
                    "response": ProviderResponse(content="Stored it for future turns."),
                },
            ]
        )
        self.app.agent.provider = provider

        list(self.app.ask_stream("Continue the localization branch.", self.state))
        event_hits = self.app.session_store.search_conversation_events("query_memory", project_slug="anderson_conjecture", limit=5)

        self.assertTrue(event_hits)
        self.assertTrue(any(item["event_kind"] in {"assistant_tool_call", "tool_result"} for item in event_hits))

    def test_query_memory_uses_conversation_event_hits_as_formal_source(self):
        provider = ScriptedProvider(
            [
                {
                    "response": ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                name="query_memory",
                                arguments={"query": "localization branch", "project_slug": "anderson_conjecture"},
                                call_id="call-1",
                            )
                        ]
                    )
                },
                {
                    "chunks": ["Stored it for future turns."],
                    "response": ProviderResponse(content="Stored it for future turns."),
                },
            ]
        )
        self.app.agent.provider = provider

        list(self.app.ask_stream("Continue the localization branch.", self.state))
        runtime = self.app.agent._build_runtime(
            mode=self.state.mode,
            project_slug=self.state.project_slug,
            session_id=self.state.session_id,
        )
        payload = self.app.tool_manager.dispatch(
            "query_memory",
            {"query": "query_memory"},
            runtime,
        )

        self.assertFalse(payload["session_hits"])
        self.assertTrue(payload["event_hits"])
        self.assertTrue(any(item["source"] == "session-event" for item in payload["compressed_windows"]))

    def test_query_memory_all_projects_can_search_across_project_boundaries(self):
        other_state = self.app.start_shell_state(mode="research", project_slug="algebra_lab")
        self.app.ask("We discussed syzygy stabilization in algebra_lab.", other_state)
        self.app.agent.research_workflow.research_log.append_records(
            "algebra_lab",
            [
                {
                    "type": "verified_conclusion",
                    "title": "Cross Project Research Log Lemma",
                    "content": "CROSS_PROJECT_RESEARCH_LOG_SENTINEL is a durable conclusion from algebra_lab.",
                    "session_id": other_state.session_id,
                }
            ],
        )
        self.app.execute_command("/memory write Remember the syzygy stabilization heuristic.", other_state)
        self.app.execute_command(
            "/knowledge add Syzygy Heuristic | Syzygy stabilization often signals the right filtration to inspect. | Derived from the algebra_lab exploration.",
            other_state,
        )

        current_runtime = self.app.agent._build_runtime(
            mode=self.state.mode,
            project_slug=self.state.project_slug,
            session_id=self.state.session_id,
        )
        current_only = self.app.tool_manager.dispatch(
            "query_memory",
            {"query": "syzygy stabilization"},
            current_runtime,
        )
        cross_project = self.app.tool_manager.dispatch(
            "query_memory",
            {"query": "syzygy stabilization", "all_projects": True},
            current_runtime,
        )

        self.assertFalse(current_only["session_hits"])
        self.assertFalse(current_only["knowledge_hits"])
        self.assertTrue(any(item["project_slug"] == "algebra_lab" for item in cross_project["session_hits"]))
        self.assertTrue(any(item["project_slug"] == "algebra_lab" for item in cross_project["knowledge_hits"]))
        self.assertTrue(any(item["project_slug"] == "algebra_lab" for item in cross_project["dynamic_hits"]))
        self.assertTrue(cross_project["all_projects"])
        self.assertEqual(cross_project["project_scope"], "all-projects")

        research_log_search = self.app.tool_manager.dispatch(
            "query_memory",
            {"query": "CROSS_PROJECT_RESEARCH_LOG_SENTINEL", "all_projects": True},
            current_runtime,
        )
        research_hit = next(item for item in research_log_search["research_log_hits"] if item["project_slug"] == "algebra_lab")
        read_back = self.app.tool_manager.dispatch(
            "read_runtime_file",
            {"relative_path": research_hit["content_path"]},
            current_runtime,
        )

        self.assertEqual(research_hit["content_path"], "projects/algebra_lab/memory/research_log.jsonl")
        self.assertEqual(read_back["root"], str(self.app.paths.home))
        self.assertIn("CROSS_PROJECT_RESEARCH_LOG_SENTINEL", read_back["content"])

    def test_context_manager_compresses_history_when_budget_is_tight(self):
        for index in range(8):
            self.app.ask(
                "Turn %s: We keep discussing integral extensions, Krull dimension, and local methods in quite some detail."
                % index,
                self.state,
            )

        self.app.config.provider.max_context_tokens = 220
        self.app.config.context.compression_threshold_tokens = 110
        provider = ScriptedProvider(
            [
                {
                    "chunks": ["Compressed answer."],
                    "response": ProviderResponse(content="Compressed answer."),
                }
            ]
        )
        self.app.agent.provider = provider
        self.app.context_manager.provider = provider

        self.app.ask("Summarize the current direction.", self.state)

        first_call_messages = provider.calls[0]["messages"]
        rendered = "\n".join(item["content"] for item in first_call_messages)
        self.assertIn("<session-history-summary>", rendered)
        self.assertIn("compressed conversation history", rendered)
        self.assertIn("query_session_records", rendered)
        self.assertIn("Raw record locations:", rendered)
        self.assertIn("sessions/%s/messages.jsonl" % self.state.session_id, rendered)
        self.assertTrue(self.app.paths.session_context_summaries_file(self.state.session_id).exists())
        summaries = read_jsonl(self.app.paths.session_context_summaries_file(self.state.session_id))
        self.assertEqual(summaries[-1]["recovery_tool"], "query_session_records")
        self.assertIn("messages", summaries[-1]["raw_record_locations"])

    def test_context_manager_reuses_previous_summary_until_threshold_is_hit_again(self):
        for index in range(10):
            self.app.session_store.append_message(
                self.state.session_id,
                "user" if index % 2 == 0 else "assistant",
                "History turn %s: %s" % (index, " ".join(["local-criterion-branch"] * 18)),
                metadata={},
            )

        provider = CountingSummaryProvider()
        self.app.context_manager.provider = provider
        self.app.config.context.compression_threshold_tokens = 700
        self.app.config.context.history_compression_token_budget = 90000
        self.app.config.context.history_compression_chunk_token_budget = 1500

        compressed1, meta1 = self.app.context_manager.build_provider_messages(
            session_id=self.state.session_id,
            user_message="Continue the branch.",
            system_prompt="You are Moonshine.",
            tool_schemas=[],
        )
        summary_call_count = len(provider.calls)
        self.assertTrue(meta1["compressed_history"])
        self.assertGreater(summary_call_count, 0)
        self.assertIn("<session-history-summary", "\n".join(item["content"] for item in compressed1))

        compressed2, meta2 = self.app.context_manager.build_provider_messages(
            session_id=self.state.session_id,
            user_message="Continue the branch with one more local step.",
            system_prompt="You are Moonshine.",
            tool_schemas=[],
        )
        self.assertTrue(meta2["compressed_history"])
        self.assertTrue(meta2.get("reused_summary"))
        self.assertEqual(len(provider.calls), summary_call_count)
        self.assertIn("<session-history-summary", "\n".join(item["content"] for item in compressed2))

    def test_context_manager_splits_old_history_into_sixty_message_count_chunks(self):
        provider = CountingSummaryProvider()
        self.app.context_manager.provider = provider
        self.app.config.context.history_compression_chunk_count = 60
        messages = [
            {
                "id": index + 1,
                "role": "user" if index % 2 == 0 else "assistant",
                "content": "History message %s. %s" % (index, " ".join(["detailed-proof-branch"] * 120)),
            }
            for index in range(180)
        ]

        summaries = self.app.context_manager._summarize_history_chunks(messages)

        self.assertEqual(len(summaries), 60)
        self.assertEqual(len(provider.calls), 60)
        chunk_sizes = []
        for call in provider.calls:
            rendered = call["messages"][0]["content"]
            chunk_sizes.append(
                rendered.count('"role": "user"')
                + rendered.count('"role": "assistant"')
                + rendered.count('"role": "tool"')
            )
            self.assertIn("research progress report", call["messages"][0]["content"])
            self.assertIn("roughly 1500 tokens", call["messages"][0]["content"])
            self.assertIn("roughly 1500 tokens", call["system_prompt"])
        self.assertTrue(all(size == 3 for size in chunk_sizes))

    def test_context_manager_splits_oversized_summary_source_into_bounded_provider_calls(self):
        provider = CountingSummaryProvider()
        self.app.context_manager.provider = provider
        source = " ".join(["oversized-compression-source"] * 240)

        with mock.patch("moonshine.agent_runtime.context_manager.SUMMARY_PROVIDER_INPUT_TOKEN_BUDGET", 40):
            summary = self.app.context_manager._summarize_with_provider(
                purpose="oversized test context",
                text=source,
                token_budget=8,
            )

        self.assertGreater(len(provider.calls), 1)
        self.assertIn("Preserved research summary", summary)
        for call in provider.calls:
            rendered = call["messages"][0]["content"]
            source_chunk = rendered.rsplit("\n\n", 1)[-1]
            self.assertLessEqual(self.app.context_manager.estimate_tokens(source_chunk), 40)

    def test_research_log_compression_splits_oversized_source_into_bounded_provider_calls(self):
        provider = CountingSummaryProvider()
        self.app.agent.research_workflow.provider = provider
        source = " ".join(["research-log-compression-source"] * 240)

        with mock.patch("moonshine.agent_runtime.research_workflow.RESEARCH_COMPRESSION_INPUT_TOKEN_BUDGET", 40):
            summary = self.app.agent.research_workflow._summarize_research_log_text(source, 1)

        self.assertGreater(len(provider.calls), 1)
        self.assertIn("Preserved research summary", summary)
        for call in provider.calls:
            rendered = call["messages"][0]["content"]
            source_chunk = rendered.split(":\n", 1)[-1]
            self.assertLessEqual(
                moonshine_utils.estimate_token_count(source_chunk, model_name=provider.model),
                40,
            )

    def test_context_manager_recompresses_from_raw_history_after_threshold_is_exceeded_again(self):
        for index in range(10):
            self.app.session_store.append_message(
                self.state.session_id,
                "user" if index % 2 == 0 else "assistant",
                "History turn %s: %s" % (index, " ".join(["local-criterion-branch"] * 18)),
                metadata={},
            )

        provider = SequencedSummaryProvider()
        self.app.context_manager.provider = provider
        self.app.config.context.compression_threshold_tokens = 700
        self.app.config.context.history_compression_chunk_count = 60
        self.app.config.context.history_compression_chunk_token_budget = 1500

        compressed1, meta1 = self.app.context_manager.build_provider_messages(
            session_id=self.state.session_id,
            user_message="Continue the branch.",
            system_prompt="You are Moonshine.",
            tool_schemas=[],
        )
        self.assertTrue(meta1["compressed_history"])
        self.assertIn("Summary batch 1.", "\n".join(item["content"] for item in compressed1))
        first_call_count = len(provider.calls)

        self.app.config.context.compression_threshold_tokens = 5000
        compressed2, meta2 = self.app.context_manager.build_provider_messages(
            session_id=self.state.session_id,
            user_message="Add one non-overflowing continuation step.",
            system_prompt="You are Moonshine.",
            tool_schemas=[],
        )
        self.assertTrue(meta2["compressed_history"])
        self.assertTrue(meta2.get("reused_summary"))
        self.assertEqual(len(provider.calls), first_call_count)
        self.assertIn("Summary batch 1.", "\n".join(item["content"] for item in compressed2))

        self.app.session_store.append_message(
            self.state.session_id,
            "assistant",
            "A long continuation step that pushes the context pressure back over the threshold: %s"
            % (" ".join(["proof-branch-update"] * 120)),
            metadata={},
        )
        self.app.config.context.compression_threshold_tokens = 700
        compressed3, meta3 = self.app.context_manager.build_provider_messages(
            session_id=self.state.session_id,
            user_message="Continue after the long branch update.",
            system_prompt="You are Moonshine.",
            tool_schemas=[],
        )
        self.assertTrue(meta3["compressed_history"])
        self.assertFalse(meta3.get("reused_summary", False))
        self.assertGreater(len(provider.calls), first_call_count)
        self.assertNotIn("Summary batch 1.", "\n".join(item["content"] for item in compressed3))

    def test_context_pressure_snapshot_counts_tool_schema_budget(self):
        snapshot_without_tools = self.app.context_manager.context_pressure_snapshot(
            messages=[{"role": "user", "content": "Summarize the current direction."}],
            system_prompt="You are Moonshine.",
            tool_schemas=[],
        )
        snapshot_with_tools = self.app.context_manager.context_pressure_snapshot(
            messages=[{"role": "user", "content": "Summarize the current direction."}],
            system_prompt="You are Moonshine.",
            tool_schemas=[
                {
                    "name": "large_tool",
                    "description": "This schema is intentionally verbose. " * 60,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search text." * 40}
                        },
                    },
                }
            ],
        )

        self.assertGreater(snapshot_with_tools["estimated_tokens"], snapshot_without_tools["estimated_tokens"])

    def test_context_pressure_warning_is_emitted_before_compression(self):
        provider = ScriptedProvider(
            [
                {
                    "chunks": ["Warning-path answer."],
                    "response": ProviderResponse(content="Warning-path answer."),
                }
            ]
        )
        self.app.agent.provider = provider
        self.app.context_manager.provider = provider
        self.app.config.provider.max_context_tokens = 1400
        self.app.config.context.compression_threshold_tokens = 700
        self.app.config.context.warning_ratio = 0.5
        self.app.config.context.pressure_warning_ratio = 0.85
        self.app.config.context.pressure_critical_ratio = 0.95

        long_message = " ".join(["Krull-dimension-local-criterion"] * 55)
        events = list(self.app.ask_stream(long_message, self.state))
        status_texts = [event.text for event in events if event.type == "status"]

        self.assertTrue(any("Context pressure warning" in text for text in status_texts))
        self.assertFalse(any("Compressed older in-turn context" in text for text in status_texts))

    def test_live_provider_context_is_compressed_after_large_tool_results(self):
        self.app.config.provider.max_context_tokens = 4000
        self.app.config.context.compression_threshold_tokens = 2000
        self.app.config.context.warning_ratio = 0.5
        self.app.config.context.compression_min_recent_messages = 1
        provider = ScriptedProvider(
            [
                {
                    "response": ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(name="load_agent_definition", arguments={}, call_id="call-1"),
                            ProviderToolCall(name="load_skill_definition", arguments={"slug": "memory-hygiene"}, call_id="call-2"),
                            ProviderToolCall(name="load_skill_definition", arguments={"slug": "query-memory"}, call_id="call-3"),
                            ProviderToolCall(name="load_skill_definition", arguments={"slug": "conclusion-manage"}, call_id="call-4"),
                            ProviderToolCall(name="load_tool_definition", arguments={"name": "manage_skill"}, call_id="call-5"),
                            ProviderToolCall(name="load_tool_definition", arguments={"name": "query_memory"}, call_id="call-6"),
                        ]
                    )
                },
                {
                    "chunks": ["Compressed answer after loading the tool manuals."],
                    "response": ProviderResponse(content="Compressed answer after loading the tool manuals."),
                },
            ]
        )
        self.app.agent.provider = provider
        self.app.context_manager.provider = provider

        events = list(self.app.ask_stream("Load the relevant capability manuals and then answer.", self.state))
        status_texts = [event.text for event in events if event.type == "status"]

        self.assertTrue(any("Compressed older in-turn context" in text for text in status_texts))
        self.assertLess(len(provider.calls[1]["messages"]), 8)

    def test_live_context_trim_drops_orphan_leading_tool_results(self):
        self.app.config.context.compression_threshold_tokens = 300
        self.app.config.context.compression_min_recent_messages = 1
        messages = [
            {"role": "user", "content": "Start the turn."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_runtime_file",
                            "arguments": "{\"path\":\"large.md\"}",
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "read_runtime_file",
                "content": " ".join(["large-tool-result"] * 500),
            },
            {"role": "assistant", "content": "Continue after the tool result."},
        ]

        compressed, metadata = self.app.context_manager.compact_provider_messages(
            messages=messages,
            system_prompt="You are Moonshine.",
            session_id=self.state.session_id,
            artifact_label="test-live-provider",
            tool_schemas=[],
        )

        self.assertTrue(metadata["compressed_history"])
        for index, message in enumerate(compressed):
            if message.get("role") != "tool":
                continue
            self.assertGreater(index, 0)
            self.assertEqual(compressed[index - 1].get("role"), "assistant")
            self.assertTrue(compressed[index - 1].get("tool_calls"))

    def test_orphan_leading_tool_results_are_recovered_as_plain_context(self):
        recovered = self.app.context_manager._recover_leading_tool_results_as_context(
            [
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "name": "read_runtime_file",
                    "content": json.dumps(
                        {
                            "name": "read_runtime_file",
                            "output": {"content": "Important lemma from the reference file."},
                            "error": None,
                        }
                    ),
                },
                {"role": "assistant", "content": "Continue from the tool evidence."},
            ]
        )

        self.assertEqual(recovered[0]["role"], "assistant")
        self.assertNotIn("tool_call_id", recovered[0])
        self.assertIn("preserved as plain context", recovered[0]["content"])
        self.assertIn("read_runtime_file", recovered[0]["content"])
        self.assertIn("Full tool result:", recovered[0]["content"])
        self.assertIn("Important lemma", recovered[0]["content"])
        self.assertIn('"output": {"content": "Important lemma from the reference file."}', recovered[0]["content"])
        self.assertEqual(recovered[1]["content"], "Continue from the tool evidence.")

    def test_tail_cut_uses_strict_budget_and_allows_zero_recent_messages(self):
        self.app.config.context.compression_threshold_tokens = 1000
        self.app.config.context.tail_token_budget_ratio = 0.2
        messages = [
            {"role": "user", "content": "Protected opening message."},
            {"role": "assistant", "content": " ".join(["oversized-recent-message"] * 500)},
        ]

        tail_start = self.app.context_manager._find_tail_cut_by_tokens(messages, head_end=1)

        self.assertEqual(tail_start, len(messages))

    def test_tail_cut_keeps_tool_call_groups_atomic_under_strict_budget(self):
        self.app.config.context.compression_threshold_tokens = 1000
        self.app.config.context.tail_token_budget_ratio = 0.2
        messages = [
            {"role": "user", "content": "Protected opening message."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "read_runtime_file",
                            "arguments": json.dumps({"path": "large.md", "padding": " ".join(["argument-padding"] * 500)}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "name": "read_runtime_file",
                "content": "small result",
            },
        ]

        tail_start = self.app.context_manager._find_tail_cut_by_tokens(messages, head_end=1)

        self.assertEqual(tail_start, len(messages))

    def test_context_overflow_error_triggers_aggressive_recovery_retry(self):
        self.app.config.provider.max_context_tokens = 1024
        self.app.config.context.compression_threshold_tokens = 2000
        self.app.config.context.warning_ratio = 0.5
        self.app.config.context.overflow_retry_limit = 2

        for index in range(6):
            self.app.ask(
                "History turn %s: %s"
                % (index, " ".join(["integral-extension-local-criterion"] * 30)),
                self.state,
            )

        provider = OverflowThenRecoverProvider()
        self.app.agent.provider = provider
        self.app.context_manager.provider = provider

        events = list(self.app.ask_stream("Summarize the current direction after the overflow.", self.state))
        status_texts = [event.text for event in events if event.type == "status"]
        turn_events = [
            json.loads(line)
            for line in self.app.paths.session_turn_events_file(self.state.session_id).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

        self.assertTrue(any("Context overflow detected" in text for text in status_texts))
        self.assertEqual(events[-1].text, "Recovered after overflow.")
        self.assertTrue(any(item["type"] == "context_overflow_recovery" for item in turn_events))

    def test_token_estimator_prefers_tiktoken_when_available(self):
        class FakeEncoding(object):
            def encode(self, text, disallowed_special=()):
                return [1, 2, 3, 4, 5, 6, 7]

        with mock.patch("moonshine.utils._get_tiktoken_encoding", return_value=FakeEncoding()):
            self.assertEqual(moonshine_utils.estimate_token_count("Moonshine token test", model_name="gpt-4o-mini"), 7)

    def test_installed_skill_markdown_is_auto_discovered(self):
        custom_skill = self.app.paths.installed_skills_dir / "proof-audit" / "SKILL.md"
        atomic_write(
            custom_skill,
            """---
name: proof-audit
description: Check gaps, hidden assumptions, and dependency chains.
allowed-tools: query_memory
metadata:
  title: Proof Audit
  category: installed
  tags: proof,review
---
# Proof Audit

## Summary
- Check gaps in the argument.

## Execution Steps
1. Track gaps in the argument.

## Tool Calls
- `query_memory`: Load prior proof discussions when needed.

## File References
- `projects/<project_slug>/memory/lemmas.md`
""",
        )

        reloaded = MoonshineApp(home=self.temp_dir.name)
        skill = reloaded.skill_manager.get_skill("proof-audit")

        self.assertIsNotNone(skill)
        self.assertIn("dependency chains", skill.description)

    def test_mcp_server_descriptor_is_discoverable_and_loadable(self):
        descriptor = self.app.paths.mcp_servers_dir / "reference-server.md"
        atomic_write(
            descriptor,
            """<!--
{
  "slug": "reference-server",
  "title": "Reference Server",
  "description": "Descriptor for an external reference lookup MCP server.",
  "transport": "stdio",
  "enabled": false,
  "command": "python",
  "args": ["-m", "reference_server"]
}
-->
# Reference Server

- Provides external reference lookups.
""",
        )

        reloaded = MoonshineApp(home=self.temp_dir.name)
        servers = reloaded.tool_manager.list_mcp_servers(include_disabled=True)
        payload = reloaded.tool_manager.dispatch(
            "load_mcp_server_definition",
            {"slug": "reference-server"},
            reloaded.agent._build_runtime(
                mode=self.state.mode,
                project_slug=self.state.project_slug,
                session_id=self.state.session_id,
            ),
        )

        self.assertTrue(any(item["slug"] == "reference-server" for item in servers))
        self.assertIn("external reference lookup", payload["description"])
        self.assertIn("Provides external reference lookups", payload["body"])

    def test_mcp_stdio_transport_discovers_and_calls_external_tool(self):
        server_script = self.app.paths.home / "echo_mcp_server.py"
        atomic_write(
            server_script,
            r'''
import json
import sys


def read_message():
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        text = line.decode("ascii").strip()
        if ":" in text:
            key, value = text.split(":", 1)
            headers[key.lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def send_message(payload):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(("Content-Length: %s\r\n\r\n" % len(body)).encode("ascii") + body)
    sys.stdout.buffer.flush()


while True:
    message = read_message()
    if message is None:
        break
    method = message.get("method")
    message_id = message.get("id")
    if method == "initialize":
        send_message(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "echo-test", "version": "0.1.0"},
                },
            }
        )
    elif method == "tools/list":
        tool = {
            "name": "echo_text",
            "description": "Echo text for Moonshine MCP tests.",
            "inputSchema": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        }
        send_message(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {"tools": [tool]},
            }
        )
    elif method == "tools/call":
        params = message.get("params") or {}
        arguments = params.get("arguments") or {}
        text = str(arguments.get("text", ""))
        send_message(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "result": {
                    "content": [{"type": "text", "text": "echo: " + text}],
                    "structuredContent": {"echo": text},
                    "isError": False,
                },
            }
        )
    elif message_id is not None:
        send_message(
            {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32601, "message": "unknown method"},
            }
        )
'''.lstrip(),
        )
        descriptor = self.app.paths.mcp_servers_dir / "echo-server.md"
        atomic_write(
            descriptor,
            """<!--
{
  "slug": "echo-server",
  "title": "Echo MCP Server",
  "description": "Test MCP server that exposes an echo tool.",
  "transport": "stdio",
  "enabled": true,
  "command": "%s",
  "args": ["%s"],
  "discover_tools": true,
  "timeout_seconds": 5
}
-->
# Echo MCP Server
""" % (sys.executable.replace("\\", "\\\\"), str(server_script).replace("\\", "\\\\")),
        )

        reloaded = MoonshineApp(home=self.temp_dir.name)
        tool_name = "mcp_echo_server_echo_text"
        schema_names = {item["name"] for item in reloaded.tool_manager.schemas()}
        result = reloaded.tool_manager.dispatch(
            tool_name,
            {"text": "hello mcp"},
            reloaded.agent._build_runtime(
                mode=self.state.mode,
                project_slug=self.state.project_slug,
                session_id=self.state.session_id,
            ),
        )

        self.assertIn(tool_name, schema_names)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["server_slug"], "echo-server")
        self.assertEqual(result["tool_name"], "echo_text")
        self.assertIn("echo: hello mcp", result["content"])
        self.assertEqual(result["structured_content"]["echo"], "hello mcp")

    def test_builtin_mcp_templates_include_tavily_and_filesystem(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        servers = {
            item["slug"]: item
            for item in reloaded.tool_manager.list_mcp_servers(include_disabled=True)
        }

        self.assertIn("tavily", servers)
        self.assertIn("filesystem", servers)
        self.assertFalse(servers["tavily"]["enabled"])
        self.assertTrue(servers["filesystem"]["enabled"])
        self.assertIn("tavily-mcp", " ".join(servers["tavily"]["args"]))
        self.assertEqual(servers["filesystem"]["transport"], "local")
        self.assertEqual(servers["filesystem"]["command"], "")

        schema_names = {item["name"] for item in reloaded.tool_manager.schemas()}
        self.assertIn("mcp_filesystem_read_file", schema_names)
        self.assertIn("mcp_filesystem_write_file", schema_names)

    def test_startup_notice_prompts_for_user_tavily_key_when_missing(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        with mock.patch.dict("os.environ", {"TAVILY_API_KEY": ""}):
            notices = reloaded.startup_notices()

        self.assertTrue(any("TAVILY_API_KEY" in item for item in notices))
        self.assertTrue(any("https://app.tavily.com/" in item for item in notices))

    def test_startup_notice_does_not_treat_tavily_key_as_project_permanent(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        with mock.patch.dict("os.environ", {"TAVILY_API_KEY": "tvly-test-value"}):
            notices = reloaded.startup_notices()

        self.assertTrue(any("Tavily MCP is disabled" in item for item in notices))
        self.assertFalse(any("tvly-test-value" in item for item in notices))

    def test_tavily_credentials_file_is_created_only_after_user_setup(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)

        self.assertFalse(reloaded.paths.credentials_file.exists())

        with mock.patch.dict("os.environ", {"TAVILY_API_KEY": ""}):
            result = reloaded.configure_tavily_api_key("tvly-test-value", enable=True)

        payload = read_json(reloaded.paths.credentials_file, default={})
        descriptor = (reloaded.paths.mcp_servers_dir / "tavily.md").read_text(encoding="utf-8")
        server = reloaded.tool_manager.get_mcp_server("tavily")

        self.assertEqual(result["credential_file"], str(reloaded.paths.credentials_file))
        self.assertEqual(payload["secrets"]["TAVILY_API_KEY"], "tvly-test-value")
        self.assertIn('"enabled": true', descriptor)
        self.assertIn("${TAVILY_API_KEY}", descriptor)
        self.assertNotIn("tvly-test-value", descriptor)
        self.assertTrue(server.enabled)

    def test_mcp_default_env_reads_tavily_key_from_credentials_file(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        reloaded.configure_tavily_api_key("tvly-file-value", enable=True)

        with mock.patch.dict("os.environ", {"TAVILY_API_KEY": ""}):
            defaults = reloaded.tool_manager.mcp_registry._default_env({})

        self.assertEqual(defaults["TAVILY_API_KEY"], "tvly-file-value")

    def test_tavily_setup_command_does_not_print_secret(self):
        output = io.StringIO()
        with mock.patch("sys.stdout", output), mock.patch.dict("os.environ", {"TAVILY_API_KEY": ""}):
            exit_code = cli_main([
                "--home",
                self.temp_dir.name,
                "mcp",
                "--set-tavily-key",
                "tvly-cli-value",
            ])

        payload = read_json(self.app.paths.credentials_file, default={})
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["secrets"]["TAVILY_API_KEY"], "tvly-cli-value")
        self.assertNotIn("tvly-cli-value", output.getvalue())

    def test_tavily_static_tools_register_when_enabled(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        reloaded.configure_tavily_api_key("tvly-test-value", enable=True)
        enabled = MoonshineApp(home=self.temp_dir.name)

        schema_names = {item["name"] for item in enabled.tool_manager.schemas()}
        self.assertIn("mcp_tavily_tavily_search", schema_names)
        self.assertIn("mcp_tavily_tavily_extract", schema_names)

    def test_mcp_descriptor_values_support_env_var_interpolation(self):
        from moonshine.tools.mcp_bridge import _build_safe_env, _interpolate_env_value

        with mock.patch.dict("os.environ", {"TAVILY_API_KEY": "tvly-test-value"}):
            env = _build_safe_env({"TAVILY_API_KEY": "${TAVILY_API_KEY}"})
            interpolated_arg = _interpolate_env_value("https://example.invalid/${TAVILY_API_KEY}/mcp")

        self.assertEqual(env["TAVILY_API_KEY"], "tvly-test-value")
        self.assertEqual(interpolated_arg, "https://example.invalid/tvly-test-value/mcp")

    def test_filesystem_mcp_root_defaults_to_runtime_sessions_dir(self):
        from moonshine.tools.mcp_bridge import MCPStdioClient

        reloaded = MoonshineApp(home=self.temp_dir.name)
        server = reloaded.tool_manager.get_mcp_server("filesystem")
        defaults = MCPStdioClient(server)._default_env()

        self.assertEqual(defaults["MOONSHINE_MCP_FILESYSTEM_ROOT"], str(reloaded.paths.sessions_dir))
        self.assertEqual(defaults["MOONSHINE_SESSIONS_DIR"], str(reloaded.paths.sessions_dir))

    def test_mcp_filesystem_root_defaults_to_current_project_at_call_time(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        session_id = reloaded.session_store.create_session("chat", "general")
        defaults = reloaded.tool_manager.mcp_registry._default_env({"session_id": session_id, "project_slug": "general"})

        self.assertEqual(defaults["MOONSHINE_HOME"], str(reloaded.paths.home))
        self.assertEqual(defaults["MOONSHINE_CURRENT_SESSION_DIR"], str(reloaded.paths.session_dir(session_id)))
        self.assertEqual(defaults["MOONSHINE_PROJECT_DIR"], str(reloaded.paths.project_dir("general")))
        self.assertEqual(defaults["MOONSHINE_MCP_FILESYSTEM_ROOT"], str(reloaded.paths.project_dir("general")))

    def test_mcp_filesystem_root_falls_back_to_session_without_project(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        session_id = reloaded.session_store.create_session("chat", "")
        defaults = reloaded.tool_manager.mcp_registry._default_env({"session_id": session_id})

        self.assertEqual(defaults["MOONSHINE_MCP_FILESYSTEM_ROOT"], str(reloaded.paths.session_dir(session_id)))

    def test_mcp_filesystem_root_env_override_wins_over_session_default(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        session_id = reloaded.session_store.create_session("chat", "general")
        custom_root = str(reloaded.paths.home / "safe-workspace")
        with mock.patch.dict("os.environ", {"MOONSHINE_MCP_FILESYSTEM_ROOT": custom_root}):
            defaults = reloaded.tool_manager.mcp_registry._default_env({"session_id": session_id})

        self.assertEqual(defaults["MOONSHINE_MCP_FILESYSTEM_ROOT"], custom_root)

    def test_filesystem_mcp_tools_use_local_implementation_without_external_transport(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        target_file = reloaded.paths.project_dir("general") / "sample.txt"
        atomic_write(target_file, "hello filesystem fallback")
        runtime = reloaded.agent._build_runtime(mode="chat", project_slug="general", session_id=self.state.session_id)

        with mock.patch("moonshine.tools.mcp_bridge.MCPStdioClient.__enter__", side_effect=AssertionError("stdio transport should not be used")):
            result = reloaded.tool_manager.dispatch("mcp_filesystem_list_directory", {"path": "."}, runtime)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["server_slug"], "filesystem")
        self.assertEqual(result["mcp_result"].get("fallback"), "local-filesystem")
        self.assertIn("sample.txt", result["content"])

    def test_runtime_file_reads_are_project_relative_with_legacy_project_prefix(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        project_slug = "general"
        target_file = reloaded.paths.project_dir(project_slug) / "workspace" / "problem.md"
        atomic_write(target_file, "project relative problem")
        runtime = reloaded.agent._build_runtime(mode="research", project_slug=project_slug, session_id=self.state.session_id)

        project_relative = reloaded.tool_manager.dispatch(
            "read_runtime_file",
            {"relative_path": "workspace/problem.md"},
            runtime,
        )
        legacy_prefixed = reloaded.tool_manager.dispatch(
            "read_runtime_file",
            {"relative_path": "projects/general/workspace/problem.md"},
            runtime,
        )

        self.assertEqual(project_relative["path"], str(target_file))
        self.assertEqual(legacy_prefixed["path"], str(target_file))
        self.assertEqual(project_relative["root"], str(reloaded.paths.project_dir(project_slug)))
        self.assertEqual(Path(legacy_prefixed["relative_path"]), Path("workspace/problem.md"))

    def test_runtime_file_reads_global_knowledge_prefix_from_home(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        global_index = reloaded.paths.knowledge_index_file
        atomic_write(global_index, "# Global Knowledge\n\n- durable result\n")
        project_shadow = reloaded.paths.project_dir("general") / "knowledge" / "KNOWLEDGE.md"
        atomic_write(project_shadow, "wrong shadow")
        runtime = reloaded.agent._build_runtime(mode="research", project_slug="general", session_id=self.state.session_id)

        result = reloaded.tool_manager.dispatch(
            "read_runtime_file",
            {"relative_path": "knowledge/KNOWLEDGE.md"},
            runtime,
        )

        self.assertEqual(result["path"], str(global_index))
        self.assertEqual(result["root"], str(reloaded.paths.home))
        self.assertIn("durable result", result["content"])

    def test_runtime_file_reads_session_prefix_from_home(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        session_id = "session-readable"
        messages_file = reloaded.paths.session_messages_file(session_id)
        atomic_write(messages_file, '{"role":"assistant","content":"exact session fact"}\n')
        project_shadow = reloaded.paths.project_dir("general") / "sessions" / session_id / "messages.jsonl"
        atomic_write(project_shadow, "wrong shadow")
        runtime = reloaded.agent._build_runtime(mode="research", project_slug="general", session_id=self.state.session_id)

        result = reloaded.tool_manager.dispatch(
            "read_runtime_file",
            {"relative_path": "sessions/session-readable/messages.jsonl"},
            runtime,
        )

        self.assertEqual(result["path"], str(messages_file))
        self.assertEqual(result["root"], str(reloaded.paths.home))
        self.assertIn("exact session fact", result["content"])

    def test_runtime_file_reads_staged_inputs_prefix_from_home(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        input_file = reloaded.paths.home / "inputs" / "source.md"
        atomic_write(input_file, "# Input\n\nhome-level staged input")
        project_shadow = reloaded.paths.project_dir("general") / "inputs" / "source.md"
        atomic_write(project_shadow, "wrong shadow")
        runtime = reloaded.agent._build_runtime(mode="chat", project_slug="general", session_id=self.state.session_id)

        result = reloaded.tool_manager.dispatch(
            "read_runtime_file",
            {"relative_path": "inputs/source.md"},
            runtime,
        )

        self.assertEqual(result["path"], str(input_file))
        self.assertEqual(result["root"], str(reloaded.paths.home))
        self.assertIn("home-level staged input", result["content"])

    def test_runtime_file_reads_home_absolute_paths_from_returned_tools(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        entry_file = reloaded.paths.knowledge_entries_dir / "known.md"
        atomic_write(entry_file, "# Known\n\nabsolute path content")
        runtime = reloaded.agent._build_runtime(mode="research", project_slug="general", session_id=self.state.session_id)

        result = reloaded.tool_manager.dispatch(
            "read_runtime_file",
            {"relative_path": str(entry_file)},
            runtime,
        )

        self.assertEqual(result["path"], str(entry_file))
        self.assertEqual(result["root"], str(reloaded.paths.home))
        self.assertIn("absolute path content", result["content"])

    def test_runtime_file_reads_other_project_prefix_from_home(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        other_log = reloaded.paths.project_research_log_file("algebra_lab")
        atomic_write(other_log, '{"content":"cross project exact fact"}\n')
        project_shadow = reloaded.paths.project_dir("general") / "projects" / "algebra_lab" / "memory" / "research_log.jsonl"
        atomic_write(project_shadow, "wrong shadow")
        runtime = reloaded.agent._build_runtime(mode="research", project_slug="general", session_id=self.state.session_id)

        result = reloaded.tool_manager.dispatch(
            "read_runtime_file",
            {"relative_path": "projects/algebra_lab/memory/research_log.jsonl"},
            runtime,
        )

        self.assertEqual(result["path"], str(other_log))
        self.assertEqual(result["root"], str(reloaded.paths.home))
        self.assertIn("cross project exact fact", result["content"])

    def test_mcp_filesystem_normalizes_legacy_project_prefix_for_writes(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        project_slug = "general"
        runtime = reloaded.agent._build_runtime(mode="chat", project_slug=project_slug, session_id=self.state.session_id)
        target_file = reloaded.paths.project_dir(project_slug) / "workspace" / "problem.md"
        nested_file = reloaded.paths.project_dir(project_slug) / "projects" / project_slug / "workspace" / "problem.md"

        result = reloaded.tool_manager.dispatch(
            "mcp_filesystem_write_file",
            {"path": "projects/general/workspace/problem.md", "content": "normalized write"},
            runtime,
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["structured_content"]["path"], str(target_file))
        self.assertEqual(target_file.read_text(encoding="utf-8"), "normalized write")
        self.assertFalse(nested_file.exists())

    def test_research_mode_filesystem_write_blocks_managed_memory_and_problem_reference(self):
        reloaded = MoonshineApp(home=self.temp_dir.name)
        project_slug = "general"
        runtime = reloaded.agent._build_runtime(mode="research", project_slug=project_slug, session_id=self.state.session_id)

        problem_result = reloaded.tool_manager.dispatch(
            "mcp_filesystem_write_file",
            {"path": "workspace/problem.md", "content": "manual problem write"},
            runtime,
        )
        memory_result = reloaded.tool_manager.dispatch(
            "mcp_filesystem_write_file",
            {"path": "memory/research_log.md", "content": "manual memory write"},
            runtime,
        )
        normal_result = reloaded.tool_manager.dispatch(
            "mcp_filesystem_write_file",
            {"path": "workspace/notes.md", "content": "allowed note"},
            runtime,
        )

        self.assertEqual(problem_result["status"], "error")
        self.assertIn("Research-mode write blocked", problem_result["content"])
        self.assertIn("workspace/problem.md", problem_result["content"])
        self.assertEqual(memory_result["status"], "error")
        self.assertIn("Research-mode write blocked", memory_result["content"])
        self.assertIn("memory/", memory_result["content"])
        self.assertEqual(normal_result["status"], "ok")
        self.assertTrue((reloaded.paths.project_dir(project_slug) / "workspace" / "notes.md").exists())

    def test_tavily_search_falls_back_to_direct_http_when_mcp_transport_fails(self):
        class FakeHTTPResponse(object):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "answer": "AI for math saw new theorem-proving benchmarks.",
                        "results": [
                            {
                                "title": "AI for Math News",
                                "url": "https://example.com/ai-math",
                                "content": "A concise update about new math benchmarks.",
                            }
                        ],
                    }
                ).encode("utf-8")

        reloaded = MoonshineApp(home=self.temp_dir.name)
        reloaded.configure_tavily_api_key("tvly-test-value", enable=True)
        enabled = MoonshineApp(home=self.temp_dir.name)
        runtime = enabled.agent._build_runtime(mode="chat", project_slug="general", session_id=self.state.session_id)

        with mock.patch("moonshine.tools.mcp_bridge.MCPStdioClient.__enter__", side_effect=RuntimeError("stdio failed")):
            with mock.patch("moonshine.tools.mcp_bridge.urlopen", return_value=FakeHTTPResponse()):
                result = enabled.tool_manager.dispatch(
                    "mcp_tavily_tavily_search",
                    {"query": "latest AI for math news", "topic": "news", "max_results": 3},
                    runtime,
                )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["server_slug"], "tavily")
        self.assertEqual(result["mcp_result"].get("fallback"), "tavily-http")
        self.assertIn("AI for Math News", result["content"])
        self.assertIn("answer", result["structured_content"])

    def test_runtime_tool_definition_is_resynced_from_packaged_default_on_restart(self):
        target = self.app.paths.tool_definitions_dir / "manage_skill.md"
        atomic_write(
            target,
            """<!--
{
  "name": "manage_skill",
  "handler": "manage_skill",
  "description": "Stale runtime copy that should be replaced on restart.",
  "parameters": {
    "type": "object",
    "properties": {
      "operation": {"type": "string"}
    },
    "required": ["operation"]
  }
}
-->
# Tool: manage_skill

Stale runtime override.
""",
        )

        packaged_text = read_text(packaged_tool_definitions_dir() / "manage_skill.md").rstrip()
        self.assertNotEqual(read_text(target).rstrip(), packaged_text)

        override_app = MoonshineApp(home=self.temp_dir.name)
        tool_schema = {
            item["name"]: item
            for item in override_app.tool_registry.schemas()
        }
        self.assertEqual(read_text(target).rstrip(), packaged_text)
        self.assertNotIn("Stale runtime copy", tool_schema["manage_skill"]["description"])
        self.assertIn("installed Agent Skills-compatible markdown skills", tool_schema["manage_skill"]["description"])

    def test_removed_builtin_skill_runtime_copy_is_deleted_on_restart(self):
        stale_skill = self.app.paths.builtin_skills_dir / "removed-skill" / "SKILL.md"
        atomic_write(
            stale_skill,
            """---
name: removed-skill
description: Stale runtime builtin skill that no longer exists in packaged assets.
---

# Removed Skill

## Usage Hint
- Use this skill only if stale runtime copies are not cleaned.
""",
        )
        self.assertTrue(stale_skill.exists())

        MoonshineApp(home=self.temp_dir.name)

        self.assertFalse(stale_skill.exists())

    def test_memory_created_at_is_preserved_when_entry_is_updated(self):
        first = self.app.memory.remember_explicit(
            "Prefer reductions mod maximal ideals first.",
            project_slug=self.state.project_slug,
            session_id=self.state.session_id,
        )
        second = self.app.memory.remember_explicit(
            "Prefer reductions mod maximal ideals first.",
            project_slug=self.state.project_slug,
            session_id=self.state.session_id,
        )

        entry = self.app.memory.dynamic_store.get_entry(first.slug)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.created_at, first.created_at)
        self.assertEqual(second.slug, first.slug)

    def test_manage_skill_tool_supports_lifecycle_operations(self):
        runtime = self.app.agent._build_runtime(
            mode=self.state.mode,
            project_slug=self.state.project_slug,
            session_id=self.state.session_id,
        )
        self.app.tool_manager.dispatch(
            "manage_skill",
            {
                "operation": "create",
                "slug": "proof-audit",
                "title": "Proof Audit",
                "description": "Check gaps in arguments.",
                "body": "# Proof Audit\n\n- Track proof gaps.\n",
                "tags": ["proof", "review"],
            },
            runtime,
        )
        created = self.app.skill_manager.get_skill("proof-audit")
        self.assertIsNotNone(created)
        self.assertIn("## Summary", created.body)
        self.assertIn("## Execution Steps", created.body)
        self.assertIn("name: proof-audit", self.app.paths.installed_skills_dir.joinpath("proof-audit", "SKILL.md").read_text(encoding="utf-8"))

        self.app.tool_manager.dispatch(
            "manage_skill",
            {
                "operation": "patch",
                "slug": "proof-audit",
                "old_text": "Track proof gaps.",
                "new_text": "Track proof gaps and hidden assumptions.",
            },
            runtime,
        )
        patched = self.app.skill_manager.get_skill("proof-audit")
        self.assertIsNotNone(patched)
        self.assertIn("hidden assumptions", patched.body)

        file_result = self.app.tool_manager.dispatch(
            "manage_skill",
            {
                "operation": "write_file",
                "slug": "proof-audit",
                "relative_path": "notes/example.md",
                "content": "# Example\n\n- Proof sketch note.\n",
            },
            runtime,
        )
        self.assertTrue(self.app.paths.installed_skills_dir.joinpath("proof-audit", "notes", "example.md").exists())
        self.assertIn("write_file", file_result["operation"])

        self.app.tool_manager.dispatch(
            "manage_skill",
            {
                "operation": "delete_file",
                "slug": "proof-audit",
                "relative_path": "notes/example.md",
            },
            runtime,
        )
        self.assertFalse(self.app.paths.installed_skills_dir.joinpath("proof-audit", "notes", "example.md").exists())

        self.app.tool_manager.dispatch(
            "manage_skill",
            {
                "operation": "delete",
                "slug": "proof-audit",
            },
            runtime,
        )
        self.assertIsNone(self.app.skill_manager.get_skill("proof-audit"))

    def test_manage_skill_rejects_invalid_template_breakage(self):
        runtime = self.app.agent._build_runtime(
            mode=self.state.mode,
            project_slug=self.state.project_slug,
            session_id=self.state.session_id,
        )
        self.app.tool_manager.dispatch(
            "manage_skill",
            {
                "operation": "create",
                "slug": "proof-audit",
                "title": "Proof Audit",
                "description": "Check gaps in arguments.",
                "workflow": "- Review the current proof.\n- List missing lemmas.",
            },
            runtime,
        )

        with self.assertRaises(ValueError):
            self.app.tool_manager.dispatch(
                "manage_skill",
                {
                    "operation": "patch",
                    "slug": "proof-audit",
                    "old_text": "## Summary",
                    "new_text": "## Objective",
                },
                runtime,
            )

    def test_llm_memory_pipeline_routes_specialized_extraction_skills(self):
        provider = SkillExtractionProvider(
            [
                json.dumps(
                    {
                        "run": True,
                        "skills": ["extract-user-memory", "extract-project-memory", "extract-conclusion-memory"],
                        "reason": "The turn contains a durable user preference, project update, and stable claim.",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "dynamic_entries": [
                            {
                                "alias": "user-preferences",
                                "title": "User Preference",
                                "summary": "Prefers concise proofs.",
                                "body": "The user prefers concise proofs in mathematical work.",
                                "tags": ["preference", "style"],
                            }
                        ],
                        "knowledge_entries": [],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "dynamic_entries": [
                            {
                                "alias": "project-context",
                                "title": "Project Context Update",
                                "summary": "The project focuses on local criteria for Noetherian rings.",
                                "body": "Current focus: local criteria for Noetherian rings.",
                                "project_slug": "anderson_conjecture",
                                "tags": ["project", "context"],
                            }
                        ],
                        "knowledge_entries": [],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "dynamic_entries": [],
                        "knowledge_entries": [
                            {
                                "title": "Local Criterion",
                                "statement": "Local criteria reduce the global statement to maximal ideals.",
                                "proof_sketch": "Derived from the current turn.",
                                "status": "partial",
                                "project_slug": "anderson_conjecture",
                                "tags": ["lemma", "local-criterion"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        self.app.memory.set_provider(provider)

        result = self.app.memory.auto_extract(
            "I prefer concise proofs. The project studies local criteria for Noetherian rings.",
            "A useful partial conclusion is that local criteria reduce the global statement to maximal ideals.",
            "anderson_conjecture",
            session_id=self.state.session_id,
        )

        self.assertEqual(result["entries"], 3)
        self.assertEqual(result["conclusions"], 0)
        self.assertIn("concise proofs", self.app.memory.dynamic_store.read_file("user-preferences"))
        self.assertIn("local criteria for Noetherian rings", self.app.memory.dynamic_store.read_file("project-context", project_slug="anderson_conjecture"))
        knowledge_hits = self.app.memory.knowledge_store.search("Local Criterion", project_slug="anderson_conjecture", limit=1)
        self.assertFalse(knowledge_hits)
        lemmas_text = self.app.memory.dynamic_store.read_file("project-lemmas", project_slug="anderson_conjecture")
        self.assertIn("Candidate: Local Criterion", lemmas_text)
        self.assertIn("memory-trigger-evaluator", provider.calls[0]["messages"][0]["content"])
        self.assertIn("extract-user-memory", provider.calls[1]["messages"][0]["content"])
        self.assertEqual(provider.calls[0]["method"], "generate_structured")
        self.assertEqual(provider.calls[0]["schema_name"], "memory_trigger_decision")

    def test_background_auto_extract_subagent_writes_memory_and_notifies(self):
        provider = SkillExtractionProvider(
            [
                json.dumps(
                    {
                        "run": True,
                        "skills": ["extract-user-memory"],
                        "reason": "The turn contains a durable user preference.",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "dynamic_entries": [
                            {
                                "alias": "user-preferences",
                                "title": "User Preference",
                                "summary": "Prefers concise proofs.",
                                "body": "The user prefers concise proofs in mathematical work.",
                                "tags": ["preference", "style"],
                            }
                        ],
                        "knowledge_entries": [],
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        self.app.memory.set_provider(provider)

        scheduled = self.app.memory.submit_auto_extract(
            "I prefer concise proofs.",
            "Understood.",
            "anderson_conjecture",
            session_id=self.state.session_id,
            pending_tool_calls=False,
        )

        self.assertTrue(scheduled["queued"])
        self.app.memory.wait_for_auto_extract_tasks(self.state.session_id, timeout_seconds=2.0)
        notifications = self.app.memory.collect_auto_extract_notifications(session_id=self.state.session_id)

        self.assertTrue(notifications)
        self.assertIn("Memory updated in", notifications[0])
        self.assertIn("/memory to edit", notifications[0])
        self.assertIn("concise proofs", self.app.memory.dynamic_store.read_file("user-preferences"))

    def test_pre_compress_and_session_end_extraction_are_audited_without_conversation_events(self):
        chat_state = self.app.start_shell_state(mode="chat", project_slug="anderson_conjecture")
        pre_result = self.app.memory.extract_pre_compress(
            session_id=chat_state.session_id,
            project_slug="anderson_conjecture",
            window_text="I prefer diagram-first explanations. Lemma: local criteria reduce the global claim.",
        )
        self.app.session_store.append_message(
            chat_state.session_id,
            "user",
            "I prefer concise proof sketches at the end of research sessions.",
        )
        self.app.session_store.append_message(chat_state.session_id, "assistant", "Understood.")
        end_result = self.app.memory.extract_session_end(
            session_id=chat_state.session_id,
            project_slug="anderson_conjecture",
        )

        audit_entries = read_jsonl(self.app.paths.memory_audit_log)
        pre_events = [
            item
            for item in audit_entries
            if item.get("event") == "memory_extract_completed" and item.get("trigger") == "pre_compress"
            and item.get("session_id") == chat_state.session_id
        ]
        end_events = [
            item
            for item in audit_entries
            if item.get("event") == "memory_extract_completed" and item.get("trigger") == "session_end"
            and item.get("session_id") == chat_state.session_id
        ]
        conversation_events = self.app.session_store.get_conversation_events(chat_state.session_id)

        self.assertTrue(pre_events)
        self.assertTrue(end_events)
        self.assertGreaterEqual(pre_events[-1]["entries"] + pre_events[-1]["conclusions"], 1)
        self.assertGreaterEqual(end_events[-1]["entries"] + end_events[-1]["conclusions"], 1)
        self.assertTrue(pre_result["updated_files"])
        self.assertTrue(end_result["updated_files"])
        self.assertFalse(
            any(
                item["event_kind"] == "memory_extract_completed"
                and item["payload"].get("trigger") in {"pre_compress", "session_end"}
                for item in conversation_events
            )
        )

    def test_schema_invalid_llm_output_is_rejected_without_writing_memory(self):
        provider = SkillExtractionProvider(
            [
                json.dumps(
                    {
                        "run": True,
                        "skills": ["extract-user-memory"],
                        "reason": "The turn contains a durable preference.",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "dynamic_entries": [
                            {
                                "alias": "not-allowed",
                                "title": "Bad Alias",
                                "summary": "This should be rejected.",
                                "body": "This should be rejected.",
                                "tags": ["invalid"],
                            }
                        ],
                        "knowledge_entries": [],
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        self.app.memory.set_provider(provider)

        result = self.app.memory.auto_extract(
            "I prefer concise proofs.",
            "Understood.",
            "anderson_conjecture",
            session_id=self.state.session_id,
        )

        self.assertEqual(result["entries"], 0)
        self.assertEqual(result["conclusions"], 0)
        self.assertNotIn("concise proofs", self.app.memory.dynamic_store.read_file("user-preferences"))

    def test_local_extraction_writer_skips_malformed_structured_items(self):
        extracted = ExtractedItems(
            entries=[
                {
                    "alias": "not-allowed",
                    "slug": "bad-entry",
                    "title": "Bad Entry",
                    "summary": "Bad summary",
                    "body": "Bad body",
                    "source": "heuristic",
                    "tags": [],
                },
                {
                    "alias": "project-progress",
                    "slug": "missing-project",
                    "title": "Missing Project",
                    "summary": "Should not write without project scope.",
                    "body": "Should not write without project scope.",
                    "source": "heuristic",
                    "tags": [],
                },
            ],
            conclusions=[
                {
                    "title": "Malformed Conclusion",
                    "statement": "This should not enter knowledge.",
                    "proof_sketch": "",
                    "status": "not-a-status",
                    "tags": [],
                }
            ],
        )

        result = self.app.memory._apply_extracted_items(
            extracted,
            project_slug=None,
            session_id=self.state.session_id,
            source_message_role="user",
            source_excerpt="malformed extraction payload",
        )

        self.assertEqual(result["entries"], 0)
        self.assertEqual(result["conclusions"], 0)
        self.assertNotIn("Bad Entry", self.app.paths.memory_index_file.read_text(encoding="utf-8"))

    def test_structured_task_registry_exposes_memory_schemas(self):
        trigger_task = get_structured_task("memory-trigger-decision")
        extraction_task = get_structured_task("memory-extraction-result")
        project_task = get_structured_task("research-project-resolution")
        verifier_task = get_structured_task("pessimistic-verification-review")
        task_names = [item.task_name for item in list_structured_tasks()]

        self.assertEqual(trigger_task.schema_name, "memory_trigger_decision")
        self.assertEqual(extraction_task.schema_name, "memory_extraction_result")
        self.assertEqual(project_task.schema_name, "research_project_resolution")
        self.assertEqual(verifier_task.schema_name, "pessimistic_verification_review")
        self.assertIn("memory-trigger-decision", task_names)
        self.assertIn("memory-extraction-result", task_names)
        self.assertIn("research-project-resolution", task_names)
        self.assertIn("pessimistic-verification-review", task_names)
        self.assertIn("skills", trigger_task.schema["properties"])
        self.assertIn("dynamic_entries", extraction_task.schema["properties"])
        self.assertIn("recommended_action", project_task.schema["properties"])
        self.assertIn("verdict", verifier_task.schema["properties"])

    def test_structured_task_call_sites_use_structured_generation_and_validation_where_needed(self):
        with open("moonshine/agent_runtime/extraction.py", encoding="utf-8") as handle:
            extraction_source = handle.read()
        with open("moonshine/agent_runtime/research_mode.py", encoding="utf-8") as handle:
            project_source = handle.read()
        with open("moonshine/agent_runtime/research_workflow.py", encoding="utf-8") as handle:
            workflow_source = handle.read()
        with open("moonshine/tools/verification_tools.py", encoding="utf-8") as handle:
            verifier_source = handle.read()

        self.assertIn('get_structured_task("memory-trigger-decision")', extraction_source)
        self.assertIn('get_structured_task("memory-extraction-result")', extraction_source)
        self.assertIn("generate_structured", extraction_source)
        self.assertIn("validate_json_schema(structured, schema)", extraction_source)
        self.assertIn("validate_json_schema(parsed, schema)", extraction_source)

        self.assertIn("RESEARCH_PROJECT_RESOLUTION_SCHEMA", project_source)
        self.assertIn("generate_structured", project_source)
        self.assertIn("validate_json_schema(payload, RESEARCH_PROJECT_RESOLUTION_SCHEMA)", project_source)

        self.assertIn("check_conclusion_gate", workflow_source)
        self.assertIn("build_autonomous_prompt", workflow_source)
        self.assertIn("## Stage Transition", workflow_source)
        self.assertIn("research_log.md", workflow_source)

        self.assertIn("PESSIMISTIC_REVIEW_SCHEMA", verifier_source)
        self.assertIn("generate_structured", verifier_source)
        self.assertIn("validate_json_schema(result, PESSIMISTIC_VERIFICATION_RESULT_SCHEMA)", verifier_source)

    def test_openai_structured_generation_uses_api_json_schema_and_local_validation(self):
        captured = {}

        class FakeHTTPResponse(object):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps({"ok": True, "items": []}),
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeHTTPResponse()

        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "ok": {"type": "boolean"},
                "items": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["ok", "items"],
        }
        provider = OpenAIChatCompletionsProvider(
            model="moonshine-test",
            base_url="https://example.invalid/v1",
            api_key_env="OPENAI_API_KEY",
            timeout_seconds=7,
            temperature=0.0,
            stream=False,
        )

        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), mock.patch("moonshine.providers.urlopen", fake_urlopen):
            payload = provider.generate_structured(
                system_prompt="Return structured data.",
                messages=[{"role": "user", "content": "Return ok."}],
                response_schema=schema,
                schema_name="test_schema",
            )

        self.assertEqual(payload, {"ok": True, "items": []})
        self.assertEqual(captured["timeout"], 7)
        self.assertNotIn("tools", captured["payload"])
        response_format = captured["payload"]["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])
        self.assertEqual(response_format["json_schema"]["name"], "test_schema")
        self.assertEqual(response_format["json_schema"]["schema"], schema)

    def test_openai_provider_preserves_reasoning_content(self):
        class FakeHTTPResponse(object):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "reasoning_content": "reasoning to pass back",
                                    "content": "visible answer",
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        provider = OpenAIChatCompletionsProvider(
            model="moonshine-test",
            base_url="https://example.invalid/v1",
            api_key_env="OPENAI_API_KEY",
            timeout_seconds=7,
            temperature=0.0,
            stream=False,
        )

        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), mock.patch("moonshine.providers.urlopen", lambda request, timeout: FakeHTTPResponse()):
            response = provider.generate(system_prompt="system", messages=[{"role": "user", "content": "hello"}])

        self.assertEqual(response.content, "visible answer")
        self.assertEqual(response.reasoning_content, "reasoning to pass back")

    def test_openai_structured_generation_falls_back_to_json_object_response_format(self):
        captured_payloads = []

        class FakeHTTPResponse(object):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps({"ok": True}),
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            captured_payloads.append(payload)
            if len(captured_payloads) == 1:
                raise HTTPError(
                    request.full_url,
                    400,
                    "Bad Request",
                    hdrs=None,
                    fp=io.BytesIO(
                        b'{"error":{"message":"This response_format type is unavailable now","type":"invalid_request_error"}}'
                    ),
                )
            return FakeHTTPResponse()

        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        }
        provider = OpenAIChatCompletionsProvider(
            model="moonshine-test",
            base_url="https://example.invalid/v1",
            api_key_env="OPENAI_API_KEY",
            timeout_seconds=7,
            temperature=0.0,
            stream=False,
            max_retries=0,
        )

        with mock.patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), mock.patch("moonshine.providers.urlopen", fake_urlopen):
            payload = provider.generate_structured(
                system_prompt="Return JSON.",
                messages=[{"role": "user", "content": "Return {\"ok\": true} as JSON."}],
                response_schema=schema,
                schema_name="fallback_schema",
            )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(captured_payloads[0]["response_format"]["type"], "json_schema")
        self.assertEqual(captured_payloads[1]["response_format"]["type"], "json_object")
        self.assertIn("JSON mode fallback", captured_payloads[1]["messages"][0]["content"])
        self.assertIn("fallback_schema", captured_payloads[1]["messages"][0]["content"])
        self.assertIn('"required": [', captured_payloads[1]["messages"][0]["content"])

    def test_context_manager_returns_reasoning_content_from_assistant_metadata(self):
        session_id = self.app.session_store.create_session("chat", "general")
        self.app.session_store.append_message(session_id, "user", "first question")
        self.app.session_store.append_message(
            session_id,
            "assistant",
            "first answer",
            metadata={"reasoning_content": "reasoning to pass back"},
        )

        messages, _metadata = self.app.context_manager.build_provider_messages(
            session_id=session_id,
            user_message="next question",
            system_prompt="system",
            tool_schemas=[],
        )

        assistant_messages = [item for item in messages if item.get("role") == "assistant"]
        self.assertEqual(assistant_messages[-1].get("reasoning_content"), "reasoning to pass back")

    def test_render_agent_events_prints_reasoning_delta_with_label(self):
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            final = render_agent_events(
                [
                    AgentEvent(type="reasoning_delta", text="thinking one "),
                    AgentEvent(type="reasoning_delta", text="thinking two"),
                    AgentEvent(type="text_delta", text="visible answer"),
                    AgentEvent(type="final", text="visible answer", payload={"render_final": False}),
                ]
        )

        rendered = output.getvalue()
        self.assertIn("[reasoning]\nthinking one thinking two\n[/reasoning]\nvisible answer", rendered)
        self.assertEqual(final, "visible answer")

    def test_azure_openai_provider_uses_deployment_url_api_key_header_and_system_message(self):
        captured = {}

        class FakeHTTPResponse(object):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "Azure response ok.",
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["headers"] = dict(request.header_items())
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeHTTPResponse()

        provider = AzureOpenAIChatCompletionsProvider(
            model="gpt-5-chat",
            base_url="https://example-resource.cognitiveservices.azure.com/",
            api_key_env="AZURE_OPENAI_API_KEY",
            api_version="2024-12-01-preview",
            timeout_seconds=11,
            temperature=None,
            stream=False,
        )

        with mock.patch.dict("os.environ", {"AZURE_OPENAI_API_KEY": "azure-test-key"}), mock.patch("moonshine.providers.urlopen", fake_urlopen):
            response = provider.generate(
                system_prompt="You are Moonshine.",
                messages=[{"role": "user", "content": "Say ok."}],
                tool_schemas=[{"name": "example_tool", "description": "Example.", "parameters": {"type": "object"}}],
            )

        self.assertEqual(response.content, "Azure response ok.")
        self.assertEqual(captured["timeout"], 11)
        self.assertIn("/openai/deployments/gpt-5-chat/chat/completions?api-version=2024-12-01-preview", captured["url"])
        self.assertEqual(captured["headers"]["Api-key"], "azure-test-key")
        self.assertNotIn("model", captured["payload"])
        self.assertNotIn("temperature", captured["payload"])
        self.assertEqual(captured["payload"]["messages"][0], {"role": "system", "content": "You are Moonshine."})
        self.assertEqual(captured["payload"]["messages"][1], {"role": "user", "content": "Say ok."})
        self.assertEqual(captured["payload"]["tools"][0]["function"]["name"], "example_tool")

    def test_azure_openai_provider_does_not_send_max_context_tokens(self):
        captured = {}

        class FakeHTTPResponse(object):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "Azure response ok.",
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeHTTPResponse()

        provider = AzureOpenAIChatCompletionsProvider(
            model="gpt-5-chat",
            base_url="https://example-resource.cognitiveservices.azure.com/",
            api_key_env="AZURE_OPENAI_API_KEY",
            api_version="2024-12-01-preview",
            timeout_seconds=11,
            temperature=None,
            stream=False,
        )

        with mock.patch.dict("os.environ", {"AZURE_OPENAI_API_KEY": "azure-test-key"}), \
            mock.patch.object(
                provider,
                "_build_payload",
                return_value={
                    "model": "gpt-5-chat",
                    "messages": [{"role": "system", "content": "You are Moonshine."}],
                    "max_context_tokens": 258000,
                },
            ), \
            mock.patch("moonshine.providers.urlopen", fake_urlopen):
            response = provider.generate(
                system_prompt="You are Moonshine.",
                messages=[{"role": "user", "content": "Say ok."}],
                tool_schemas=[],
            )

        self.assertEqual(response.content, "Azure response ok.")
        self.assertNotIn("model", captured["payload"])
        self.assertNotIn("max_context_tokens", captured["payload"])

    def test_azure_openai_provider_surfaces_http_error_body_in_offline_note(self):
        error_body = json.dumps({"error": {"message": "The deployed model does not support streaming."}}).encode("utf-8")

        provider = AzureOpenAIChatCompletionsProvider(
            model="gpt-5-chat",
            base_url="https://example-resource.cognitiveservices.azure.com/",
            api_key_env="AZURE_OPENAI_API_KEY",
            api_version="2024-12-01-preview",
            timeout_seconds=11,
            temperature=None,
            stream=False,
            max_retries=0,
        )

        http_error = HTTPError(
            url="https://example-resource.cognitiveservices.azure.com/",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(error_body),
        )

        with mock.patch.dict("os.environ", {"AZURE_OPENAI_API_KEY": "azure-test-key"}), \
            mock.patch("moonshine.providers.urlopen", side_effect=http_error):
            response = provider.generate(
                system_prompt="You are Moonshine.",
                messages=[{"role": "user", "content": "Say ok."}],
                tool_schemas=[],
            )

        self.assertIn("HTTP Error 400: Bad Request", response.content)
        self.assertIn("does not support streaming", response.content)

    def test_azure_openai_stream_falls_back_to_non_stream_generate(self):
        calls = []

        class FakeHTTPResponse(object):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "Azure non-stream response ok.",
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            calls.append(payload)
            if payload.get("stream"):
                raise HTTPError(
                    url=request.full_url,
                    code=400,
                    msg="Bad Request",
                    hdrs=None,
                    fp=io.BytesIO(
                        json.dumps({"error": {"message": "Streaming is not enabled for this deployment."}}).encode("utf-8")
                    ),
                )
            return FakeHTTPResponse()

        provider = AzureOpenAIChatCompletionsProvider(
            model="gpt-5-chat",
            base_url="https://example-resource.cognitiveservices.azure.com/",
            api_key_env="AZURE_OPENAI_API_KEY",
            api_version="2024-12-01-preview",
            timeout_seconds=11,
            temperature=None,
            stream=True,
            max_retries=0,
        )

        with mock.patch.dict("os.environ", {"AZURE_OPENAI_API_KEY": "azure-test-key"}), \
            mock.patch("moonshine.providers.urlopen", fake_urlopen):
            events = list(
                provider.stream_generate(
                    system_prompt="You are Moonshine.",
                    messages=[{"role": "user", "content": "Say ok."}],
                    tool_schemas=[],
                )
            )

        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0].get("stream"))
        self.assertNotIn("stream", calls[1])
        self.assertEqual(events[0].type, "text_delta")
        self.assertEqual(events[0].text, "Azure non-stream response ok.")
        self.assertEqual(events[-1].type, "response")
        self.assertEqual(events[-1].response.content, "Azure non-stream response ok.")

    def test_azure_openai_generate_retries_without_temperature_when_deployment_rejects_it(self):
        calls = []

        class FakeHTTPResponse(object):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "Azure response without temperature.",
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            calls.append(payload)
            if "temperature" in payload:
                raise HTTPError(
                    url=request.full_url,
                    code=400,
                    msg="Bad Request",
                    hdrs=None,
                    fp=io.BytesIO(
                        json.dumps(
                            {
                                "error": {
                                    "message": "Unsupported value: 'temperature' does not support 0.2 with this model. Only the default (1) value is supported.",
                                    "type": "invalid_request_error",
                                    "param": "temperature",
                                    "code": "unsupported_value",
                                }
                            }
                        ).encode("utf-8")
                    ),
                )
            return FakeHTTPResponse()

        provider = AzureOpenAIChatCompletionsProvider(
            model="gpt-5-mini",
            base_url="https://example-resource.cognitiveservices.azure.com/",
            api_key_env="AZURE_OPENAI_API_KEY",
            api_version="2024-12-01-preview",
            timeout_seconds=11,
            temperature=0.2,
            stream=False,
            max_retries=0,
        )

        with mock.patch.dict("os.environ", {"AZURE_OPENAI_API_KEY": "azure-test-key"}), \
            mock.patch("moonshine.providers.urlopen", fake_urlopen):
            response = provider.generate(
                system_prompt="You are Moonshine.",
                messages=[{"role": "user", "content": "Say ok."}],
                tool_schemas=[],
            )

        self.assertEqual(response.content, "Azure response without temperature.")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["temperature"], 0.2)
        self.assertNotIn("temperature", calls[1])

    def test_azure_openai_generate_structured_retries_without_temperature_when_deployment_rejects_it(self):
        calls = []

        class FakeHTTPResponse(object):
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": json.dumps({"status": "ok"}),
                                }
                            }
                        ]
                    }
                ).encode("utf-8")

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data.decode("utf-8"))
            calls.append(payload)
            if "temperature" in payload:
                raise HTTPError(
                    url=request.full_url,
                    code=400,
                    msg="Bad Request",
                    hdrs=None,
                    fp=io.BytesIO(
                        json.dumps(
                            {
                                "error": {
                                    "message": "Unsupported value: 'temperature' does not support 0.2 with this model. Only the default (1) value is supported.",
                                    "type": "invalid_request_error",
                                    "param": "temperature",
                                    "code": "unsupported_value",
                                }
                            }
                        ).encode("utf-8")
                    ),
                )
            return FakeHTTPResponse()

        provider = AzureOpenAIChatCompletionsProvider(
            model="gpt-5-mini",
            base_url="https://example-resource.cognitiveservices.azure.com/",
            api_key_env="AZURE_OPENAI_API_KEY",
            api_version="2024-12-01-preview",
            timeout_seconds=11,
            temperature=0.2,
            stream=False,
            max_retries=0,
        )

        with mock.patch.dict("os.environ", {"AZURE_OPENAI_API_KEY": "azure-test-key"}), \
            mock.patch("moonshine.providers.urlopen", fake_urlopen):
            payload = provider.generate_structured(
                system_prompt="Return structured data.",
                messages=[{"role": "user", "content": "Return json."}],
                response_schema={
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                    "required": ["status"],
                },
                schema_name="simple_status",
            )

        self.assertEqual(payload, {"status": "ok"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["temperature"], 0.2)
        self.assertNotIn("temperature", calls[1])

    def test_structured_generation_wraps_single_required_array_property(self):
        class ArrayPayloadProvider(BaseProvider):
            def generate(self, *, system_prompt, messages, tool_schemas=None):
                return ProviderResponse(
                    content=json.dumps(
                        [
                            {
                                "type": "research_note",
                                "title": "FTC reduction",
                                "content": "The turn analyzed the FTC reduction with the matrix structure.",
                            }
                        ]
                    )
                )

        payload = ArrayPayloadProvider().generate_structured(
            system_prompt="Return structured data.",
            messages=[{"role": "user", "content": "Return records."}],
            response_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "records": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "type": {"type": "string"},
                                "title": {"type": "string"},
                                "content": {"type": "string"},
                            },
                            "required": ["type", "title", "content"],
                        },
                    }
                },
                "required": ["records"],
            },
            schema_name="research_turn_archive",
        )

        self.assertEqual(payload["records"][0]["title"], "FTC reduction")

    def test_model_context_window_resolves_current_gpt5_deployment(self):
        self.assertEqual(resolve_model_context_window("gpt-5-chat"), 400000)
        self.assertEqual(resolve_model_context_window("gpt-5"), 400000)
        self.assertEqual(resolve_model_context_window("custom-model"), DEFAULT_CONTEXT_WINDOW_TOKENS)
        self.assertEqual(resolve_model_context_window("custom-model", configured=12345), 12345)
        self.assertEqual(resolve_model_context_window("gpt-5-chat", configured=DEFAULT_CONTEXT_WINDOW_TOKENS), 258000)

    def test_azure_provider_configuration_command_stores_key_and_updates_settings(self):
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            exit_code = cli_main(
                [
                    "--home",
                    self.temp_dir.name,
                    "provider",
                    "--azure-openai",
                    "--endpoint",
                    "https://example-resource.cognitiveservices.azure.com/",
                    "--deployment",
                    "gpt-5-chat",
                    "--api-version",
                    "2024-12-01-preview",
                    "--set-api-key",
                    "azure-secret-value",
                ]
            )

        config_payload = read_json(self.app.paths.config_file, default={})
        credentials_payload = read_json(self.app.paths.credentials_file, default={})
        rendered = output.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertEqual(config_payload["provider"]["type"], "azure_openai")
        self.assertEqual(config_payload["provider"]["model"], "gpt-5-chat")
        self.assertEqual(config_payload["provider"]["base_url"], "https://example-resource.cognitiveservices.azure.com")
        self.assertEqual(config_payload["provider"]["api_version"], "2024-12-01-preview")
        self.assertEqual(config_payload["provider"]["api_key_env"], "AZURE_OPENAI_API_KEY")
        self.assertIsNone(config_payload["provider"]["temperature"])
        self.assertEqual(config_payload["provider"]["max_context_tokens"], 258000)
        self.assertEqual(credentials_payload["secrets"]["AZURE_OPENAI_API_KEY"], "azure-secret-value")
        self.assertNotIn("azure-secret-value", rendered)

    def test_verification_provider_configuration_command_stores_dedicated_openai_compatible_settings(self):
        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            exit_code = cli_main(
                [
                    "--home",
                    self.temp_dir.name,
                    "provider",
                    "--target",
                    "verification",
                    "--openai-compatible",
                    "--base-url",
                    "https://example.invalid/v1",
                    "--model",
                    "verify-model",
                    "--api-key-env",
                    "VERIFY_API_KEY",
                    "--set-api-key",
                    "verify-secret-value",
                ]
            )

        config_payload = read_json(self.app.paths.config_file, default={})
        credentials_payload = read_json(self.app.paths.credentials_file, default={})

        self.assertEqual(exit_code, 0)
        self.assertFalse(config_payload["verification_provider"]["inherit_from_main"])
        self.assertEqual(config_payload["verification_provider"]["type"], "openai_compatible")
        self.assertEqual(config_payload["verification_provider"]["model"], "verify-model")
        self.assertEqual(config_payload["verification_provider"]["base_url"], "https://example.invalid/v1")
        self.assertEqual(config_payload["verification_provider"]["api_key_env"], "VERIFY_API_KEY")
        self.assertEqual(credentials_payload["secrets"]["VERIFY_API_KEY"], "verify-secret-value")

    def test_provider_command_supports_incremental_main_provider_updates(self):
        steps = [
            ["--home", self.temp_dir.name, "provider", "--type", "openai_compatible"],
            ["--home", self.temp_dir.name, "provider", "--base-url", "https://example.invalid/v1"],
            ["--home", self.temp_dir.name, "provider", "--model", "gpt-5-mini"],
            ["--home", self.temp_dir.name, "provider", "--api-key-env", "OPENAI_API_KEY"],
            ["--home", self.temp_dir.name, "provider", "--stream"],
            ["--home", self.temp_dir.name, "provider", "--temperature", "0.1"],
        ]

        for argv in steps:
            with mock.patch("sys.stdout", io.StringIO()):
                exit_code = cli_main(argv)
            self.assertEqual(exit_code, 0)

        config_payload = read_json(self.app.paths.config_file, default={})

        self.assertEqual(config_payload["provider"]["type"], "openai_compatible")
        self.assertEqual(config_payload["provider"]["base_url"], "https://example.invalid/v1")
        self.assertEqual(config_payload["provider"]["model"], "gpt-5-mini")
        self.assertEqual(config_payload["provider"]["api_key_env"], "OPENAI_API_KEY")
        self.assertTrue(config_payload["provider"]["stream"])
        self.assertEqual(config_payload["provider"]["temperature"], 0.1)

    def test_provider_command_supports_incremental_verification_provider_updates(self):
        steps = [
            ["--home", self.temp_dir.name, "provider", "--target", "verification", "--dedicated"],
            ["--home", self.temp_dir.name, "provider", "--target", "verification", "--type", "azure_openai"],
            ["--home", self.temp_dir.name, "provider", "--target", "verification", "--endpoint", "https://verify-resource.cognitiveservices.azure.com/"],
            ["--home", self.temp_dir.name, "provider", "--target", "verification", "--deployment", "verify-gpt-5"],
            ["--home", self.temp_dir.name, "provider", "--target", "verification", "--api-version", "2024-12-01-preview"],
            ["--home", self.temp_dir.name, "provider", "--target", "verification", "--no-stream"],
            ["--home", self.temp_dir.name, "provider", "--target", "verification", "--set-api-key", "verify-secret-value"],
        ]

        for argv in steps:
            with mock.patch("sys.stdout", io.StringIO()):
                exit_code = cli_main(argv)
            self.assertEqual(exit_code, 0)

        config_payload = read_json(self.app.paths.config_file, default={})
        credentials_payload = read_json(self.app.paths.credentials_file, default={})

        self.assertFalse(config_payload["verification_provider"]["inherit_from_main"])
        self.assertEqual(config_payload["verification_provider"]["type"], "azure_openai")
        self.assertEqual(config_payload["verification_provider"]["base_url"], "https://verify-resource.cognitiveservices.azure.com")
        self.assertEqual(config_payload["verification_provider"]["model"], "verify-gpt-5")
        self.assertEqual(config_payload["verification_provider"]["api_version"], "2024-12-01-preview")
        self.assertEqual(config_payload["verification_provider"]["api_key_env"], "AZURE_OPENAI_API_KEY")
        self.assertFalse(config_payload["verification_provider"]["stream"])
        self.assertEqual(credentials_payload["secrets"]["AZURE_OPENAI_API_KEY"], "verify-secret-value")

    def test_switching_provider_type_to_azure_clears_default_temperature(self):
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(
                cli_main(
                    [
                        "--home",
                        self.temp_dir.name,
                        "provider",
                        "--temperature",
                        "0.2",
                    ]
                ),
                0,
            )
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(
                cli_main(
                    [
                        "--home",
                        self.temp_dir.name,
                        "provider",
                        "--type",
                        "azure_openai",
                    ]
                ),
                0,
            )

        config_payload = read_json(self.app.paths.config_file, default={})
        self.assertIsNone(config_payload["provider"]["temperature"])

    def test_verification_provider_can_return_to_inherit_main_after_dedicated_configuration(self):
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(
                cli_main(
                    [
                        "--home",
                        self.temp_dir.name,
                        "provider",
                        "--target",
                        "verification",
                        "--dedicated",
                        "--type",
                        "openai_compatible",
                        "--base-url",
                        "https://example.invalid/v1",
                        "--model",
                        "verify-model",
                    ]
                ),
                0,
            )
        with mock.patch("sys.stdout", io.StringIO()):
            self.assertEqual(
                cli_main(
                    [
                        "--home",
                        self.temp_dir.name,
                        "provider",
                        "--target",
                        "verification",
                        "--inherit-main",
                    ]
                ),
                0,
            )

        config_payload = read_json(self.app.paths.config_file, default={})
        self.assertTrue(config_payload["verification_provider"]["inherit_from_main"])

    def test_stage_input_file_copies_external_markdown_into_project_notes(self):
        external_holder = tempfile.TemporaryDirectory()
        self.addCleanup(external_holder.cleanup)
        source = Path(external_holder.name) / "problem.md"
        source.write_text("# Problem\n\nStudy the neural network function class.\n", encoding="utf-8")

        result = self.app.stage_input_file(str(source), project_slug="anderson_conjecture")

        self.assertTrue(result["staged"])
        self.assertEqual(
            result["relative_path"],
            "projects/anderson_conjecture/references/notes/problem.md",
        )
        staged_path = self.app.paths.home / result["relative_path"]
        self.assertTrue(staged_path.exists())
        self.assertIn("neural network function", staged_path.read_text(encoding="utf-8"))

    def test_cli_ask_input_file_injects_read_runtime_file_prompt(self):
        external_holder = tempfile.TemporaryDirectory()
        self.addCleanup(external_holder.cleanup)
        source = Path(external_holder.name) / "input.md"
        source.write_text("# Input\n\nUse this file as the research source.\n", encoding="utf-8")

        output = io.StringIO()
        with mock.patch("sys.stdout", output):
            exit_code = cli_main(
                [
                    "--home",
                    self.temp_dir.name,
                    "ask",
                    "--mode",
                    "chat",
                    "--project",
                    "general",
                    "--input-file",
                    str(source),
                    "Summarize it.",
                ]
            )

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("Read projects/general/references/notes/input.md with read_runtime_file.", rendered)
        self.assertTrue((self.app.paths.project_reference_notes_dir("general") / "input.md").exists())

    def test_skill_loads_are_audited(self):
        runtime = self.app.agent._build_runtime(
            mode=self.state.mode,
            project_slug=self.state.project_slug,
            session_id=self.state.session_id,
        )
        self.app.tool_manager.dispatch(
            "load_skill_definition",
            {"slug": "memory-hygiene"},
            runtime,
        )
        audit_entries = [
            json.loads(line)
            for line in self.app.paths.skills_audit_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertTrue(any(item["event"] == "skill_load" and item["slug"] == "memory-hygiene" for item in audit_entries))

    def test_knowledge_entries_use_structured_metadata_comments(self):
        self.app.execute_command(
            "/knowledge add Local Criterion | Local criteria often reduce the global statement to maximal ideals. | Standard reduction sketch.",
            self.state,
        )
        result = self.app.memory.knowledge_store.search("Local Criterion", project_slug="anderson_conjecture", limit=1)[0]
        markdown = self.app.memory.knowledge_store.entry_path(result["id"]).read_text(encoding="utf-8")

        self.assertIn("<!--", markdown)
        self.assertIn('"project_slug": "anderson_conjecture"', markdown)
        self.assertIn('"source_type": "manual"', markdown)

    def test_session_database_uses_wal_mode(self):
        with sqlite3.connect(str(self.app.paths.sessions_db)) as connection:
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(str(mode).lower(), "wal")

    def test_session_sqlite_stores_structured_conversation_events(self):
        provider = ScriptedProvider(
            [
                {
                    "response": ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                name="query_memory",
                                arguments={"query": "local criteria", "project_slug": "anderson_conjecture"},
                                call_id="call-1",
                            )
                        ]
                    )
                },
                {
                    "chunks": ["Stored it."],
                    "response": ProviderResponse(content="Stored it."),
                },
            ]
        )
        self.app.agent.provider = provider

        list(self.app.ask_stream("Remember: prefer local criteria.", self.state))
        events = self.app.session_store.get_conversation_events(self.state.session_id)
        event_kinds = [item["event_kind"] for item in events]

        self.assertIn("message", event_kinds)
        self.assertIn("assistant_tool_call", event_kinds)
        self.assertIn("tool_result", event_kinds)
