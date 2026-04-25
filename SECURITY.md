# Security

ClaudeBridge is a **single-user** tool. It assumes:

- One owner, one Telegram bot, one whitelisted Telegram user ID.
- The owner trusts files (CLAUDE.md, configs) under their `Project/` tree.

Do **not** deploy this for more than one user, or expose the daemon to a network
beyond outbound HTTPS to api.telegram.org and api.anthropic.com.

## Threat model summary

See `PLAN.md` for the full table. Headline mitigations:

- **No inbound port.** Long-poll only.
- **Hard whitelist** on `effective_user.id`; private-chat-only; `chat_id == user_id` enforced.
- **Per-scope `--allowed-tools` whitelist** (denylist-by-omission).
- **Global `--disallowed-tools`** — no `curl`, `nc`, `ssh`, `*base64*`, no read of `~/.aws/`, `~/.claude/.credentials*`. These stack on top of `~/.claude/settings.json` denies (`deny > ask > allow` precedence).
- **systemd hardening** — `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only` with explicit `ReadWritePaths`, `LimitCORE=0`.
- **Conflict (token-compromise) detection** — daemon exits 78 → `RestartPreventExitStatus=78` keeps it dead → `~/.claude_bridge/deadletter.flag` records the event.
- **Daily + per-call cost caps** to limit damage from accidental loops or compromise.

## Out of scope

- Compromised Telegram account on the owner's phone (Telegram 2FA / Registration Lock helps; this is not addressed by the daemon).
- Indirect prompt injection through tool outputs in scopes that grant `Bash`, `Edit`, or `Write` (e.g., `git diff` of a poisoned repo, README of a freshly-installed dep). Keep sensitive scopes read-only.
- Multi-tenant deployment.

## Persistence to be aware of

Each Telegram message and Claude reply is persisted in three places, two of
which are NOT controlled by this code:

1. **Telegram cloud** — full plaintext. Bot API is not E2E.
2. **Anthropic API logs** — per Anthropic's retention policy.
3. **`~/.claude/projects/<cwd>/<session-uuid>.jsonl`** — Claude Code itself
   writes the full session transcript here. This is how `--resume` works.
   `install.sh` adds `.claude/` and `*.jsonl` to `.gitignore` in every scoped
   project to prevent accidental `git add` exposure, but the files still exist
   on disk.

Don't paste third-party PII, customer data, or secrets into the bridge chat.

## Reporting

For serious issues (auth bypass, exfiltration paths, indirect-injection chains
that defeat the deny list), please contact the maintainer privately rather
than opening a public issue.
