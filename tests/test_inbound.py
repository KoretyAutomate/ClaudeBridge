"""Inbound update wiring tests. No network, no subprocess.

Covers the handler wiring that made a missing reply undiagnosable: every
update must hit the group -1 logger, and an edited message must reach the
edit handler rather than the main chokepoint.
"""
from __future__ import annotations

import asyncio
import logging
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from telegram import Chat, Message, Update, User
from telegram.ext import filters

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bridge  # noqa: E402

CHAT_ID = 4242

# The exact filter expressions main() registers. Kept as module constants so
# a change to one in bridge.py without the other shows up as a failure here.
MAIN_FILTER = (
    filters.ChatType.PRIVATE & filters.TEXT
    & ~filters.UpdateType.EDITED_MESSAGE
)
EDIT_FILTER = (
    filters.ChatType.PRIVATE & filters.TEXT
    & filters.UpdateType.EDITED_MESSAGE
)


def _msg(text: str, *, age_secs: int = 0) -> Message:
    user = User(id=CHAT_ID, first_name="K", is_bot=False)
    chat = Chat(id=CHAT_ID, type=Chat.PRIVATE)
    when = datetime.now(timezone.utc) - timedelta(seconds=age_secs)
    m = Message(
        message_id=1, date=when, chat=chat, from_user=user, text=text,
    )
    m.set_bot(None)  # never used; keeps Message from needing a live bot
    return m


def _update(text: str, *, edited: bool = False, age_secs: int = 0) -> Update:
    m = _msg(text, age_secs=age_secs)
    if edited:
        return Update(update_id=1, edited_message=m)
    return Update(update_id=1, message=m)


class TestUpdateRouting(unittest.TestCase):
    """A normal message and an edit must land on different handlers."""

    def test_normal_message_hits_main_filter_only(self) -> None:
        u = _update("/era refactor please")
        self.assertTrue(MAIN_FILTER.check_update(u))
        self.assertFalse(EDIT_FILTER.check_update(u))

    def test_edited_message_hits_edit_filter_only(self) -> None:
        u = _update("/era refactor please", edited=True)
        self.assertFalse(MAIN_FILTER.check_update(u))
        self.assertTrue(EDIT_FILTER.check_update(u))


class TestInboundLogging(unittest.TestCase):
    """Group -1 logger must see every update, whatever the filters do."""

    def _capture(self, update: object) -> list[str]:
        records: list[str] = []

        class Grab(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record.getMessage())

        h = Grab()
        bridge.log.addHandler(h)
        old = bridge.log.level
        bridge.log.setLevel(logging.INFO)
        try:
            asyncio.run(bridge.log_inbound_update(update, None))
        finally:
            bridge.log.removeHandler(h)
            bridge.log.setLevel(old)
        return records

    def test_logs_normal_message(self) -> None:
        out = self._capture(_update("hello there"))
        self.assertTrue(any("inbound.update" in r for r in out), out)
        self.assertTrue(any("kind=message" in r for r in out), out)
        self.assertTrue(any(f"chat={CHAT_ID}" in r for r in out), out)

    def test_logs_edited_message_kind(self) -> None:
        out = self._capture(_update("fixed text", edited=True))
        self.assertTrue(any("kind=edited_message" in r for r in out), out)

    def test_logs_age_for_delayed_message(self) -> None:
        out = self._capture(_update("late", age_secs=3600))
        joined = "\n".join(out)
        self.assertIn("inbound.update", joined)
        # ~3600s, printed as an integer second count.
        self.assertRegex(joined, r"age=3(59\d|60\d)s")

    def test_never_raises_on_non_update(self) -> None:
        out = self._capture({"not": "an update"})
        self.assertTrue(any("non-update" in r for r in out), out)


if __name__ == "__main__":
    unittest.main()
