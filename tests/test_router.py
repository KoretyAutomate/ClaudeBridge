"""Router resolution tests. No network, no subprocess."""
from __future__ import annotations

import sqlite3
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bridge  # noqa: E402


def _setup_cfg() -> None:
    bridge.CFG = {
        "default_scope": "idea",
        "never_sticky": ["trading", "podcast"],
        "prefixes": {
            "idea": {"cwd": "/tmp/_ideas", "model": "sonnet",
                     "allowed_tools": ["Read"]},
            "podcast": {"cwd": "/tmp/podcast", "model": "opus",
                        "allowed_tools": ["Read", "Edit"]},
            "trading": {"cwd": "/tmp/trading", "model": "sonnet",
                        "allowed_tools": ["Read"]},
            "screener": {"cwd": "/tmp/screener", "model": "sonnet",
                         "allowed_tools": ["Read", "Edit"]},
        },
        "verbs": {
            "/help": "h", "/list": "l", "/status": "s",
            "/new": "n", "/reset": "n", "/scaffold": "sc",
        },
    }
    bridge.NEVER_STICKY = set(bridge.CFG["never_sticky"])


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE sessions (
            chat_id INTEGER, scope TEXT, session_uuid TEXT,
            updated_at TEXT, PRIMARY KEY (chat_id, scope)
        );
        CREATE TABLE chat_state (
            chat_id INTEGER PRIMARY KEY,
            sticky_scope TEXT, sticky_set_at TEXT
        );
        CREATE TABLE sessions_archive (
            chat_id INTEGER, scope TEXT, session_uuid TEXT, archived_at TEXT
        );
        CREATE TABLE daily_cost (day TEXT PRIMARY KEY, cost_usd REAL);
    """)
    return c


class TestRouter(unittest.TestCase):

    def setUp(self) -> None:
        _setup_cfg()
        self.conn = _conn()

    def test_verb_help(self) -> None:
        token, body, kind = bridge.resolve("/help", 1, self.conn)
        self.assertEqual(("/help", "", "verb"), (token, body, kind))

    def test_verb_scaffold_with_arg(self) -> None:
        token, body, kind = bridge.resolve("/scaffold MyThing", 1, self.conn)
        self.assertEqual(token, "/scaffold")
        self.assertEqual(body, "MyThing")
        self.assertEqual(kind, "verb")

    def test_prefix_with_body(self) -> None:
        token, body, kind = bridge.resolve("/podcast write ep5", 1, self.conn)
        self.assertEqual(("podcast", "write ep5", "route"),
                         (token, body, kind))

    def test_bare_prefix_is_switch(self) -> None:
        token, body, kind = bridge.resolve("/screener", 1, self.conn)
        self.assertEqual(("screener", None, "switch"), (token, body, kind))

    def test_unknown_prefix_is_error(self) -> None:
        _, _, kind = bridge.resolve("/nonsense do something", 1, self.conn)
        self.assertEqual(kind, "error")

    def test_unprefixed_falls_to_default(self) -> None:
        token, body, kind = bridge.resolve("just a thought", 1, self.conn)
        self.assertEqual(token, "idea")
        self.assertEqual(kind, "route")

    def test_sticky_routes_unprefixed(self) -> None:
        bridge.update_sticky(self.conn, 1, "screener")
        token, body, kind = bridge.resolve("keep going", 1, self.conn)
        self.assertEqual(token, "screener")
        self.assertEqual(kind, "route")

    def test_trading_never_sticks(self) -> None:
        # Force a sticky write of trading scope (bypassing the guard) to
        # verify resolve() ignores it.
        self.conn.execute(
            "INSERT INTO chat_state (chat_id, sticky_scope, sticky_set_at) "
            "VALUES (?, ?, ?)",
            (1, "trading", datetime.utcnow().isoformat()),
        )
        self.conn.commit()
        token, _, _ = bridge.resolve("what's AAPL doing", 1, self.conn)
        self.assertEqual(token, "idea")

    def test_podcast_never_sticks(self) -> None:
        self.conn.execute(
            "INSERT INTO chat_state (chat_id, sticky_scope, sticky_set_at) "
            "VALUES (?, ?, ?)",
            (1, "podcast", datetime.utcnow().isoformat()),
        )
        self.conn.commit()
        token, _, _ = bridge.resolve("a personal story", 1, self.conn)
        self.assertEqual(token, "idea")

    def test_sticky_expires_after_24h(self) -> None:
        long_ago = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        self.conn.execute(
            "INSERT INTO chat_state (chat_id, sticky_scope, sticky_set_at) "
            "VALUES (?, ?, ?)",
            (1, "screener", long_ago),
        )
        self.conn.commit()
        token, _, _ = bridge.resolve("idea", 1, self.conn)
        self.assertEqual(token, "idea")

    def test_scaffold_name_regex(self) -> None:
        self.assertTrue(bridge.SCAFFOLD_NAME_RE.match("FooBar"))
        self.assertTrue(bridge.SCAFFOLD_NAME_RE.match("foo-bar_baz"))
        self.assertFalse(bridge.SCAFFOLD_NAME_RE.match(""))
        self.assertFalse(bridge.SCAFFOLD_NAME_RE.match("../etc"))
        self.assertFalse(bridge.SCAFFOLD_NAME_RE.match("foo bar"))
        self.assertFalse(bridge.SCAFFOLD_NAME_RE.match("a" * 33))


class TestSessions(unittest.TestCase):

    def setUp(self) -> None:
        _setup_cfg()
        self.conn = _conn()

    def test_get_or_create_creates_then_returns_same(self) -> None:
        u1 = bridge.get_or_create_uuid(self.conn, 42, "podcast")
        u2 = bridge.get_or_create_uuid(self.conn, 42, "podcast")
        self.assertEqual(u1, u2)
        self.assertEqual(len(u1), 36)

    def test_per_chat_per_scope_isolated(self) -> None:
        a = bridge.get_or_create_uuid(self.conn, 42, "podcast")
        b = bridge.get_or_create_uuid(self.conn, 42, "trading")
        self.assertNotEqual(a, b)

    def test_reset_archives_old(self) -> None:
        old = bridge.get_or_create_uuid(self.conn, 42, "podcast")
        new = bridge.reset_session(self.conn, 42, "podcast")
        self.assertNotEqual(old, new)
        archived = self.conn.execute(
            "SELECT session_uuid FROM sessions_archive WHERE chat_id=42"
        ).fetchone()
        self.assertEqual(archived[0], old)

    def test_daily_cost_accumulates(self) -> None:
        self.assertEqual(0.0, bridge.todays_cost(self.conn))
        bridge.add_cost(self.conn, 0.50)
        bridge.add_cost(self.conn, 0.75)
        self.assertAlmostEqual(1.25, bridge.todays_cost(self.conn))


if __name__ == "__main__":
    unittest.main()
