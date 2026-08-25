# Installing CEN Harness HUD

## Prerequisites

- macOS (arm64 validated)
- **Python 3 requirements:**
  - `python3` 3.9+ is sufficient for the AGY and Pi components.
  - **`python3` 3.11+ is REQUIRED when installing the Codex or Herdr
    components** — their config patching needs the stdlib `tomllib` module.
    On older Pythons those components refuse to run (fail-closed) rather than
    patch TOML blindly; `install.sh` prints a note when it detects this.
- At least one supported harness installed and authenticated **by you**:
  AGY (Antigravity CLI), Codex CLI, Pi Coding Agent, and/or Herdr.
  CEN HUD does not install, bundle, authenticate, or migrate any harness.

## Install

```sh
./install.sh              # installs all detected components
./install.sh --agy        # or select components explicitly
./install.sh --agy --codex --pi --herdr
```

The installer will:

1. copy the product into `~/.local/share/cen-harness-hud/<version>/`
2. create command links in `~/.local/bin/` (`cen-hud`, `cen-codex`,
   `cen-codex-status`)
3. patch only the exact config keys it owns, after taking a private backup
   (`0600`) — any foreign customization aborts the install with a CONFLICT
   instead of being overwritten
4. write an install manifest (`~/.config/cen-harness-hud/install-manifest.json`,
   mode `0600`) recording every mutation for exact rollback

If `~/.local/bin` is not on your `PATH`, add it:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

## Verify

```sh
cen-hud doctor
```

`doctor` is strictly read-only and reports PASS/WARN/FAIL/SKIP per check,
including privacy-at-rest permissions.

## Re-running / upgrading

Running the installer again at the same version is a no-op
(`ALREADY_INSTALLED`). Upgrades from a previous installed version are not
supported: run `cen-hud uninstall` first.

## Uninstall

```sh
cen-hud uninstall
```

Removes exactly what the manifest recorded — restoring patched configs from
private backups only if you have not edited them since install — and keeps
your personal runtime state (`~/.config/herdr/cen-harness-hud-quota/`)
unless you delete it yourself.

If any file was modified since install, it is left untouched (your edits are
never overwritten). In that case the install manifest is retired to a private
archive under `~/.config/cen-harness-hud/drift-archive/` together with the
kept backups, so no active manifest falsely claims the product is still
installed; reinstalling afterwards proceeds through normal conflict checks
and will still refuse to overwrite a genuinely conflicting customization.

## Requirements notes

- No `sudo`, no Homebrew, no package-manager changes, no network access.
- The installer never reads or copies credentials; authenticating each
  harness remains a manual step you own.
