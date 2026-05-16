"""Tests for Moonshine command handling."""

from __future__ import annotations

import tempfile
import unittest

from moonshine.app import MoonshineApp


raise unittest.SkipTest("Legacy test module replaced by the refactored English test suite.")


class CommandHandlingTestCase(unittest.TestCase):
    """Validate REPL command behavior."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app = MoonshineApp(home=self.temp_dir.name)
        self.state = self.app.start_shell_state(mode="chat", project_slug="general")

    def test_mode_switch_and_project_switch(self):
        result = self.app.execute_command("/mode research", self.state)
        self.assertIn("research", result)
        self.assertEqual(self.state.mode, "research")

        result = self.app.execute_command("/project algebra_lab", self.state)
        self.assertIn("algebra_lab", result)
        self.assertEqual(self.state.project_slug, "algebra_lab")

    def test_remember_and_promote(self):
        remember_result = self.app.execute_command("/remember 研究反例时优先看商环。", self.state)
        self.assertIn("已写入显式记忆", remember_result)

        review = self.app.execute_command("/memory review", self.state)
        self.assertIn("建议提升到静态规则", review)

        entries = self.app.memory.dynamic_store.search("商环", limit=1)
        self.assertTrue(entries)
        promote_result = self.app.execute_command("/memory promote %s" % entries[0].slug, self.state)
        self.assertIn("已提升到静态规则", promote_result)

    def test_context_command_returns_summary(self):
        self.app.ask("记住：构造反例时优先考虑 Noetherian 条件。", self.state)
        result = self.app.execute_command("/context", self.state)
        self.assertIn("Moonshine 当前上下文", result)


if __name__ == "__main__":
    unittest.main()
