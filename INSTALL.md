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

## OpenCode notes

OpenCode support has NO separate `--opencode` installer component. Its
sidebar card is installed/configured by the **Herdr** component:

```sh
./install.sh --herdr
```

OpenCode must already be installed (by you) if you want to use that card;
CEN HUD does not install or authenticate OpenCode. The card shows only the
lifecycle state that Herdr itself detects — CEN HUD publishes no OpenCode
metadata of its own.

## Codex notes

Installing the Codex component may:

1. patch `[tui].status_line` in `~/.codex/config.toml` to the CEN-owned
   native footer shape (model/reasoning, status, context, short-window and
   weekly remaining values)
2. enable `[features].hooks = true` when absent
3. add ONE CEN SessionStart command hook to `~/.codex/hooks.json`
4. preserve your unrelated existing hooks
5. fail closed (CONFLICT) if the owned config surface is foreign/custom

CEN HUD does not install or authenticate Codex. `cen-codex` remains
available as a profile-aware launcher, but telemetry does NOT require it
for normal generic Codex sessions when native session ownership can be
proven.

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

Uninstall behavior depends on the install manifest schema recorded at
install time.

**Schema 2 installs (v0.3.1 and later):**

- The installer keeps private full-file backups for failed-install rollback,
  and the manifest additionally records the exact CEN-owned semantic config
  surfaces (per-key/table entries with their before/after state).
- A clean uninstall reverses ONLY those owned surfaces, and only while each
  one still matches its recorded CEN after-state.
- Unrelated user edits elsewhere in the same file do NOT block the safe
  inverse of owned surfaces.
- A user edit TO an owned surface is preserved and treated as semantic
  drift: it is never overwritten, and drift evidence is retained.
- CEN-created config files are deleted only when no user content remains
  in them.
- Your personal runtime state (`~/.config/herdr/cen-harness-hud-quota/`) is
  kept unless you delete it yourself.

**Schema 1 legacy installs (v0.3.0 and earlier):**

- Remain supported by the newer manager through the conservative v0.3.0
  whole-file/hash uninstall path: patched configs are restored from private
  backups only when the whole file still matches, otherwise the manifest is
  retired to a private archive under
  `~/.config/cen-harness-hud/drift-archive/` with the kept backups.
- Schema-2 ownership begins only after a v0.3.1 installation.

Upgrades from a previous installed version remain uninstall-first: run
`cen-hud uninstall` before installing the new version. In-place upgrades
are not supported.

## Requirements notes

- No `sudo`, no Homebrew, no package-manager changes, no network access.
- The installer never reads or copies credentials; authenticating each
  harness remains a manual step you own.
