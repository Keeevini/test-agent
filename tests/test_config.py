from unittest import TestCase
from src.config import parse_context_limit

class ContextLimitTests(TestCase):
    def test_default_and_override(self):
        self.assertEqual(parse_context_limit(None), 16384)
        self.assertEqual(parse_context_limit("24576"), 24576)
    def test_invalid_limit(self):
        for value in ("", "0", "-1", "abc", "1.5"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "MAX_CONTEXT_TOKENS"):
                parse_context_limit(value)
