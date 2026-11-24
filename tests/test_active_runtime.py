import unittest
from unittest import mock

from engine import active_runtime, config, harness, settings


class ActiveRuntimeTests(unittest.TestCase):
    def test_parallel_workers_pauses_approval_waits_and_restarts(self):
        now = [0.0]
        state = {"status":"running", "pending":[{},{}]}
        counter = active_runtime.Counter(state, lambda:now[0])
        now[0] = 60; counter.observe(state)
        self.assertEqual(state["active_runtime_seconds"],60)
        state["paused"] = True; counter.observe(state)
        now[0] = 6000; counter.observe(state)
        self.assertEqual(state["active_runtime_seconds"],60)
        state["paused"] = False; counter.observe(state)
        now[0] += 30; counter.observe(state)
        self.assertEqual(state["active_runtime_seconds"],90)
        state["pending"] = [{"waiting":{"kind":"approval"}}]; counter.observe(state)
        now[0] += 3600; counter.observe(state)
        self.assertEqual(state["active_runtime_seconds"],90)
        state["pending"] = {"worker":{}}; counter.observe(state)
        # Loading a new broker process never uses an old timestamp.
        now[0] += 86400
        counter = active_runtime.Counter(state, lambda:now[0])
        counter.observe(state)
        self.assertEqual(state["active_runtime_seconds"],90)
        now[0] += 15; counter.observe(state)
        self.assertEqual(state["active_runtime_seconds"],105)

    def test_decisions_stops_and_empty_scheduler_are_not_work(self):
        for status in ["waiting","paused","paused_decision","stopped","done","error","booting"]:
            self.assertFalse(active_runtime.working({"status":status,"pending":[{}]}))
        self.assertFalse(active_runtime.working({"status":"running","pending":[]}))
        self.assertFalse(active_runtime.working({"status":"running","pending":[{}],"gate":"plan"}))

    def test_optional_limit_and_setting_validation(self):
        self.assertFalse(config.DEFAULTS["active_time_limit_enabled"])
        state={"active_runtime_seconds":100*3600}
        with mock.patch.dict(config.CFG,active_time_limit_enabled=False):
            self.assertEqual(harness.budget(state),("ok",100))
        with mock.patch.dict(config.CFG,active_time_limit_enabled=True,active_time_limit_hours=101):
            self.assertEqual(harness.budget(state)[0],"warn")
            config.CFG["active_time_limit_hours"]=100
            self.assertEqual(harness.budget(state)[0],"over")
        self.assertEqual(settings.check_global({"active_time_limit_enabled":False}),{"active_time_limit_enabled":False})
        for value in [0,-1,float('nan'),True]:
            with self.assertRaises(ValueError): settings.check_global({"active_time_limit_hours":value})
