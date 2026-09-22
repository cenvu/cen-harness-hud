# Privacy

CEN HUD has **no CEN-operated backend, no telemetry and no analytics**. One
optional integration (the Pi DeepSeek balance footer) is an explicit network
client — full disclosure below.

## What CEN HUD never does

- never stores the raw DeepSeek API key on disk
- never logs or displays the raw DeepSeek API key
- never sends credentials to CEN (CEN operates no server)
- never sends telemetry or analytics to CEN

## Persistent state (non-secret but personal)

| Integration | Stored | Where |
|---|---|---|
| AGY | sanitized account local-part (truncated), plan tier, quota percentages | `~/.config/herdr/cen-harness-hud-quota/agy/*.json` (`0700` dir, `0600` files) |
| Codex profiles | one-way short profile tag mapped to its CODEX_HOME filesystem path | `~/.config/herdr/cen-harness-hud-quota/profiles/` (`0700` dir, `0600` files) |
| Codex sessions | CODEX_HOME path plus schema/timing metadata, filename is a SHA-256-derived key from the native Codex session id (the raw session id is not used as the filename) | `~/.config/herdr/cen-harness-hud-quota/codex-sessions/` (`0700` dir, `0600` files) |
| Codex snapshots | sanitized account local-part alias, plan tier, normalized quota remaining data, window duration, reset timestamp, fetched/attempt timing, stale flag — hash-keyed by session id | `~/.config/herdr/cen-harness-hud-quota/codex-snapshots/` (`0700` dir, `0600` files) |

No full email address, no OAuth token, no API key, and no `auth.json`
content is stored in any of the above. Quota snapshot metadata IS persisted
locally as described; only money balances (PAYG cash amounts) are never
stored.

Only this persistent AGY/Codex state uses those disk permissions. The Pi
balance integration persists nothing (see below).

## PI EXCEPTION — outbound network with your existing key

The optional Pi balance integration:

- obtains your **existing** DeepSeek API key in memory via the supported
  surfaces: the `DEEPSEEK_API_KEY` environment variable or Pi's ModelRegistry
- computes a short one-way SHA-256 fingerprint of it for display identity
  (rendered as `DS#` plus 4 hash characters, e.g. `DS#1a2b`) — the raw key is
  never persisted, logged or displayed
- sends the key **only** as `Authorization: Bearer` over HTTPS to DeepSeek's
  official balance endpoint (`https://api.deepseek.com/user/balance`)
- receives balance data and caches the formatted result in memory for
  45 seconds, bound to the key fingerprint
- persists nothing: no raw key, no balance sidecar, no cache file — so the
  `0700`/`0600` state-root permissions above do not apply to it

## Never stored anywhere by CEN HUD

- passwords, API keys, OAuth/JWT tokens, cookies
- full email addresses
- money balances (PAYG cash amounts)

## Removal

`cen-hud uninstall` does not delete personal runtime state automatically;
delete `~/.config/herdr/cen-harness-hud-quota/` manually if you want it gone.
