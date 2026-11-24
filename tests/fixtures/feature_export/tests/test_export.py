import tempfile
import unittest
from pathlib import Path

from engine import coreapi
from features.feature_export import feature


class ExportTests(unittest.TestCase):
    def test_the_stage_writes_a_summary(self):
        folder = Path(tempfile.mkdtemp())
        run = coreapi.RunView.from_state({"id": "r1", "task": "make a pdf", "agents": ["claude"]})
        feature.ExportSummary().run(coreapi.Context("feature_export", run, folder))
        self.assertIn("make a pdf", (folder / "duo" / "features" / "feature_export" / "summary.md").read_text())

    def test_register_adds_the_stage_route_and_slot(self):
        api = coreapi.Api("feature_export")
        feature.register(api)
        self.assertEqual([s.name for s in api.stages], ["export_summary"])
        self.assertEqual(api.slots, [("home", "ui/home.js")])
