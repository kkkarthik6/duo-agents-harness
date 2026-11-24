"""A run starts for any account: exact tier first, then the nearest tier, then each CLI's own default model."""
import unittest
from unittest import mock

from engine import agent_failures, model_discovery, model_policy, roleflow

RATED = {"claude": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"], "codex": ["gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna"]}


def snapshot(by_cli):
    return {"checked_at": 1, "expires_at": 9e9, "clis": [
        {"id": c, "installed": True, "status": "ready", "checked_at": 1, "source": "test",
         "models": [{"id": m, "display_name": m, "aliases": []} for m in models]} for c, models in by_cli.items()]}


def assign(found, agents, **state):
    st = dict({"agents": agents, "roles": {}, "dynamic_models": True, "workspace": "/tmp"}, **state)
    with mock.patch.object(model_discovery, "refresh", return_value=snapshot(found)):
        return st, roleflow.ensure(st)


class ExactTierTests(unittest.TestCase):
    def test_rated_models_are_used_exactly_as_before_with_no_fallback(self):
        st, profile = assign(RATED, ["claude", "codex"])
        self.assertEqual(profile["lead"]["model"], "claude-opus-5-5")
        self.assertEqual(profile["critic"]["model"], "gpt-6.1-sol")
        self.assertEqual(st["model_fallbacks"], [])
        self.assertFalse(any("fallback" in v for v in profile.values()))


class NearestTierTests(unittest.TestCase):
    def test_an_account_with_only_large_models_still_gets_a_coder_and_reviewer(self):
        st, profile = assign({"claude": ["claude-opus-5-5"], "codex": ["gpt-6-astra"]}, ["claude", "codex"])
        self.assertEqual(profile["builder"]["model"], "claude-opus-5-5")
        self.assertEqual(profile["builder"]["fallback"], "nearest tier: large")
        self.assertEqual({f["role"] for f in st["model_fallbacks"]}, {"builder", "critic"})

    def test_an_account_with_only_a_small_model_runs_every_role_on_it(self):
        st, profile = assign({"claude": ["claude-haiku-5-5"]}, ["claude"])
        self.assertEqual({v["model"] for v in profile.values()}, {"claude-haiku-5-5"})
        self.assertEqual(profile["builder"].get("fallback"), None)
        self.assertEqual(len(st["model_fallbacks"]), 3)

    def test_the_closest_tier_is_preferred_over_a_distant_one(self):
        _, profile = assign({"claude": ["claude-sonnet-5-5", "claude-haiku-5-5"]}, ["claude"])
        self.assertEqual(profile["lead"]["model"], "claude-sonnet-5-5")        # large wanted: medium before small


class CliDefaultTests(unittest.TestCase):
    def test_models_duo_has_never_rated_still_let_a_run_start(self):
        st, profile = assign({"claude": ["claude-opus-6"], "codex": ["gpt-7-ultra"]}, ["claude", "codex"])
        self.assertTrue(all(v["model"] is None and v["fallback"] == "CLI default model" for v in profile.values()))
        self.assertEqual({v["agent"] for v in profile.values()}, {"claude", "codex"})

    def test_two_clis_still_review_across_providers_when_both_use_their_defaults(self):
        st, profile = assign({"claude": ["a"], "codex": ["b"]}, ["claude", "codex"])
        self.assertNotEqual(profile["lead"]["provider"], profile["challenger"]["provider"])
        self.assertEqual(st["review_independence"], [])

    def test_one_cli_with_only_new_models_works_and_says_reviews_share_a_provider(self):
        st, profile = assign({"codex": ["gpt-7-ultra"]}, ["codex"])
        self.assertEqual({v["agent"] for v in profile.values()}, {"codex"})
        self.assertEqual({w["review"] for w in st["review_independence"]}, {"plan", "code"})

    def test_a_provider_with_an_expired_login_is_not_used_even_as_a_default(self):
        state = {"agent_availability": {}}
        agent_failures.record(state, "claude", None, {"kind": "auth_expired"}, now=10)
        st, profile = assign({"claude": ["x"], "codex": ["y"]}, ["claude", "codex"], agent_availability=state["agent_availability"])
        self.assertEqual({v["agent"] for v in profile.values()}, {"codex"})

    def test_no_ready_cli_gives_a_clear_message(self):
        with self.assertRaisesRegex(ValueError, "install and sign in"):
            assign({}, ["claude", "codex"])

    def test_a_default_model_is_shown_to_the_user_as_such(self):
        st, _ = assign({"codex": ["gpt-7-ultra"]}, ["codex"])
        with mock.patch.object(model_discovery, "refresh", return_value=snapshot({"codex": ["gpt-7-ultra"]})):
            rows = model_policy.human_view(st)
        self.assertTrue(all(r["selected_model"] == "CLI default" and r["fallback"] for r in rows))

    def test_a_missing_model_name_flows_through_the_adapter_without_a_model_flag(self):
        from engine import cli_adapters
        argv = cli_adapters.get("codex").argv(["codex"], None, None)
        self.assertNotIn("--model", argv)


if __name__ == "__main__":
    unittest.main()
