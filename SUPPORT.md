# Support & Platform Status

## Validated

- **macOS, Apple Silicon (arm64)** — validated end-to-end by the maintainer:
  install, doctor, runtime surfaces (AGY statusline, Codex status line and
  helper, Pi footer, Herdr sidebar cards), uninstall/rollback.

## Experimental / unvalidated

- **Intel macOS** — expected to work in principle but not tested; reports
  welcome once public issue tracking exists.

## Not supported in v0.1.0

- Linux / WSL
- native Windows

The code is POSIX-oriented, but no claims are made until tested.

## Known limitations

- Upgrades from a previous installed version are unsupported: uninstall first.
- `~/.local/bin` must be on your `PATH` for the commands to resolve.
- Herdr sidebar cards require a Herdr restart after (re)installing so the
  running server picks up the plugin registration.
