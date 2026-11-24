import unittest
from unittest import mock

from engine import models, session as sessmod


class StageModelTests(unittest.TestCase):
    def test_programming_builder_uses_a_large_model(self):
        self.assertEqual(models.model_for("claude", "build", "programming"), "claude-opus-5-5")

    def test_research_builder_uses_a_large_model(self):
        self.assertEqual(models.model_for("claude", "build", "research"), "claude-opus-5-5")

    def test_codex_scores_with_a_smaller_model(self):
        self.assertEqual(models.model_for("codex", "score", "programming"), "gpt-6-luna")

    def test_codex_verifies_with_terra(self):
        self.assertEqual(models.model_for("codex", "verify"), "gpt-6.1-terra")

    def test_config_override_wins_over_the_table(self):
        over = {"codex": {"score": "gpt-5.6-terra"}}
        self.assertEqual(models.model_for("codex", "score", None, over), "gpt-5.6-terra")

    def test_task_type_override_in_config_wins(self):
        over = {"programming": {"claude": {"build": "claude-opus-5-5"}}}
        self.assertEqual(models.model_for("claude", "build", "programming", over), "claude-opus-5-5")

    def test_switch_command_for_claude(self):
        self.assertEqual(models.switch_command("claude", "claude-opus-5-5"), "/model claude-opus-5-5")



if __name__ == "__main__":
    unittest.main()
