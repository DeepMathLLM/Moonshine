"""Command handling tests for the refactored Moonshine CLI."""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from moonshine.app import MoonshineApp
from moonshine.utils import read_json


class CommandHandlingTestCase(unittest.TestCase):
    """Validate slash command behavior against the refactored app."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app = MoonshineApp(home=self.temp_dir.name)
        self.state = self.app.start_shell_state(mode="chat", project_slug="general")

    def test_mode_switch_and_project_switch(self):
        result = self.app.execute_command("/mode research", self.state)
        self.assertIn("research", result)
        self.assertEqual(self.state.mode, "research")

        previous_session_id = self.state.session_id
        result = self.app.execute_command("/project algebra_lab", self.state)
        self.assertIn("algebra_lab", result)
        self.assertIn("started new session", result)
        self.assertEqual(self.state.project_slug, "algebra_lab")
        self.assertNotEqual(self.state.session_id, previous_session_id)
        previous_meta = self.app.session_store.get_session_meta(previous_session_id)
        self.assertEqual(previous_meta.get("status"), "closed")

    def test_memory_write_remember_review_and_promote(self):
        remember_result = self.app.execute_command("/memory write Check quotient rings first when constructing counterexamples.", self.state)
        self.assertIn("Stored explicit memory", remember_result)

        review = self.app.execute_command("/remember", self.state)
        self.assertIn("Memory review report", review)

        entries = self.app.memory.dynamic_store.search("quotient rings", limit=1)
        self.assertTrue(entries)
        promote_result = self.app.execute_command("/memory promote %s" % entries[0].slug, self.state)
        self.assertIn("Promoted memory into", promote_result)

    def test_context_and_skills_commands_return_summaries(self):
        self.app.ask("Remember: prioritize Noetherian conditions when constructing counterexamples.", self.state)

        context_result = self.app.execute_command("/context", self.state)
        skills_result = self.app.execute_command("/skills", self.state)
        skill_body_result = self.app.execute_command("/skills show memory-hygiene", self.state)
        tools_result = self.app.execute_command("/tools", self.state)
        agent_result = self.app.execute_command("/agent", self.state)
        mcp_result = self.app.execute_command("/mcp", self.state)

        self.assertIn("Moonshine context summary", context_result)
        self.assertIn("Builtin", skills_result)
        self.assertIn("Prefer summaries with traceable sources over raw transcript dumps", skill_body_result)
        self.assertIn("config.yaml loaded: yes", context_result)
        self.assertIn("load_tool_definition", tools_result)
        self.assertIn("moonshine-core", agent_result)
        self.assertIn("reference-library", mcp_result)

    def test_mcp_tavily_set_key_command_stores_credential_without_echoing_secret(self):
        with mock.patch.dict("os.environ", {"TAVILY_API_KEY": ""}):
            result = self.app.execute_command("/mcp tavily set-key tvly-command-value", self.state)

        payload = read_json(self.app.paths.credentials_file, default={})
        descriptor = (self.app.paths.mcp_servers_dir / "tavily.md").read_text(encoding="utf-8")

        self.assertIn("Stored Tavily API key", result)
        self.assertNotIn("tvly-command-value", result)
        self.assertEqual(payload["secrets"]["TAVILY_API_KEY"], "tvly-command-value")
        self.assertIn('"enabled": true', descriptor)
