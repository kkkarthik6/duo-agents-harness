"""Task contracts, bounded recovery and independent anonymous reviews."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import orchestration as orch, panes, phases
from tests.test_phases import st, opts


class ContractTests(unittest.TestCase):
    def test_outputs_must_exist_belong_to_task_and_stay_inside_project(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "paper.md").write_text("A real deliverable")
            data = dict(task_id="T1", complete=True, remaining=[], outputs=["paper.md"])
            self.assertEqual(orch.output_errors(root, data, "T1", ["paper.md"]), [])
            self.assertTrue(orch.output_errors(root, data, "T2"))
            self.assertTrue(orch.output_errors(root, dict(data, outputs=["absent.md"]), "T1"))
            self.assertTrue(orch.output_errors(root, dict(data, outputs=["../outside"]), "T1"))
            self.assertTrue(orch.output_errors(root, data, "T1", ["src/"]))
            self.assertTrue(orch.output_errors(root, dict(data, remaining=["unfinished"]), "T1"))

    def test_review_packet_excludes_history_routing_and_symlinks(self):
        with tempfile.TemporaryDirectory() as d:
            root, packet = Path(d) / "project", Path(d) / "packet"
            root.mkdir()
            for rel, text in {"paper.md": "Built by Claude with claude-sonnet-5-5",
                              ".duo/run/chat.md": "Builder identity", "duo/agents.md": "Private routing",
                              "duo/tasks/T1.md": "agent: claude", ".claude/settings.json": "Private settings"}.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
            (root / "secret-link").symlink_to(root / ".duo/run/chat.md")
            state = {"agents": ["claude", "codex"]}
            orch.review_packet(root, packet, state, "T1", "Write the paper")
            self.assertFalse((packet / ".duo").exists())
            self.assertFalse((packet / "duo/agents.md").exists())
            self.assertFalse((packet / ".claude").exists())
            self.assertFalse((packet / "secret-link").exists())
            self.assertNotIn("claude", (packet / "paper.md").read_text().lower())
            self.assertEqual((packet / "duo/tasks/T1.md").read_text(), "Write the paper")
            saved = json.loads(json.dumps(state))
            self.assertEqual(orch.alias(saved, "claude"), orch.alias(state, "claude"))
            self.assertNotEqual(orch.alias(saved, "claude"), orch.alias(saved, "codex"))

    def test_blinding_preserves_model_names_in_scientific_content(self):
        state = {"agents": ["claude", "codex"]}
        text = "GPT-3 is a baseline.\nBuilt by Claude with gpt-6.1-sol\n"
        clean = orch.strip_attribution(state, text)
        self.assertIn("GPT-3 is a baseline.", clean)
        self.assertNotIn("Built by Claude", clean)




class TransportTests(unittest.TestCase):
    def test_failed_model_switch_does_not_send_task_to_wrong_screen(self):
        driver = panes.TmuxDriver()
        driver.panes["claude"] = "%1"
        with mock.patch.object(panes.panectl, "wait_idle", return_value=(True, ">")), \
                mock.patch.object(panes.panectl, "command", return_value=(False, "Select model")), \
                mock.patch.object(panes, "send_text") as send:
            with self.assertRaises(RuntimeError):
                driver.send("claude", "Build now", model="model-id")
        send.assert_not_called()
