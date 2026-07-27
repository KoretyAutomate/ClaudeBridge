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
            used_count INTEGER NOT NULL DEFAULT 0,
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

    def test_get_existing_returns_none_when_unused(self) -> None:
        # No row at all.
        self.assertIsNone(bridge.get_existing_uuid(self.conn, 42, "podcast"))
        # Row with used_count=0 (e.g. just-migrated stale entry) is also None.
        self.conn.execute(
            "INSERT INTO sessions (chat_id, scope, session_uuid, used_count) "
            "VALUES (42, 'podcast', 'stale-uuid', 0)"
        )
        self.assertIsNone(bridge.get_existing_uuid(self.conn, 42, "podcast"))

    def test_upsert_session_then_get_existing(self) -> None:
        bridge.upsert_session(self.conn, 42, "podcast", "uuid-from-claude")
        self.assertEqual(
            "uuid-from-claude",
            bridge.get_existing_uuid(self.conn, 42, "podcast"),
        )
        # Second upsert with a new uuid replaces and bumps used_count.
        bridge.upsert_session(self.conn, 42, "podcast", "new-uuid")
        self.assertEqual(
            "new-uuid",
            bridge.get_existing_uuid(self.conn, 42, "podcast"),
        )
        used = self.conn.execute(
            "SELECT used_count FROM sessions WHERE chat_id=42 AND scope='podcast'"
        ).fetchone()[0]
        self.assertEqual(2, used)

    def test_per_chat_per_scope_isolated(self) -> None:
        bridge.upsert_session(self.conn, 42, "podcast", "u-podcast")
        bridge.upsert_session(self.conn, 42, "trading", "u-trading")
        self.assertEqual("u-podcast",
                         bridge.get_existing_uuid(self.conn, 42, "podcast"))
        self.assertEqual("u-trading",
                         bridge.get_existing_uuid(self.conn, 42, "trading"))

    def test_archive_uuid_deletes_active_row(self) -> None:
        bridge.upsert_session(self.conn, 42, "podcast", "u1")
        bridge.archive_uuid(self.conn, 42, "podcast", "u1")
        self.assertIsNone(bridge.get_existing_uuid(self.conn, 42, "podcast"))
        archived = self.conn.execute(
            "SELECT session_uuid FROM sessions_archive WHERE chat_id=42"
        ).fetchone()
        self.assertEqual("u1", archived[0])

    def _age_session(self, chat_id: int, scope: str, secs: int) -> None:
        """Backdate updated_at so the row looks `secs` seconds idle."""
        old = (datetime.utcnow() - timedelta(seconds=secs)).isoformat()
        self.conn.execute(
            "UPDATE sessions SET updated_at=? WHERE chat_id=? AND scope=?",
            (old, chat_id, scope),
        )
        self.conn.commit()

    def test_fresh_session_is_not_stale(self) -> None:
        bridge.upsert_session(self.conn, 42, "thincart", "u1")
        self.assertFalse(
            bridge.is_session_stale(self.conn, 42, "thincart", 43200)
        )

    def test_idle_session_past_ttl_is_stale(self) -> None:
        # The real bug: a thread minted for last week's report resumed a week
        # later, so a bare "approve" landed in the wrong conversation.
        bridge.upsert_session(self.conn, 42, "thincart", "u1")
        self._age_session(42, "thincart", 7 * 24 * 3600)
        self.assertTrue(
            bridge.is_session_stale(self.conn, 42, "thincart", 43200)
        )

    def test_ttl_zero_disables_expiry(self) -> None:
        bridge.upsert_session(self.conn, 42, "thincart", "u1")
        self._age_session(42, "thincart", 365 * 24 * 3600)
        self.assertFalse(
            bridge.is_session_stale(self.conn, 42, "thincart", 0)
        )

    def test_stale_check_tolerates_missing_and_bad_rows(self) -> None:
        # No row at all.
        self.assertFalse(
            bridge.is_session_stale(self.conn, 42, "thincart", 43200)
        )
        # Unparseable timestamp must not raise — treat as not stale.
        bridge.upsert_session(self.conn, 42, "thincart", "u1")
        self.conn.execute(
            "UPDATE sessions SET updated_at='not-a-date' "
            "WHERE chat_id=42 AND scope='thincart'"
        )
        self.conn.commit()
        self.assertFalse(
            bridge.is_session_stale(self.conn, 42, "thincart", 43200)
        )

    def test_resume_failure_heuristic(self) -> None:
        self.assertTrue(bridge._is_resume_failure(
            "claude exited 1: Error: session not found"
        ))
        self.assertTrue(bridge._is_resume_failure(
            "could not find session 'xyz'"
        ))
        self.assertFalse(bridge._is_resume_failure(
            "claude exited 1: Error: rate limited"
        ))
        self.assertFalse(bridge._is_resume_failure(""))

    def test_daily_cost_accumulates(self) -> None:
        self.assertEqual(0.0, bridge.todays_cost(self.conn))
        bridge.add_cost(self.conn, 0.50)
        bridge.add_cost(self.conn, 0.75)
        self.assertAlmostEqual(1.25, bridge.todays_cost(self.conn))


class TestTableFormatter(unittest.TestCase):

    def test_separator_detection(self) -> None:
        self.assertTrue(bridge._is_table_separator("|---|---|"))
        self.assertTrue(bridge._is_table_separator("| --- | :---: | ---: |"))
        self.assertFalse(bridge._is_table_separator("---"))  # no pipe
        self.assertFalse(bridge._is_table_separator("| Cell | Cell |"))  # no dash
        self.assertFalse(bridge._is_table_separator(""))

    def test_has_tables_positive(self) -> None:
        text = "Intro\n\n| H1 | H2 |\n|---|---|\n| a | b |\n\nOutro"
        self.assertTrue(bridge._has_markdown_tables(text))

    def test_has_tables_negative(self) -> None:
        self.assertFalse(bridge._has_markdown_tables("just plain text"))
        self.assertFalse(bridge._has_markdown_tables("---\nh-rule\n---"))

    def test_format_renders_table_as_cards(self) -> None:
        text = "Before\n\n| Option | Notes |\n|---|---|\n| A | foo |\n| B | bar |\n\nAfter"
        out = bridge._format_for_telegram_html(text)
        self.assertIn("Before", out)
        self.assertIn("After", out)
        # Card format: bold header label : value.
        self.assertIn("<b>Option</b>: A", out)
        self.assertIn("<b>Notes</b>: foo", out)
        self.assertIn("<b>Option</b>: B", out)
        self.assertIn("<b>Notes</b>: bar", out)
        # Cards are separated by a horizontal rule.
        self.assertIn("———", out)

    def test_format_strips_bold_from_header_labels(self) -> None:
        text = "| **Option** | **Notes** |\n|---|---|\n| A | foo |"
        out = bridge._format_for_telegram_html(text)
        self.assertIn("<b>Option</b>: A", out)
        self.assertNotIn("<b><b>", out)  # no nested bold

    def test_format_renders_inline_bold_in_cells(self) -> None:
        text = "| Option | Notes |\n|---|---|\n| **strong** | plain |"
        out = bridge._format_for_telegram_html(text)
        self.assertIn("<b>Option</b>: <b>strong</b>", out)

    def test_format_html_escapes_inside_table(self) -> None:
        text = "| Cmd | Effect |\n|---|---|\n| code | <x> |"
        out = bridge._format_for_telegram_html(text)
        # Literal `<x>` must be escaped so Telegram does not parse it as HTML.
        self.assertIn("&lt;x&gt;", out)

    def test_format_html_escapes_outside_table(self) -> None:
        text = "Has <script> and & ampersand"
        out = bridge._format_for_telegram_html(text)
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("&amp;", out)

    def test_format_handles_no_tables(self) -> None:
        out = bridge._format_for_telegram_html("just plain text")
        self.assertEqual(out, "just plain text")


if __name__ == "__main__":
    unittest.main()
