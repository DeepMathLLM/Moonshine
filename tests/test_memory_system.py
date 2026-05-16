"""Tests for Moonshine memory layers."""

from __future__ import annotations

import tempfile
import unittest

from moonshine.app import MoonshineApp


raise unittest.SkipTest("Legacy test module replaced by the refactored English test suite.")


class MemorySystemTestCase(unittest.TestCase):
    """Exercise the core layered memory workflow."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.app = MoonshineApp(home=self.temp_dir.name)
        self.state = self.app.start_shell_state(mode="research", project_slug="anderson_conjecture")

    def test_explicit_memory_updates_index(self):
        self.app.ask("记住：研究诺特环时优先考虑 Krull 维度。", self.state)

        explicit_text = self.app.memory.dynamic_store.read_file("feedback-explicit")
        index_text = self.app.paths.memory_index_file.read_text(encoding="utf-8")

        self.assertIn("Krull", explicit_text)
        self.assertIn("显式记忆", index_text)

    def test_session_search_and_recent_history_work(self):
        self.app.ask("当前项目是 Anderson 猜想的必要性方向。", self.state)
        self.app.ask("我偏好代数方法。", self.state)

        results = self.app.memory.session_store.search_messages("代数方法", project_slug="anderson_conjecture", limit=3)
        self.assertTrue(results)
        self.assertIn("代数方法", results[0]["content"])

    def test_knowledge_layer_accepts_manual_and_auto_entries(self):
        self.app.execute_command("/knowledge add Nakayama 引理 | 若 M = IM 且 I 在 Jacobson 根中，则 M = 0 | 标准证明略", self.state)
        results = self.app.memory.knowledge_store.search("Nakayama", project_slug="anderson_conjecture", limit=3)
        self.assertTrue(results)
        self.assertEqual(results[0]["title"], "Nakayama 引理")

    def test_auto_extraction_creates_project_preference_and_context_entries(self):
        self.app.ask("我偏好使用代数方法。当前项目是研究 Noetherian ring 的局部条件。", self.state)
        preference_text = self.app.memory.dynamic_store.read_file("user-preferences")
        project_text = self.app.memory.dynamic_store.read_file("project-context", project_slug="anderson_conjecture")

        self.assertIn("代数方法", preference_text)
        self.assertIn("Noetherian ring", project_text)


if __name__ == "__main__":
    unittest.main()
