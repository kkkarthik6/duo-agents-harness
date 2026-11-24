import unittest

from engine.readiness import checks, login_card


def by_id(items):
    return {item["id"]: item for item in items}


class ChecksTest(unittest.TestCase):
    def test_all_good(self):
        items = checks(
            {"claude": {"found": True, "signed_in": True}},
            (True, "ok"),
            True,
        )
        for item in items:
            self.assertTrue(item["ok"], item)
            self.assertEqual(item["fix"], "", item)

    def test_agent_not_found(self):
        items = by_id(checks(
            {"claude": {"found": False, "signed_in": None}},
            (True, "ok"),
            True,
        ))
        self.assertFalse(items["agent:claude"]["ok"])
        self.assertIn("Install the", items["agent:claude"]["fix"])

    def test_agent_found_but_signed_out(self):
        items = by_id(checks(
            {"claude": {"found": True, "signed_in": False}},
            (True, "ok"),
            True,
        ))
        self.assertFalse(items["agent:claude"]["ok"])
        self.assertIn("sign in", items["agent:claude"]["fix"])

    def test_signed_in_unknown_is_ok(self):
        items = by_id(checks(
            {"claude": {"found": True, "signed_in": None}},
            (True, "ok"),
            True,
        ))
        self.assertTrue(items["agent:claude"]["ok"])

    def test_docker_down(self):
        items = by_id(checks({}, (False, "daemon not running"), True))
        self.assertFalse(items["docker"]["ok"])
        self.assertIn("Start Docker Desktop", items["docker"]["fix"])

    def test_tmux_missing(self):
        items = by_id(checks({}, (True, "ok"), False))
        self.assertFalse(items["tmux"]["ok"])
        self.assertIn("brew install tmux", items["tmux"]["fix"])

    def test_login_card(self):
        card = login_card("claude")
        self.assertEqual(card["title"], "Sign in to claude")
        self.assertEqual(len(card["steps"]), 3)
        self.assertIn("claude", card["steps"][1])


if __name__ == "__main__":
    unittest.main()
