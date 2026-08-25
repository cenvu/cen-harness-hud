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
| **Codex CLI** | Native TUI status line, one-shot CLI helper, Herdr sidebar card | Model/context rows; structured short/long rate-limit remaining percentages; Herdr card adds identity/plan plus both structured window LEFT % with reset countdown snapshots |
| **Pi Coding Agent** | Native Pi footer widget | DeepSeek key fingerprint and PAYG balance |
| **Herdr multiplexer** | Sidebar agent cards | Compact per-agent identity/quota/balance summaries |

## Platform support

- **macOS on Apple Silicon (arm64): validated** on the maintainer's machine.
- Intel macOS: unvalidated/experimental.
- Linux, WSL, native Windows: not supported.

## Design principles

- **No third-party packages bundled by CEN HUD** — the Python components
  (installer, AGY/Codex/Herdr integrations) use the Python standard library
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
  over stdio. External harnesses may of course perform their own network
  activity according to their own behavior and configuration.
- **Installer credential boundary** — the installer never reads or migrates
  credentials (`auth.json`, keychains, token stores). The optional Pi balance
  integration accesses your existing DeepSeek key in memory at runtime; see
  [PRIVACY.md](PRIVACY.md) for exact disclosure.
- **Fail-closed configuration surgery** — foreign config customizations are
  never overwritten; conflicts abort the install before any mutation.
- **Privacy at rest** — personal-but-non-secret persistent state lives under
  a `0700` directory with `0600` files.

See [INSTALL.md](INSTALL.md) to install, [SUPPORT.md](SUPPORT.md) for
supported platforms, and [PRIVACY.md](PRIVACY.md) for what is stored locally.

## License

[MIT](LICENSE)

## Roadmap and contributions

CEN Harness HUD is intentionally extensible, and contributors are welcome to
add more harness adapters.

**Currently supported:** AGY / Antigravity · Codex CLI · Pi Coding Agent /
DeepSeek balance · Herdr.

**Planned / candidate integrations** (not implemented yet):

- Claude Code
- dsclaude
- OpenCode
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
