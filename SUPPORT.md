# Support & Platform Status

## Validated

- **macOS, Apple Silicon (arm64)** — validated end-to-end by the maintainer:
  install, doctor, runtime surfaces (AGY statusline, Codex status line and
  helper, Pi footer, Herdr sidebar cards), uninstall/rollback.

## Experimental / unvalidated

- **Intel macOS** — expected to work in principle but not tested; reports
  welcome once public issue tracking exists.

## Not supported

- Linux / WSL
- native Windows

The code is POSIX-oriented, but no claims are made until tested.

## Known limitations

- Upgrades from a previous installed version are unsupported: uninstall first.
- `~/.local/bin` must be on your `PATH` for the commands to resolve.
- Herdr may need a configuration reload or restart after (re)installing
  before new sidebar/plugin configuration is visible.

## OpenCode support

OpenCode support is STATE-ONLY through Herdr's native detection and requires
the Herdr component. CEN HUD provides:

- no OpenCode quota or identity/account display
- no CEN polling, ticker, or background process
- no terminal scraping
- no CEN OpenCode metadata publisher

The card renders whatever lifecycle state Herdr itself detects for the pane;
CEN HUD does not add state vocabulary of its own.
