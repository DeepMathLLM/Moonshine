"""Regression tests for crash-safe tool execution journaling."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from moonshine.agent_runtime.execution_journal import (
    TOOL_EXECUTION_AMBIGUOUS,
    TOOL_EXECUTION_BLOCKED,
    TOOL_EXECUTION_FINISHED,
    TOOL_EXECUTION_STARTED,
)
from moonshine.model_tools import handle_function_calls
from moonshine.moonshine_constants import MoonshinePaths
from moonshine.providers import ProviderToolCall
from moonshine.storage.session_store import SessionStore


class ScriptedRegistry(object):
    """Minimal deterministic registry for execution-lifecycle tests."""

    def __init__(self, handlers):
        self.handlers = dict(handlers)
        self.dispatches = []

    def dispatch(self, name, arguments, runtime):
        self.dispatches.append((name, dict(arguments or {})))
        handler = self.handlers[name]
        return handler(runtime, **dict(arguments or {}))


class InterruptedToolRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.paths = MoonshinePaths(Path(self.temp_dir.name))
        self.store = SessionStore(self.paths)
        self.session_id = self.store.create_session("chat", "tool-recovery-test")

    def _runtime(self, store=None):
        return {
            "session_store": store or self.store,
            "session_id": self.session_id,
            "_tool_results_in_round": [],
        }

    def _execution_events(self, store=None):
        store = store or self.store
        return [
            item
            for item in store.get_conversation_events(self.session_id)
            if str(item.get("event_kind") or "").startswith("tool_execution_")
        ]

    def test_completed_call_is_terminal_before_later_call_is_interrupted(self):
        side_effects = []

        def complete(runtime, value):
            side_effects.append("complete:%s" % value)
            return {"value": value, "status": "done"}

        def interrupt(runtime):
            side_effects.append("interrupt-side-effect")
            raise KeyboardInterrupt("simulated process interruption")

        registry = ScriptedRegistry({"complete": complete, "interrupt": interrupt})
        runtime = self._runtime()
        calls = [
            ProviderToolCall(name="complete", arguments={"value": 7}, call_id="call-complete"),
            ProviderToolCall(name="interrupt", arguments={}, call_id="call-interrupt"),
        ]

        with self.assertRaises(KeyboardInterrupt):
            handle_function_calls(registry, calls, runtime)

        self.assertEqual(side_effects, ["complete:7", "interrupt-side-effect"])
        self.assertEqual([item["name"] for item in runtime["_tool_results_in_round"]], ["complete"])

        events = self._execution_events()
        complete_started = [
            item for item in events
            if item["event_kind"] == TOOL_EXECUTION_STARTED
            and item["payload"].get("call_id") == "call-complete"
        ]
        complete_finished = [
            item for item in events
            if item["event_kind"] == TOOL_EXECUTION_FINISHED
            and item["payload"].get("call_id") == "call-complete"
        ]
        interrupted_started = [
            item for item in events
            if item["event_kind"] == TOOL_EXECUTION_STARTED
            and item["payload"].get("call_id") == "call-interrupt"
        ]
        interrupted_ambiguous = [
            item for item in events
            if item["event_kind"] == TOOL_EXECUTION_AMBIGUOUS
            and item["payload"].get("call_id") == "call-interrupt"
        ]

        self.assertEqual(len(complete_started), 1)
        self.assertEqual(len(complete_finished), 1)
        self.assertEqual(
            complete_started[0]["payload"]["execution_id"],
            complete_finished[0]["payload"]["execution_id"],
        )
        self.assertEqual(complete_finished[0]["payload"]["outcome"], "ok")
        self.assertTrue(complete_finished[0]["payload"]["output_sha256"])
        self.assertIn('"status": "done"', complete_finished[0]["payload"]["output_preview"])
        self.assertNotIn("arguments", complete_started[0]["payload"])
        self.assertIn("arguments_sha256", complete_started[0]["payload"])

        self.assertEqual(len(interrupted_started), 1)
        self.assertEqual(len(interrupted_ambiguous), 1)
        self.assertEqual(
            interrupted_started[0]["payload"]["execution_id"],
            interrupted_ambiguous[0]["payload"]["execution_id"],
        )
        self.assertEqual(interrupted_ambiguous[0]["payload"]["state"], "ambiguous")
        self.assertEqual(self.store.get_session_meta(self.session_id)["status"], "interrupted")

    def test_restart_blocks_new_tool_dispatch_after_orphaned_execution_start(self):
        execution_id = "tool-exec-hard-crash"
        self.store.append_conversation_event(
            self.session_id,
            event_kind=TOOL_EXECUTION_STARTED,
            role="tool",
            content="Tool execution started before simulated hard crash",
            payload={
                "execution_id": execution_id,
                "tool": "external_side_effect",
                "call_id": "call-before-crash",
                "arguments_sha256": "deadbeef",
                "arguments_preview": '{"value": 1}',
                "turn_sequence": 0,
            },
        )

        restarted_store = SessionStore(self.paths)
        dispatch_count = []

        def must_not_run(runtime):
            dispatch_count.append(1)
            return {"unexpected": True}

        registry = ScriptedRegistry({"must_not_run": must_not_run})
        runtime = self._runtime(restarted_store)
        results = handle_function_calls(
            registry,
            [ProviderToolCall(name="must_not_run", arguments={}, call_id="call-after-restart")],
            runtime,
        )

        self.assertEqual(dispatch_count, [])
        self.assertEqual(registry.dispatches, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["output"]["status"], "blocked_interrupted_execution")
        self.assertIn(execution_id, results[0]["error"])
        self.assertEqual(restarted_store.get_session_meta(self.session_id)["status"], "interrupted")
        blocked = [
            item for item in self._execution_events(restarted_store)
            if item["event_kind"] == TOOL_EXECUTION_BLOCKED
        ]
        self.assertEqual(len(blocked), 1)
        self.assertIn(execution_id, blocked[0]["payload"]["ambiguous_execution_ids"])

    def test_completed_resume_turn_does_not_hide_older_interrupted_tool_turn(self):
        # Turn 1 starts and executes a tool, but the process dies before its
        # turn_completed record is written.
        self.store.append_turn_event(
            self.session_id,
            {"type": "turn_started", "text": "first", "created_at": "2026-09-05T00:00:00Z"},
        )
        execution_id = "tool-exec-finished-before-crash"
        self.store.append_conversation_event(
            self.session_id,
            event_kind=TOOL_EXECUTION_STARTED,
            role="tool",
            content="Tool execution started",
            payload={
                "execution_id": execution_id,
                "tool": "external_side_effect",
                "call_id": "call-first-turn",
                "turn_sequence": 1,
                "arguments_sha256": "abc",
                "arguments_preview": "{}",
            },
        )
        self.store.append_conversation_event(
            self.session_id,
            event_kind=TOOL_EXECUTION_FINISHED,
            role="tool",
            content="Tool execution finished",
            payload={
                "execution_id": execution_id,
                "tool": "external_side_effect",
                "call_id": "call-first-turn",
                "turn_sequence": 1,
                "outcome": "ok",
                "output_sha256": "def",
                "output_preview": '{"ok": true}',
            },
        )

        # Turn 2 is a resumed, tool-free turn that completes. LIFO pairing must
        # close turn 2, not accidentally consume the older interrupted turn 1.
        self.store.append_turn_event(
            self.session_id,
            {"type": "turn_started", "text": "resume-one", "created_at": "2026-09-05T00:00:00Z"},
        )
        self.store.append_turn_event(
            self.session_id,
            {"type": "turn_completed", "text": "resume-one done", "created_at": "2026-09-05T00:00:00Z"},
        )
        # Turn 3 begins and attempts another tool call.
        self.store.append_turn_event(
            self.session_id,
            {"type": "turn_started", "text": "resume-two", "created_at": "2026-09-05T00:00:00Z"},
        )

        dispatch_count = []

        def must_not_run(runtime):
            dispatch_count.append(1)
            return {"unexpected": True}

        registry = ScriptedRegistry({"must_not_run": must_not_run})
        results = handle_function_calls(
            registry,
            [ProviderToolCall(name="must_not_run", arguments={}, call_id="call-resumed")],
            self._runtime(),
        )

        self.assertEqual(dispatch_count, [])
        self.assertEqual(registry.dispatches, [])
        self.assertEqual(results[0]["output"]["status"], "blocked_interrupted_execution")
        self.assertIn("prior tool-bearing turn was interrupted", results[0]["error"])
        blocker = results[0]["output"]["ambiguous_executions"][0]
        self.assertEqual(blocker["state"], "interrupted_turn")
        self.assertEqual(blocker["turn_sequence"], 1)

    def test_ordinary_tool_error_is_terminal_and_does_not_poison_future_dispatch(self):
        def fail(runtime):
            raise RuntimeError("deterministic tool failure")

        failing_registry = ScriptedRegistry({"fail": fail})
        first = handle_function_calls(
            failing_registry,
            [ProviderToolCall(name="fail", arguments={}, call_id="call-fail")],
            self._runtime(),
        )
        self.assertEqual(len(first), 1)
        self.assertIn("deterministic tool failure", first[0]["error"])

        finish_events = [
            item for item in self._execution_events()
            if item["event_kind"] == TOOL_EXECUTION_FINISHED
            and item["payload"].get("call_id") == "call-fail"
        ]
        self.assertEqual(len(finish_events), 1)
        self.assertEqual(finish_events[0]["payload"]["outcome"], "error")

        later_effects = []

        def succeed(runtime):
            later_effects.append("ran")
            return {"ok": True}

        succeeding_registry = ScriptedRegistry({"succeed": succeed})
        second = handle_function_calls(
            succeeding_registry,
            [ProviderToolCall(name="succeed", arguments={}, call_id="call-succeed")],
            self._runtime(),
        )
        self.assertEqual(later_effects, ["ran"])
        self.assertIsNone(second[0]["error"])

    def test_dispatch_without_session_store_keeps_legacy_result_shape(self):
        effects = []

        def succeed(runtime, value):
            effects.append(value)
            return {"value": value}

        registry = ScriptedRegistry({"succeed": succeed})
        runtime = {"_tool_results_in_round": []}
        results = handle_function_calls(
            registry,
            [ProviderToolCall(name="succeed", arguments={"value": 3}, call_id="call-no-store")],
            runtime,
        )

        self.assertEqual(effects, [3])
        self.assertEqual(results[0]["output"], {"value": 3})
        self.assertIsNone(results[0]["error"])
        self.assertEqual(
            set(results[0]),
            {"name", "call_id", "arguments", "output", "error"},
        )


if __name__ == "__main__":
    unittest.main()
