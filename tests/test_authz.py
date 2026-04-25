"""Auth chokepoint tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bridge  # noqa: E402

# Mirror python-telegram-bot's ChatType enum so authz checks work
PRIVATE = bridge.ChatType.PRIVATE
GROUP = bridge.ChatType.GROUP


def make_update(*, user_id, chat_id, chat_type=PRIVATE, text="hi"):
    msg = MagicMock()
    msg.text = text
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=chat_id, type=chat_type),
        message=msg,
    )


class TestAuthz(unittest.TestCase):

    def setUp(self) -> None:
        bridge.ALLOWED_USER_IDS = {12345}

    def test_allowed_user_private_chat(self) -> None:
        u = make_update(user_id=12345, chat_id=12345)
        self.assertTrue(bridge.authz(u))

    def test_unknown_user_rejected(self) -> None:
        u = make_update(user_id=99999, chat_id=99999)
        self.assertFalse(bridge.authz(u))

    def test_group_chat_rejected_even_for_allowed_user(self) -> None:
        u = make_update(user_id=12345, chat_id=-1001, chat_type=GROUP)
        self.assertFalse(bridge.authz(u))

    def test_chat_id_must_match_user_id(self) -> None:
        # impersonation attempt: allowed user's id, but chat_id differs
        u = make_update(user_id=12345, chat_id=67890)
        self.assertFalse(bridge.authz(u))

    def test_no_message_text_rejected(self) -> None:
        u = make_update(user_id=12345, chat_id=12345)
        u.message.text = None
        self.assertFalse(bridge.authz(u))

    def test_missing_user_rejected(self) -> None:
        u = make_update(user_id=12345, chat_id=12345)
        u.effective_user = None
        self.assertFalse(bridge.authz(u))


if __name__ == "__main__":
    unittest.main()
