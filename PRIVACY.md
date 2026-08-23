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
| Codex | a filesystem mapping between a hashed profile tag and its CODEX_HOME path — no account identity | `~/.config/herdr/cen-harness-hud-quota/profiles/` (`0700` dir, `0600` files) |

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
- balances for AGY/Codex (subscription-based)

## Removal

`cen-hud uninstall` does not delete personal runtime state automatically;
delete `~/.config/herdr/cen-harness-hud-quota/` manually if you want it gone.
