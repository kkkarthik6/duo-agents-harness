import unittest

from engine import settings


class GlobalSettingsTests(unittest.TestCase):
    def test_changed_root_is_where_new_projects_go(self):
        import os, tempfile
        from unittest import mock
        from engine import config, trust
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(config.CFG):
            target = os.path.join(d, "elsewhere")
            config.CFG.update(settings.check_global({"root": target}))
            self.assertEqual(trust.unique_project_dir("demo"), config.root() / "demo")
            self.assertEqual(str(config.root()), target)

    def test_allowed_value_is_kept(self):
        self.assertEqual(settings.check_global({"stall_minutes": 5}), {"stall_minutes": 5})

    def test_booleans_must_be_real_booleans(self):
        with self.assertRaises(ValueError):
            settings.check_global({"auto_approve": "true"})

    def test_security_settings_cannot_be_changed(self):
        with self.assertRaisesRegex(ValueError, "cannot be changed here"):
            settings.check_global({"security_rules": False})

    def test_sandbox_must_be_auto_on_or_off(self):
        with self.assertRaises(ValueError):
            settings.check_global({"sandbox": "maybe"})

    def test_root_is_created_and_kept(self):
        import os, tempfile
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "my projects")
            self.assertEqual(settings.check_global({"root": target}), {"root": target})
            self.assertTrue(os.path.isdir(target))

    def test_root_must_be_an_absolute_folder(self):
        import tempfile
        for bad in ("", "relative/dir", 5):
            with self.assertRaises(ValueError):
                settings.check_global({"root": bad})
        with tempfile.NamedTemporaryFile() as f:
            with self.assertRaises(ValueError):
                settings.check_global({"root": f.name})

    def test_root_cannot_be_inside_duo_itself(self):
        from engine import config
        with self.assertRaises(ValueError):
            settings.check_global({"root": str(config.APP_DIR / "engine")})


if __name__ == "__main__":
    unittest.main()
