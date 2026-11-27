"""Approximate cost per task: exact when the CLI reports it, token rates otherwise, nothing when unknown."""
import tempfile
import unittest

from engine import broker, cli_adapters, codex_worker, cost


class UsageCostTests(unittest.TestCase):
    def test_a_cost_reported_by_the_cli_is_used_as_is(self):
        result = cost.usage_cost("claude-opus-5-5", {"input_tokens": 10, "output_tokens": 5, "total_cost_usd": 0.42})
        self.assertEqual(result, {"usd": 0.42, "source": "cli"})

    def test_tokens_are_priced_from_the_rate_table_with_cached_input_discounted(self):
        rates = {"m": {"input": 10.0, "cached_input": 1.0, "output": 50.0}}
        usage = {"input_tokens": 1_000_000, "cached_input_tokens": 400_000, "output_tokens": 100_000}
        result = cost.usage_cost("m", usage, rates)
        self.assertEqual(result["source"], "rates")
        self.assertAlmostEqual(result["usd"], 600_000 * 10 / 1e6 + 400_000 * 1 / 1e6 + 100_000 * 50 / 1e6)

    def test_an_unknown_model_without_a_reported_cost_has_no_price(self):
        self.assertIsNone(cost.usage_cost("mystery", {"input_tokens": 5, "output_tokens": 5}, {}))

    def test_the_shipped_rate_table_is_valid(self):
        for model, rate in cost.rates().items():
            self.assertTrue({"input", "output"} <= set(rate), model)
            self.assertTrue(all(isinstance(v, (int, float)) and v >= 0 for k, v in rate.items() if k != "note"), model)


class AdapterUsageTests(unittest.TestCase):
    def test_claude_usage_carries_the_reported_cost(self):
        adapter = cli_adapters.get("claude")
        event = {"type": "result", "result": "ok", "usage": {"input_tokens": 3}, "total_cost_usd": 0.07}
        usage = dict(adapter.events(event))["usage"]
        self.assertEqual(usage["total_cost_usd"], 0.07)
        self.assertEqual(usage["input_tokens"], 3)


class BrokerCostTests(unittest.TestCase):
    def test_task_cost_adds_up_every_call_and_counts_unpriced_calls(self):
        with tempfile.TemporaryDirectory() as run:
            broker.ensure(run, "T", "Task", "Do it.")
            broker.event(run, "usage", "T", model="a", usd=0.10, source="cli", input_tokens=10, output_tokens=2)
            broker.event(run, "usage", "T", model="b", usd=0.25, source="rates", input_tokens=20, output_tokens=4)
            broker.event(run, "usage", "T", model="c", usd=None, source=None, input_tokens=5, output_tokens=1)
            node = broker.view(run)["nodes"][0]
            self.assertAlmostEqual(node["cost"]["usd"], 0.35)
            self.assertEqual(node["cost"]["calls"], 3)
            self.assertEqual(node["cost"]["unpriced"], 1)
            self.assertEqual(node["cost"]["input_tokens"], 35)

    def test_a_task_without_usage_has_no_cost(self):
        with tempfile.TemporaryDirectory() as run:
            broker.ensure(run, "T", "Task", "Do it.")
            self.assertIsNone(broker.view(run)["nodes"][0].get("cost"))


class WorkerUsageTests(unittest.TestCase):
    def test_a_finished_call_is_recorded_on_its_task_with_a_price(self):
        with tempfile.TemporaryDirectory() as run:
            broker.ensure(run, "T", "Task", "Do it.")
            worker = codex_worker.Worker(["codex"], "codex")
            worker.broker_task = {"run_dir": run, "task_id": "T"}
            worker.seen_model = "gpt-6-astra"
            worker.usage = {"input_tokens": 1_000_000, "cached_input_tokens": 0, "output_tokens": 0}
            worker.record_usage()
            self.assertAlmostEqual(broker.view(run)["nodes"][0]["cost"]["usd"], 10.0)

    def test_a_call_with_no_usage_records_nothing(self):
        with tempfile.TemporaryDirectory() as run:
            broker.ensure(run, "T", "Task", "Do it.")
            worker = codex_worker.Worker(["codex"], "codex")
            worker.broker_task = {"run_dir": run, "task_id": "T"}
            worker.record_usage()
            self.assertIsNone(broker.view(run)["nodes"][0].get("cost"))


if __name__ == "__main__":
    unittest.main()
