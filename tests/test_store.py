import json
import tempfile
import unittest
from pathlib import Path

from engine import store


def bundle(root):
    src = Path(root) / "bundle"
    (src / "engine").mkdir(parents=True)
    (src / "engine" / "x.py").write_text("v1")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "junk.pyc").write_text("x")
    return src


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = Path(self.tmp) / "Duo"
        self.src = bundle(self.tmp)

    def test_first_install_copies_source_and_records_v1(self):
        data = store.install(self.src, self.base)
        self.assertEqual(data["current"], "source")
        self.assertEqual((self.base / "source" / "engine" / "x.py").read_text(), "v1")
        self.assertFalse((self.base / "source" / "__pycache__").exists())
        self.assertTrue((self.base / "versions.json").exists())

    def test_install_twice_keeps_history(self):
        store.install(self.src, self.base)
        store.clone("source", "first change", self.base)
        store.install(self.src, self.base)
        self.assertEqual(len(store.load(self.base)["versions"]), 2)

    def test_clone_records_feedback_and_becomes_current(self):
        store.install(self.src, self.base)
        v = store.clone("source", "Show time left on the progress bar", self.base)
        self.assertEqual(v["id"], "v2")
        self.assertEqual(v["feedback"], "Show time left on the progress bar")
        self.assertEqual(store.load(self.base)["current"], "v2")
        self.assertEqual(store.current_path(self.base), self.base / "clones" / "v2")

    def test_clone_copies_the_source_version_not_the_current_one(self):
        store.install(self.src, self.base)
        store.clone("source", "change one", self.base)
        (self.base / "clones" / "v2" / "engine" / "x.py").write_text("edited")
        v3 = store.clone("source", "change two", self.base, make_current=False)
        self.assertEqual((Path(v3["path"]) / "engine" / "x.py").read_text(), "v1")
        self.assertEqual(store.load(self.base)["current"], "v2")

    def test_switch_moves_current_and_keeps_every_version(self):
        store.install(self.src, self.base)
        store.clone("source", "change one", self.base)
        path = store.switch("source", self.base)
        self.assertEqual(path, self.base / "source")
        data = store.load(self.base)
        self.assertEqual(data["current"], "source")
        self.assertEqual(len(data["versions"]), 2)

    def test_switch_to_unknown_version_is_refused(self):
        store.install(self.src, self.base)
        with self.assertRaisesRegex(ValueError, "No version"):
            store.switch("v9", self.base)

    def test_switch_to_a_version_whose_folder_is_gone_is_refused(self):
        store.install(self.src, self.base)
        store.clone("source", "change one", self.base)
        store.switch("source", self.base)
        import shutil
        shutil.rmtree(self.base / "clones" / "v2")
        with self.assertRaisesRegex(ValueError, "missing its folder"):
            store.switch("v2", self.base)

    def test_empty_feedback_is_refused(self):
        store.install(self.src, self.base)
        with self.assertRaisesRegex(ValueError, "Write the feedback"):
            store.clone("source", "   ", self.base)

    def test_versions_json_is_valid_json_with_current_and_history(self):
        store.install(self.src, self.base)
        store.clone("source", "change one", self.base)
        raw = json.loads((self.base / "versions.json").read_text())
        self.assertEqual(raw["current"], "v2")
        self.assertEqual([v["id"] for v in raw["versions"]], ["source", "v2"])


class SyncSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = Path(self.tmp) / "Duo"
        self.src = bundle(self.tmp)

    def test_unchanged_bundle_is_not_copied_again(self):
        store.install(self.src, self.base)
        self.assertFalse(store.sync_source(self.src, self.base))

    def test_an_edited_bundle_updates_source_and_keeps_clones(self):
        store.install(self.src, self.base)
        store.clone("source", "change one", self.base)
        (self.src / "engine" / "x.py").write_text("v2")
        self.assertTrue(store.sync_source(self.src, self.base))
        self.assertEqual((self.base / "source" / "engine" / "x.py").read_text(), "v2")
        data = store.load(self.base)
        self.assertEqual(len(data["versions"]), 2)
        self.assertEqual((self.base / "clones" / "v2" / "engine" / "x.py").read_text(), "v1")
        self.assertIn("updated", data["versions"][0])

    def test_pycache_changes_do_not_count_as_a_change(self):
        store.install(self.src, self.base)
        (self.src / "__pycache__" / "junk.pyc").write_text("changed")
        self.assertFalse(store.sync_source(self.src, self.base))


if __name__ == "__main__":
    unittest.main()


class VersionNumberTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.base = Path(self.tmp) / "Duo"
        self.src = bundle(self.tmp)
        store.install(self.src, self.base)

    def test_bump_moves_one_part_and_resets_the_ones_after_it(self):
        self.assertEqual(store.bump("2.1.3", "minor"), "2.2.0")
        self.assertEqual(store.bump("2.1.3", "major"), "3.0.0")
        self.assertEqual(store.bump("2.1.3", "patch"), "2.1.4")

    def test_bump_refuses_unknown_kinds_and_bad_numbers(self):
        with self.assertRaises(ValueError):
            store.bump("2.1.3", "huge")
        with self.assertRaises(ValueError):
            store.bump("v3", "minor")

    def test_installed_copy_is_2_0_0(self):
        self.assertEqual(store.load(self.base)["versions"][0]["number"], "2.0.0")

    def test_minor_feedback_gives_the_next_minor_number(self):
        v = store.clone("source", "add a button", self.base, kind="minor")
        self.assertEqual(v["number"], "2.1.0")
        self.assertEqual(v["kind"], "minor")

    def test_major_feedback_after_a_minor_gives_the_next_major_number(self):
        store.clone("source", "first change", self.base, kind="minor")
        v = store.clone("source", "rework the flow", self.base, kind="major")
        self.assertEqual(v["number"], "3.0.0")

    def test_two_feedbacks_from_the_same_version_never_share_a_number(self):
        a = store.clone("source", "one", self.base, make_current=False, kind="minor")
        b = store.clone("source", "two", self.base, make_current=False, kind="minor")
        self.assertNotEqual(a["number"], b["number"])

    def test_clone_refuses_an_unknown_update_type(self):
        with self.assertRaises(ValueError):
            store.clone("source", "change", self.base, kind="patch")
        self.assertEqual(len(store.load(self.base)["versions"]), 1)

    def test_old_entries_without_numbers_are_numbered_in_order(self):
        data = store.load(self.base)
        data["versions"].append({"id": "v1", "path": "x", "origin": "source", "feedback": "old"})
        data["versions"].append({"id": "v2", "path": "y", "origin": "source", "feedback": "older"})
        for v in data["versions"]:
            v.pop("number", None)
        store._write_json(store._manifest(self.base), data)
        numbers = [v["number"] for v in store.load(self.base)["versions"]]
        self.assertEqual(numbers, ["2.0.0", "2.1.0", "2.2.0"])

    def test_summary_gives_current_number_and_the_choices_for_the_next_one(self):
        store.clone("source", "change", self.base, kind="minor")
        s = store.summary(self.base)
        self.assertEqual(s["current_number"], "2.1.0")
        self.assertEqual(s["next"], {"minor": "2.2.0", "major": "3.0.0"})

    def test_switching_moves_the_current_number(self):
        v = store.clone("source", "change", self.base, kind="minor")
        store.switch(v["id"], self.base)
        self.assertEqual(store.summary(self.base)["current_number"], "2.1.0")


class StableNumberTests(unittest.TestCase):
    def test_numbers_given_to_old_entries_are_saved_and_do_not_move(self):
        tmp = tempfile.mkdtemp()
        base = Path(tmp) / "Duo"
        store.install(bundle(tmp), base)
        data = json.loads((base / "versions.json").read_text())
        data["versions"][0].pop("number")
        data["versions"].append({"id": "v2", "path": "x", "origin": "source", "feedback": "old"})
        (base / "versions.json").write_text(json.dumps(data))
        store.load(base)
        data = json.loads((base / "versions.json").read_text())
        data["versions"][0]["number"] = "3.0.0"
        (base / "versions.json").write_text(json.dumps(data))
        self.assertEqual([v["number"] for v in store.load(base)["versions"]], ["3.0.0", "2.1.0"])
