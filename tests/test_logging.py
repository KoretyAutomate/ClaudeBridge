"""Log redaction tests. No network, no subprocess."""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bridge  # noqa: E402

SAMPLE_BOT_ID = "123456789:AAFakeTokenFakeTokenFakeTokenFake_-x"


def _record(msg: str, args: tuple = (), exc_info=None) -> logging.LogRecord:
    return logging.LogRecord("httpx", logging.INFO, __file__, 1, msg, args, exc_info)


class RedactingFormatterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fmt = bridge.RedactingFormatter("%(message)s")

    def test_token_in_message_args_is_masked(self) -> None:
        out = self.fmt.format(
            _record("HTTP Request: POST %s", (f"https://api.telegram.org/bot{SAMPLE_BOT_ID}/getUpdates",))
        )
        self.assertNotIn(SAMPLE_BOT_ID, out)
        self.assertIn("https://api.telegram.org/bot<redacted>/getUpdates", out)

    def test_token_in_traceback_is_masked(self) -> None:
        try:
            raise ConnectionError(f"failed https://api.telegram.org/bot{SAMPLE_BOT_ID}/getMe")
        except ConnectionError:
            out = self.fmt.format(_record("boom", exc_info=sys.exc_info()))
        self.assertNotIn(SAMPLE_BOT_ID, out)

    def test_plain_message_unchanged(self) -> None:
        self.assertEqual(self.fmt.format(_record("dns.ok target=api.telegram.org")), "dns.ok target=api.telegram.org")


if __name__ == "__main__":
    unittest.main()
