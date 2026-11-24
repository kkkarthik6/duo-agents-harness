import unittest

from engine.ste import lint

W = "word"


def rules(text):
    return [v.rule for v in lint(text)]


class SteTests(unittest.TestCase):
    def test_short_ok(self):
        self.assertEqual(lint("Run the tests. The build passes."), [])

    def test_procedural_21_words(self):
        self.assertEqual(rules("Run " + " ".join([W] * 20) + "."), ["length-procedural"])
        self.assertEqual(rules("Run " + " ".join([W] * 19) + "."), [])

    def test_descriptive_25_ok_26_fails(self):
        self.assertEqual(rules("The " + " ".join([W] * 24) + "."), [])
        self.assertEqual(rules("The " + " ".join([W] * 25) + "."), ["length-descriptive"])

    def test_modal_flagged(self):
        self.assertEqual(rules("This might break."), ["modal"])

    def test_semicolon_flagged(self):
        self.assertEqual(rules("The file opens; it fails."), ["semicolon"])

    def test_paragraph_limit(self):
        self.assertEqual(rules(" ".join(["The file opens."] * 7)), ["paragraph"])

    def test_exempt_regions(self):
        long = " ".join([W] * 40)
        text = "\n".join([
            "```python", long, "```",
            "Use `%s` here." % long,
            "> " + long,
            "See https://example.com/" + "a" * 50 + " now.",
            "```duo", '{"summary": "%s"}' % long, "```",
        ])
        self.assertEqual(lint(text), [])

    def test_contraction_not_possessive(self):
        self.assertEqual(rules("Don't run it. The agent's file."), ["contraction"])

    def test_list_items_are_sentences(self):
        self.assertEqual(lint("- Run the tests\n- Check the log"), [])


if __name__ == "__main__":
    unittest.main()
