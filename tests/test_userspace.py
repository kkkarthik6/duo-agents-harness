import json
import tempfile
import unittest
from pathlib import Path

from engine import userspace


class Base(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp()) / "Duo"
        userspace.ensure(self.base)

    def ready(self, kind="minor", text="add a thing"):
        v = userspace.clone(self.base, text, kind)
        Path(v["path"], "features", "thing").mkdir(parents=True)
        Path(v["path"], "features", "thing", "feature.json").write_text('{"name": "thing"}')
        userspace.update(self.base, v["id"], status="ready")
        return v


class LayoutTests(Base):
    def test_fresh_user_layer_is_1_0_0_with_an_empty_features_folder(self):
        u = userspace.user_dir(self.base)
        self.assertEqual((u / "VERSION").read_text().strip(), "1.0.0")
        self.assertTrue((u / "features" / "__init__.py").exists())
        self.assertTrue((u / "instructions.md").exists())
        self.assertEqual(json.loads((u / "contract.json").read_text())["version"], "1.0.0")
        self.assertEqual(userspace.summary(self.base)["number"], "1.0.0")

    def test_ensure_twice_keeps_what_is_there(self):
        (userspace.user_dir(self.base) / "instructions.md").write_text("mine")
        userspace.ensure(self.base)
        self.assertEqual((userspace.user_dir(self.base) / "instructions.md").read_text(), "mine")


class NumberTests(Base):
    def test_major_and_minor_clones_follow_the_user_version(self):
        a = userspace.clone(self.base, "big change", "major")
        b = userspace.clone(self.base, "small change", "minor")
        c = userspace.clone(self.base, "another big one", "major")
        self.assertEqual([a["number"], b["number"], c["number"]], ["1.1.0", "1.1.1", "1.2.0"])

    def test_first_minor_clone_is_v_0_1(self):
        self.assertEqual(userspace.clone(self.base, "tweak", "minor")["number"], "1.0.1")

    def test_summary_shows_the_next_numbers(self):
        userspace.clone(self.base, "big", "major")
        self.assertEqual(userspace.summary(self.base)["next"], {"major": "1.2.0", "minor": "1.1.1"})

    def test_unknown_kind_and_empty_text_are_refused(self):
        with self.assertRaises(ValueError):
            userspace.clone(self.base, "x", "patch")
        with self.assertRaises(ValueError):
            userspace.clone(self.base, "  ", "minor")

    def test_a_repair_number_is_the_next_free_minor(self):
        a = userspace.clone(self.base, "big", "major")
        userspace.clone(self.base, "small", "minor")
        self.assertEqual(userspace.repair_number(self.base, a["id"]), "1.1.2")

    def test_a_clone_copies_the_user_layer_only(self):
        (userspace.user_dir(self.base) / "instructions.md").write_text("keep tests short")
        v = userspace.clone(self.base, "x", "minor")
        self.assertEqual(Path(v["path"], "instructions.md").read_text(), "keep tests short")
        self.assertFalse(Path(v["path"], "engine").exists())
        self.assertTrue(str(v["path"]).startswith(str(self.base / "versions")))


class RollUpTests(Base):
    def test_roll_up_moves_the_clone_into_the_user_layer_and_raises_v(self):
        v = self.ready()
        new = userspace.roll_up(self.base, v["id"])
        self.assertEqual(new, "2.0.0")
        u = userspace.user_dir(self.base)
        self.assertTrue((u / "features" / "thing" / "feature.json").exists())
        self.assertEqual((u / "VERSION").read_text().strip(), "2.0.0")
        e = userspace.entry(self.base, v["id"])
        self.assertEqual((e["status"], e["rolled_into"]), ("rolled_up", "2.0.0"))

    def test_only_a_ready_clone_can_be_rolled_up(self):
        v = userspace.clone(self.base, "x", "minor")
        with self.assertRaises(ValueError):
            userspace.roll_up(self.base, v["id"])

    def test_a_clone_from_an_older_user_version_is_refused(self):
        old = self.ready(text="first")
        other = self.ready(text="second")
        userspace.roll_up(self.base, old["id"])
        with self.assertRaises(ValueError) as cm:
            userspace.roll_up(self.base, other["id"])
        self.assertIn("1.0.0", str(cm.exception))
        self.assertIn("2.0.0", str(cm.exception))

    def test_new_clones_after_a_roll_up_use_the_new_v(self):
        userspace.roll_up(self.base, self.ready()["id"])
        self.assertEqual(userspace.clone(self.base, "next", "major")["number"], "2.1.0")

    def test_go_back_restores_the_previous_user_layer(self):
        v = self.ready()
        userspace.roll_up(self.base, v["id"])
        self.assertEqual(userspace.go_back(self.base), "1.0.0")
        u = userspace.user_dir(self.base)
        self.assertFalse((u / "features" / "thing").exists())
        self.assertEqual(userspace.summary(self.base)["number"], "1.0.0")

    def test_go_back_with_nothing_to_go_back_to_is_refused(self):
        with self.assertRaises(ValueError):
            userspace.go_back(self.base)

    def test_an_auto_roll_up_is_marked(self):
        v = self.ready()
        userspace.roll_up(self.base, v["id"], auto=True)
        self.assertTrue(userspace.load(self.base)["rolled"][-1]["auto_fix"])


class LegacyTests(unittest.TestCase):
    def test_an_existing_install_starts_at_its_current_legacy_number(self):
        base = Path(tempfile.mkdtemp()) / "Duo"
        base.mkdir()
        (base / "versions.json").write_text(json.dumps({"current": "source", "versions": [
            {"id": "source", "number": "3.0.0", "path": str(base / "source")},
            {"id": "v2", "number": "2.1.0", "path": str(base / "clones" / "v2")}]}))
        userspace.migrate_legacy(base)
        self.assertEqual(userspace.summary(base)["number"], "3.0.0")
        self.assertIn("v2", (base / "versions.json").read_text())       # the legacy file is not changed

    def test_a_fresh_install_starts_at_1_0_0(self):
        base = Path(tempfile.mkdtemp()) / "Duo"
        userspace.migrate_legacy(base)
        self.assertEqual(userspace.summary(base)["number"], "1.0.0")


if __name__ == "__main__":
    unittest.main()


class TryTests(Base):
    def test_use_a_ready_version_without_changing_your_duo(self):
        v = self.ready()
        userspace.try_version(self.base, v["id"])
        self.assertEqual(userspace.active_dir(self.base), Path(v["path"]))
        self.assertEqual(userspace.summary(self.base)["number"], "1.0.0")
        self.assertEqual(userspace.summary(self.base)["trying"]["number"], v["number"])

    def test_only_a_ready_version_can_be_used(self):
        v = userspace.clone(self.base, "x", "minor")
        with self.assertRaises(ValueError):
            userspace.try_version(self.base, v["id"])

    def test_stop_using_returns_to_your_duo(self):
        v = self.ready()
        userspace.try_version(self.base, v["id"])
        userspace.stop_trying(self.base)
        self.assertEqual(userspace.active_dir(self.base), userspace.user_dir(self.base))
        self.assertIsNone(userspace.summary(self.base)["trying"])

    def test_roll_up_and_go_back_end_the_trial(self):
        v = self.ready()
        userspace.try_version(self.base, v["id"])
        userspace.roll_up(self.base, v["id"])
        self.assertIsNone(userspace.summary(self.base)["trying"])
        self.assertEqual(userspace.active_dir(self.base), userspace.user_dir(self.base))
        w = userspace.clone(self.base, "y", "minor")
        userspace.update(self.base, w["id"], status="ready")
        userspace.try_version(self.base, w["id"])
        userspace.go_back(self.base)
        self.assertIsNone(userspace.summary(self.base)["trying"])

    def test_a_trial_of_a_version_that_is_no_longer_ready_falls_back_to_your_duo(self):
        v = self.ready()
        userspace.try_version(self.base, v["id"])
        userspace.update(self.base, v["id"], status="rejected")
        self.assertEqual(userspace.active_dir(self.base), userspace.user_dir(self.base))
