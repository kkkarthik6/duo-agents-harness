import unittest

from engine.session import classify_screen
from engine.tmux import tail_lines


class ScreenTests(unittest.TestCase):
    def test_trust_prompts(self):
        self.assertEqual(classify_screen("Do you trust the files in this folder?\n> 1. Yes, proceed"), "trust")
        self.assertEqual(classify_screen("Allow Codex to work in this folder without asking for approval?"), "trust")

    def test_update_prompt_is_not_trust(self):
        text = "✨ Update available! 0.48 -> 0.49\n› 1. Update now\n  2. Skip\nPress enter to continue"
        self.assertEqual(classify_screen(text), "update")

    def test_login(self):
        self.assertEqual(classify_screen("Please log in to continue."), "login")
        self.assertEqual(classify_screen("Sign in with ChatGPT"), "login")

    def test_ready_screen(self):
        self.assertIsNone(classify_screen("Signed in with ChatGPT\n> Ask Codex to do anything"))


class TailTests(unittest.TestCase):
    def test_escape_codes_are_stripped(self):
        self.assertEqual(tail_lines("\x1b[31mred\x1b[0m text\n\x1b]0;title\x07done"), ["red text", "done"])

    def test_chrome_only_lines_are_dropped(self):
        self.assertEqual(tail_lines("working on it\n────────\n│ > │\n❯\n"), ["working on it"])

    def test_at_most_n_lines(self):
        self.assertEqual(tail_lines("a\nb\nc\nd\ne"), ["c", "d", "e"])

    def test_long_line_is_cut_to_160(self):
        self.assertEqual(len(tail_lines("x" * 400)[0]), 160)

    def test_empty_screen(self):
        self.assertEqual(tail_lines(""), [])


if __name__ == "__main__":
    unittest.main()
