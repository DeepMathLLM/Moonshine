"""Regression test: reaching the tool-round limit must not leave orphan tool_calls.

Root cause (DeepSeek-compatible OpenAI endpoints): when the model returns
tool_calls in a round where ``max_tool_rounds`` is already exhausted, Moonshine
appended the assistant tool-call message to the provider transcript and then
``break`` before executing the tools / appending the matching tool messages.
The next provider request then contained an assistant message with ``tool_calls``
that had no following tool messages, which strict OpenAI-compatible servers
reject with HTTP 400 ("insufficient tool messages following tool_calls").
"""

from __future__ import annotations

import tempfile
import unittest

from moonshine.app import MoonshineApp
from moonshine.providers import ProviderResponse, ProviderStreamEvent, ProviderToolCall


class ScriptedProvider(object):
    """Minimal scripted provider used to drive the agent loop without a live model."""

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


def _orphan_tool_call_groups(messages):
    """Return (assistant_index, tool_call_id) groups that lack a following tool message."""
    orphans = []
    for i, message in enumerate(messages):
        if message.get("role") != "assistant" or not message.get("tool_calls"):
            continue
        following_tool_ids = set()
        j = i + 1
        while j < len(messages) and messages[j].get("role") == "tool":
            following_tool_ids.add(messages[j].get("tool_call_id"))
            j += 1
        for tool_call in message["tool_calls"]:
            call_id = tool_call.get("id")
            if call_id not in following_tool_ids:
                orphans.append((i, call_id))
    return orphans


class ProviderMessageSanitizerTest(unittest.TestCase):
    """Unit tests for the send-time sanitizer that guarantees protocol validity."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp_dir.cleanup)
        self.app = MoonshineApp(home=self.temp_dir.name)
        self.state = self.app.start_shell_state(mode="research", project_slug="anderson_conjecture")

    def _sanitize(self, messages):
        return self.app.agent._sanitize_provider_messages(messages)

    def test_well_formed_tool_pairing_is_unchanged(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "let me check", "tool_calls": [{"id": "call-a", "function": {}}]},
            {"role": "tool", "tool_call_id": "call-a", "content": "result"},
            {"role": "assistant", "content": "done"},
        ]
        cleaned = self._sanitize(messages)
        self.assertEqual(cleaned, messages)

    def test_orphan_tool_calls_are_demoted_to_plain_text(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "trying a tool", "tool_calls": [{"id": "call-1", "function": {}}]},
            {"role": "user", "content": "next turn, no tool result ever arrived"},
        ]
        cleaned = self._sanitize(messages)
        assistant = cleaned[1]
        self.assertEqual(assistant["role"], "assistant")
        self.assertNotIn("tool_calls", assistant)
        self.assertIn("trying a tool", assistant["content"])

    def test_orphan_tool_results_are_folded_into_the_demoted_message(self):
        messages = [
            {"role": "assistant", "content": "checking", "tool_calls": [
                {"id": "call-a", "function": {}}, {"id": "call-b", "function": {}}]},
            {"role": "tool", "tool_call_id": "call-a", "content": "result a"},
            {"role": "user", "content": "continue"},
        ]
        cleaned = self._sanitize(messages)
        self.assertEqual(len(cleaned), 2)  # demoted assistant + trailing user; orphan tool folded away
        self.assertEqual(cleaned[0]["role"], "assistant")
        self.assertNotIn("tool_calls", cleaned[0])
        self.assertIn("result a", cleaned[0]["content"])
        self.assertEqual(cleaned[1]["role"], "user")

    def test_orphan_with_no_content_gets_placeholder_text(self):
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call-x", "function": {}}]},
            {"role": "user", "content": "go on"},
        ]
        cleaned = self._sanitize(messages)
        self.assertEqual(cleaned[0]["role"], "assistant")
        self.assertNotIn("tool_calls", cleaned[0])
        self.assertTrue(cleaned[0]["content"])

    def test_empty_input_returns_empty(self):
        self.assertEqual(self._sanitize([]), [])


class ToolRoundLimitOrphanRegressionTest(unittest.TestCase):
    """Ensure tool-round-limit finalization never produces orphan tool_calls."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp_dir.cleanup)
        self.app = MoonshineApp(home=self.temp_dir.name)
        self.state = self.app.start_shell_state(mode="research", project_slug="anderson_conjecture")

    def _assert_no_orphan_tool_calls_across_provider_calls(self, provider):
        """Assert every provider call's messages keep assistant tool_calls paired."""
        self.assertTrue(provider.calls, "provider was never called")
        for call_index, call in enumerate(provider.calls):
            orphans = _orphan_tool_call_groups(call["messages"])
            self.assertEqual(
                orphans,
                [],
                "provider call %s sent orphan tool_calls (DeepSeek returns HTTP 400 for these): %s"
                % (call_index, orphans),
            )

    def test_tool_round_limit_does_not_leave_orphan_tool_calls(self):
        self.app.config.agent.max_tool_rounds = 1  # exhaust after the first tool execution
        self.app.config.agent.max_model_rounds = 6

        provider = ScriptedProvider(
            [
                # round 1: a valid tool call -> executes (tool_rounds 0 -> 1, cap reached).
                {
                    "response": ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                name="query_memory",
                                arguments={"query": "Krull dimension first", "project_slug": "anderson_conjecture"},
                                call_id="call-1",
                            )
                        ]
                    )
                },
                # round 2: the model wants to call a tool again, but the tool-round cap is hit.
                # Before the fix, the assistant tool-call message was appended and then dropped
                # without a following tool message -> orphan -> HTTP 400 on the next request.
                {
                    "response": ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                name="query_memory",
                                arguments={"query": "local methods", "project_slug": "anderson_conjecture"},
                                call_id="call-2",
                            )
                        ]
                    )
                },
                # finalization pass: plain text, no tools.
                {
                    "chunks": ["Finalized the run."],
                    "response": ProviderResponse(content="Finalized the run."),
                },
            ]
        )
        self.app.agent.provider = provider

        events = list(self.app.ask_stream("Run the tool until the round limit.", self.state))

        self.assertTrue(any("tool round limit" in event.text.lower() for event in events if event.type == "status"))
        self._assert_no_orphan_tool_calls_across_provider_calls(provider)
        self.assertEqual(events[-1].text, "Finalized the run.")

    def test_multiple_parallel_tool_calls_stay_paired(self):
        """Sanity: parallel tool_calls in one response all get matching tool messages."""
        self.app.config.agent.max_tool_rounds = 2
        self.app.config.agent.max_model_rounds = 6

        provider = ScriptedProvider(
            [
                {
                    "response": ProviderResponse(
                        tool_calls=[
                            ProviderToolCall(
                                name="query_memory",
                                arguments={"query": "a", "project_slug": "anderson_conjecture"},
                                call_id="call-a",
                            ),
                            ProviderToolCall(
                                name="read_runtime_file",
                                arguments={"relative_path": "workspace/problem.md"},
                                call_id="call-b",
                            ),
                        ]
                    )
                },
                {
                    "response": ProviderResponse(content="Both tools ran."),
                },
            ]
        )
        self.app.agent.provider = provider

        events = list(self.app.ask_stream("Call two tools.", self.state))
        self.assertEqual(events[-1].text, "Both tools ran.")
        self._assert_no_orphan_tool_calls_across_provider_calls(provider)


if __name__ == "__main__":
    unittest.main()
