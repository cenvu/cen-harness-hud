# CEN Harness HUD

**CEN Harness HUD** is a terminal-first, local-first HUD (heads-up display) for
AI coding agents. While you work in any directory, it shows at a glance:

- **who** account identity is in use
- **what** plan / provider tier applies
- **how much** quota or balance remains
- **when** the active window resets

It renders through native surfaces of your existing tools — no web dashboard,
no resident daemon of its own, no database.

## Integrations

| Integration | Display surface | Shows |
|---|---|---|
| **AGY** (Antigravity CLI) | Native statusline footer + Herdr sidebar card | Account identity/plan, four quota pools (Gemini 5H/W, Claude/GPT 5H/W), reset countdowns |
| **Codex CLI** | Native TUI status line, one-shot CLI helper, Herdr sidebar card | Native footer: model/reasoning, native run status, context remaining, short-window and weekly remaining % (no reset countdown in the native footer through current built-in items); helper: sanitized short account alias + plan with structured quota/reset info; Herdr card: native lifecycle state, alias/plan, WEEKLY remaining % with reset countdown snapshot — short-window quota stays native-footer-only and is NOT duplicated in Herdr. Explicit raw `CODEX_HOME` profiles can receive CEN telemetry prerequisites; native session identity remains owned by the official Herdr Codex integration. |
| **Pi Coding Agent** | Native Pi footer widget | DeepSeek key fingerprint and PAYG balance |
| **OpenCode** | Herdr sidebar card (requires Herdr) | Herdr-native lifecycle state. State is supplied by Herdr's own OpenCode detection, not a CEN OpenCode publisher. Live validation observed idle/working/done; other Herdr-native states render when detected |
| **Claude Code** | Native StatusLine + Herdr sidebar card | Herdr-native lifecycle state; model/context from structured StatusLine JSON; optional five-hour/seven-day Claude rate-limit LEFT% and reset countdown when supplied; no identity or plan row; no fabricated quota when unavailable |
| **Herdr multiplexer** | Sidebar agent cards | Compact per-agent identity/quota/balance summaries |

### dsclaude compatibility

dsclaude reuses the Claude Code harness adapter and Herdr-native Claude
state/session architecture. Model and context metadata may come from the
Claude StatusLine JSON payload. When `ANTHROPIC_BASE_URL` is present, the
Claude adapter suppresses Claude.ai subscription quota and reset metadata.
CEN HUD does not currently provide a separate DeepSeek or other
custom-provider quota adapter for dsclaude.

### Raw Codex multi-profile support

Codex profile selection uses the normal `CODEX_HOME` environment variable. CEN
HUD can add telemetry prerequisites to explicitly supplied, existing
HOME-relative profile directories through repeatable `--codex-home` installer
arguments. The official Herdr Codex integration owns native session identity;
CEN HUD maps that structured session to its profile for local attribution.
Raw `CODEX_HOME` profiles no longer require the legacy `cen-codex` launcher for
HUD attribution when both integrations are installed.

## Platform support

- **macOS on Apple Silicon (arm64): validated** on the maintainer's machine.
- Intel macOS: unvalidated/experimental.
- Linux, WSL, native Windows: not supported.

## Design principles

- **No third-party packages bundled by CEN HUD** — the Python components
  (installer, AGY/Claude/Codex/Herdr integrations) use the Python standard library
  only; the Pi extension is TypeScript using Node built-ins supplied by the
  Pi runtime. CEN HUD installs no runtime and bundles no npm/Python package.
- **Offline installer** — the CEN HUD installer itself requires no network,
  downloads nothing, needs no `sudo` or Homebrew. (Runtime network behavior
  has one direct exception — see below.)
- **No CEN-operated backend** — CEN HUD has no server and adds no analytics
  or telemetry. The only DIRECT network client implemented by CEN HUD is the
  optional Pi balance feature, which queries DeepSeek's official balance
  endpoint with *your* existing DeepSeek API key. The Codex integration does
  not implement its own HTTP client; it invokes the external Codex app-server
  over stdio (structured RPC) and learns the active profile from the native
  SessionStart hook. External harnesses may of course perform their own network
  activity according to their own behavior and configuration.
- **Installer credential boundary** — the installer never reads or migrates
  credentials (`auth.json`, keychains, token stores). The optional Pi balance
  integration accesses your existing DeepSeek key in memory at runtime; see
  [PRIVACY.md](PRIVACY.md) for exact disclosure.
- **Fail-closed configuration surgery** — foreign config customizations are
  never overwritten; conflicts abort the install before any mutation.
- **Per-surface uninstall ownership** — schema-2 uninstall tracks CEN
  ownership per config surface, so unrelated user edits survive without
  restoring stale whole-file CEN state.
- **Privacy at rest** — personal-but-non-secret persistent state lives under
  a `0700` directory with `0600` files.

See [INSTALL.md](INSTALL.md) to install, [SUPPORT.md](SUPPORT.md) for
supported platforms, and [PRIVACY.md](PRIVACY.md) for what is stored locally.

## License

[MIT](LICENSE)

## Roadmap and contributions

CEN Harness HUD is intentionally extensible, and contributors are welcome to
add more harness adapters.

**Currently supported:** AGY / Antigravity · Claude Code · Codex CLI · Pi
Coding Agent / DeepSeek balance · OpenCode (Herdr-native state card) · Herdr.

**Planned / candidate integrations** (not implemented yet):

- Hermes
- DSH
- other coding harnesses/providers where a stable native or structured status
  surface exists

New integrations should prefer this model:

```
HARNESS
  ↓
native / structured truth
  ↓
normalized identity / plan / quota / balance
  ↓
native status surface + optional Herdr metadata
```

Contribution principles:

- **Native-first:** prefer official/native structured surfaces over scraping.
- Never read raw credential stores when a supported surface exists.
- Fail closed on config mutation; never overwrite foreign customizations.
- Local-first: no CEN telemetry or backend.
- Preserve the privacy-at-rest rules (`0700` state root, `0600` files).
- Add `cen-hud doctor` coverage for anything new.
- Add FAKE_HOME installer/uninstaller tests.
- Avoid duplicating provider logic across multiple harness wrappers — wrapper
  harnesses such as dsclaude may benefit from separating a HARNESS ADAPTER
  from a PROVIDER ADAPTER where practical.

PRs for new adapters and provider integrations are welcome.
