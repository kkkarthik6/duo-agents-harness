import tempfile
import unittest
from pathlib import Path

from engine import history


class HistoryDurationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = Path(self.tmp) / "history.jsonl"

    def test_missing_file_gives_empty_medians(self):
        self.assertEqual(history.medians(self.path, "programming"), {})

    def test_durations_round_trip_by_task_type(self):
        history.record_durations(self.path, "programming", {"score": 12, "deep": 30})
        history.record_durations(self.path, "programming", {"score": 20})
        history.record_durations(self.path, "research", {"score": 99})
        self.assertEqual(history.medians(self.path, "programming"), {"score": 16, "deep": 30})
        self.assertEqual(history.medians(self.path, "research"), {"score": 99})



if __name__ == "__main__":
    unittest.main()
