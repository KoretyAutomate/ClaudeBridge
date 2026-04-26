# ClaudeBridge

Telegram → headless Claude Code dispatcher. Send a message from your phone, work starts on your workstation in the right project.

Single-user. Long-poll only (no inbound port). Threaded conversations per `(chat × scope)`.

## Why

If you run [Claude Code](https://claude.ai/code) on a workstation but want to dispatch work from your phone without sitting at the keyboard, this bridges Telegram → `claude -p` headless. Each project (`/myproject`, `/finance`, etc.) maps to a working directory; conversations are threaded per scope so you can switch between projects mid-chat without context bleed.

## Architecture

```
Phone (Telegram app)
   │
   ▼
Telegram cloud  ◄── outbound long-poll only ──┐
                                              │
                              [your workstation]
                                              │
                              systemd --user
                              ClaudeBridge daemon
                                  │
                                  ├─ whitelist (your Telegram user ID)
                                  ├─ /prefix → cwd  (per project)
                                  ├─ chat × scope → session UUID  (SQLite)
                                  └─ subprocess: claude -p
                                                  --resume <uuid>
                                                    (or fresh on first call;
                                                     UUID captured from JSON
                                                     output for later resume)
                                                  --allowed-tools <whitelist>
                                                  --disallowed-tools <denylist>
                                                  --max-budget-usd 2
```

See [`PLAN.md`](PLAN.md) for full architecture, threat model, and design decisions.
See [`SECURITY.md`](SECURITY.md) for the threat model summary.

## Requirements

- Linux with `systemd --user` and linger enabled
- Python 3.11+
- `conda` (script defaults to miniconda — override `CONDA_BIN` env var if elsewhere)
- [Claude Code CLI](https://claude.ai/code) installed and authenticated (the daemon shells out to `claude -p`)
- A Telegram bot — create via [@BotFather](https://t.me/BotFather). Use a **dedicated** bot, not one you share with other tools

## Install

```bash
# 1. Clone into your workspace (path is configurable; default ~/Project/ClaudeBridge)
git clone https://github.com/<you>/ClaudeBridge.git ~/Project/ClaudeBridge
cd ~/Project/ClaudeBridge

# 2. First run — bootstraps .env and config.yaml from examples
bash scripts/install.sh
# script exits asking you to fill .env

# 3. Edit .env with your bot token, Telegram user ID, and (if you use OAuth)
#    CLAUDE_CODE_OAUTH_TOKEN. systemd-user services don't inherit your
#    interactive shell's env, so OAuth auth needs the token in .env explicitly
#    or claude -p subprocesses get HTTP 401.
${EDITOR:-nano} .env

# 4. Edit config.yaml — set your project prefixes, cwds, models, allowed_tools
${EDITOR:-nano} config.yaml

# 5. Re-run install.sh — creates conda env, installs deps,
#    audits .gitignore in each scoped project, links systemd unit
bash scripts/install.sh

# 6. Run unit tests
~/miniconda3/envs/claude_bridge/bin/python -m unittest discover -s tests -v

# 7. Enable + start
systemctl --user enable --now claude-bridge.service
journalctl --user -u claude-bridge.service -f

# Look for: dns.ok, db.ready, polling.started — then send /help to your bot.
```

## Configuration — `config.yaml`

See [`config.example.yaml`](config.example.yaml) for the schema with comments. Highlights:

- `prefixes:` — `/name → {cwd, model, allowed_tools}` per project. Anything you don't list in `allowed_tools` is implicitly denied.
- `default_scope:` — where unprefixed messages go (typically an "ideas" scratchpad).
- `never_sticky:` — list of scope names that always require an explicit prefix. Use for sensitive scopes where accidental routing of unrelated content would be bad.
- `daily_cost_cap_usd:` — hard cap on Anthropic spend per day.
- `max_budget_usd_per_call:` — cap per message.

Use `["Read", "Glob", "Grep"]` for a "glass case" scope (read-only, no shell, no writes).

## Usage from Telegram

| Send | What it does |
|---|---|
| `/help` | List verbs and prefixes |
| `/list` | Show all prefixes with cwd + allowed tools |
| `/status` | Active threads + today's cost |
| `/myproject build the next release` | Run that prompt in `myproject` cwd |
| `/myproject` (bare) | Switch sticky scope without spending a Claude call |
| `unprefixed text` | Routes to your sticky scope, or the default if none |
| `/new` | Reset the current scope's thread |
| `/scaffold NewName` | Create `~/Project/NewName/` with read-only allowed tools, register prefix |

## Routing & sessions

Each incoming message resolves in this order:

1. Non-text (photos, stickers, voice notes) → reply "text only" and drop.
2. `/<verb>` (`/help`, `/list`, `/status`, `/new`, `/scaffold`) → handled in-process, no Claude call.
3. `/<prefix>` from `config.yaml`:
   - bare `/<prefix>` → switch sticky scope, no Claude call
   - `/<prefix> body` → run `body` in that scope's `cwd`
4. `/<unknown>` → reply with help, no Claude call (don't burn budget on typos).
5. Unprefixed text → run in the chat's sticky scope (within 24h of last explicit prefix), else `default_scope`.

Sessions are stored as `(chat_id, scope) → session_uuid` in `~/.claude_bridge/sessions.db`. First message in a (chat × scope) pair spawns Claude fresh and captures the assigned `session_id` from the JSON output; subsequent messages use `--resume <uuid>`. If the resume target is gone (e.g. `~/.claude/` was cleaned), the bridge archives the dead UUID, retries fresh, and tells you in chat.

Scopes listed in `never_sticky` (`config.yaml`) always require an explicit prefix — useful for sensitive scopes where accidental routing of unrelated content would be a problem.

`/scaffold <Name>` validates the name against `^[a-zA-Z0-9_-]{1,32}$`, creates `~/Project/<Name>/` with a starter `CLAUDE.md`, and registers it as a read-only prefix in `config.yaml` (you can manually promote it to grant `Edit`/`Write`/`Bash` later, then restart the service).

Long replies that contain markdown tables are reformatted as one bold-label "card" per row, separated by `———` — much easier to read on a phone than a wrapped fixed-width table.

## Security model

See [`SECURITY.md`](SECURITY.md) and [`PLAN.md`](PLAN.md). Headline:

- Single-user, long-poll only, no inbound port
- Hard whitelist on Telegram user ID, private chat only
- Per-scope `--allowed-tools` whitelist + global `--disallowed-tools` denylist (no `curl`, `nc`, `ssh`, `base64`, no AWS or Claude credentials)
- systemd hardening (`NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only` with explicit `ReadWritePaths`, `LimitCORE=0`)
- `Conflict` on `getUpdates` (token-compromise signal) → exit 78 → service stays dead, deadletter flag written

What this does **not** defend against:
- Your Telegram account being compromised on your phone
- Indirect prompt injection through tool outputs in scopes that grant `Bash`/`Edit`/`Write`
- Multi-tenant exposure (don't deploy this for more than one user)

## Limitations

- Linux + systemd only (no Docker / macOS path in v1)
- Text only (no voice transcription in v1)
- Telegram only (no Signal / Matrix / etc. — the architecture is messenger-agnostic but the implementation isn't)

## Contributing

Issues and PRs welcome. For security-relevant findings, see `SECURITY.md` for private contact.

## License

[MIT](LICENSE).
