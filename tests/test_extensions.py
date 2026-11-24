import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from engine import config, coreapi, extensions, features, settings
from tests.test_components import feature


class ExtensionControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.user = Path(self.tmp.name)
        self.patch = mock.patch.dict(config.CFG, {"feature_controls": {}, "features": {}}, clear=False)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        feature(self.user, "demo", '''
            def register(api):
                api.setting("count", 20, minimum=1, maximum=100, label="Items per page")
                api.setting("sort", "newest", choices=["newest", "oldest"])
                api.setting("api_token", "private-value")
                api.setting("advanced", {"keep": True})
                api.slot("home", "ui/page.js")
        ''')
        self.reg = features.load(self.user)
        extensions.apply_layout(self.reg)

    def test_controls_are_typed_and_secrets_and_complex_values_are_omitted(self):
        data = extensions.view(self.reg, {})
        controls = data["features"][0]["controls"]
        self.assertEqual([x["key"] for x in controls], ["count", "sort"])
        self.assertEqual(controls[0]["maximum"], 100)
        self.assertNotIn("private-value", json.dumps(data))
        self.assertIn("execute", data["extension_points"]["stages"])

    def test_setting_changes_reach_feature_context_and_preserve_other_values(self):
        config.CFG["features"] = {"other": {"keep": 3}, "demo": {"advanced": {"keep": True}}}
        patch = extensions.configure(self.reg, {"feature": "demo", "values": {"count": 12, "sort": "oldest"}})
        config.CFG.update(patch)
        self.reg.settings_fn = lambda f: config.CFG["features"].get(f, {})
        self.assertEqual(self.reg.ctx("demo").setting("count"), 12)
        self.assertEqual(config.CFG["features"]["other"]["keep"], 3)

    def test_invalid_values_and_undeclared_controls_cannot_mutate_settings(self):
        before = copy.deepcopy(config.CFG)
        for values in ({"count": True}, {"count": 101}, {"sort": "bad"}, {"api_token": "new"}, {"security_rules": False}):
            with self.assertRaises(ValueError):
                extensions.configure(self.reg, {"feature": "demo", "values": values})
        self.assertEqual(config.CFG, before)

    def test_layout_stays_stable_across_reload_and_hidden_panels_do_not_mount(self):
        cid = self.reg.components[0]["control_id"]
        patch = extensions.configure(self.reg, {"feature": "demo", "layout": {cid: {"slot": "settings", "visible": False, "order": 2}}})
        config.CFG.update(patch)
        loaded = features.load(self.user)
        extensions.apply_layout(loaded)
        self.assertEqual(loaded.components[0]["control_id"], cid)
        self.assertEqual(loaded.components[0]["slot"], "settings")
        self.assertEqual(loaded.view()["components"], [])
        self.assertEqual(loaded.slots["settings"], [])

    def test_layout_cannot_replace_modules_or_select_unknown_slots(self):
        cid = self.reg.components[0]["control_id"]
        for patch in ({"module": "https://evil.invalid/code.js"}, {"slot": "arbitrary"}, {"order": -1}, {"visible": "yes"}):
            with self.assertRaises(ValueError):
                extensions.configure(self.reg, {"feature": "demo", "layout": {cid: patch}})

    def test_disabling_is_explicit_and_core_failures_cannot_be_overridden(self):
        patch = extensions.configure(self.reg, {"feature": "demo", "enabled": False})
        self.assertFalse(patch["feature_controls"]["demo"]["enabled"])
        with self.assertRaises(ValueError):
            extensions.configure(self.reg, {"feature": "demo", "enabled": True}, disabled={"demo"})

    def test_legacy_complex_and_nonstandard_setting_keys_remain_available(self):
        api = coreapi.Api("legacy")
        api.setting("old.key", {"a": 1})
        self.assertEqual(api.settings["old.key"], {"a": 1})
        self.assertEqual(api.setting_specs, {})

    def test_runtime_controls_validate_ranges_and_checkpoint_order(self):
        self.assertEqual(settings.check_global({"job_runtime_multiplier": 2.5}), {"job_runtime_multiplier": 2.5})
        for value in (True, 0, float("nan"), 11):
            with self.assertRaises(ValueError):
                settings.check_global({"job_runtime_multiplier": value})
        with self.assertRaises(ValueError):
            settings.check_global({"progress_nudge_seconds": 600, "progress_timeout_seconds": 300})
