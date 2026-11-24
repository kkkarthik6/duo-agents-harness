

def load_tests(loader, standard_tests, pattern):
    """Fixtures are example features with their own tests. They run inside a feature folder, not here."""
    import unittest
    return unittest.TestSuite()
