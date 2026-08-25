#!/usr/bin/env python3
"""cen-harness-hud — Profile-Aware Codex Launcher

Canonical Source: cen-harness-hud/integrations/codex/launcher.py
Installed Command: ~/.local/bin/cen-codex

Launches Codex with an explicit CODEX_HOME profile, optionally registering
the profile on the current Herdr pane for profile-aware sidebar display.

Usage:
  cen-codex # DEFAULT (~/.codex)
  cen-codex --home <PATH> # explicit home
  CODEX_HOME=<PATH> cen-codex # via environment

Precedence: --home > CODEX_HOME env > ~/.codex

Inside Herdr:
  1. Computes TAG = SHA256(realpath(home))[0:12].upper()
  2. Writes profile mapping to ~/.config/herdr/.../profiles/<TAG>.json
  3. Registers cen_codex_profile=TAG@PID on the current pane
  4. Launches codex as foreground child
  5. On ANY exit path: terminates a still-alive child (zero orphan) and runs
     OWNERSHIP-AWARE cleanup — metadata is cleared ONLY if the pane still
     carries this launcher's exact TAG@PID, so an older exiting launcher can
     never clobber a newer launcher's registration/summary

Outside Herdr:
  Simply launches codex with selected CODEX_HOME. No error.

Security:
  - AUTH.JSON READ = NO
  - No shell=True
  - No process-env scraping
  - Profile token contains only TAG@PID (no path, no email, no credential)
  - Mapping files are mode 0600
"""

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Optional

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.realpath(os.path.abspath(__file__))),
        "..", "herdr",
    ),
)

from herdr_socket import (
    pane_get_tokens,
    report_metadata,
    resolve_socket_path,
) # noqa: E402

# ── Constants ──────────────────────────────────────────────────────────────────

LAUNCHER_SOURCE = "cen-codex-launcher"
BRIDGE_SOURCE = "cen-codex-bridge"
PROFILES_DIR = os.path.expanduser(
    "~/.config/herdr/cen-harness-hud-quota/profiles"
)
DEFAULT_CODEX_HOME = os.path.expanduser("~/.codex")


# ── Profile Management ────────────────────────────────────────────────────────

def compute_tag(codex_home: str) -> str:
    """Compute 12-char uppercase hex tag from canonical CODEX_HOME path."""
    canonical = os.path.realpath(os.path.expanduser(codex_home))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12].upper()


def write_profile_mapping(tag: str, codex_home: str) -> bool:
    """Write profile mapping file atomically. Returns True on success."""
    canonical = os.path.realpath(os.path.expanduser(codex_home))

 # Ensure profiles directory exists with mode 0700
    try:
        os.makedirs(PROFILES_DIR, mode=0o700, exist_ok=True)
 # makedirs(mode=) does NOT tighten an already-existing
 # directory created with broader permissions — correct deliberately.
        os.chmod(PROFILES_DIR, 0o700)
    except OSError:
        return False

    mapping = {
        "schema": 1,
        "tag": tag,
        "codex_home": canonical,
    }

 # Atomic write: temp file + rename
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=PROFILES_DIR, prefix=f".{tag}-", suffix=".tmp"
        )
        with os.fdopen(fd, "w") as f:
            json.dump(mapping, f, indent=2)
            f.write("\n")
        os.chmod(tmp_path, 0o600)
        target = os.path.join(PROFILES_DIR, f"{tag}.json")
        os.replace(tmp_path, target) # atomic overwrite guarantee
        return True
    except Exception:
 # Clean up temp file on failure
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return False


# ── Herdr Environment Detection ──────────────────────────────────────────────

def detect_herdr() -> Optional[dict]:
    """Detect Herdr environment. Returns dict with socket/pane info or None.

    Tolerant detection: requires HERDR_PANE_ID only. Socket path resolves via
    HERDR_SOCKET_PATH with a default fallback, so behavior does not depend on
    unverified environment variable semantics.
    """
    pane_id = os.environ.get("HERDR_PANE_ID")
    if not pane_id:
        return None
    sock_path = resolve_socket_path()
    if not sock_path:
        return None
    return {"socket_path": sock_path, "pane_id": pane_id}


# ── Ownership-Aware Cleanup ──────────────────────────────────────────────────

def ownership_aware_cleanup(
    sock_path: str,
    pane_id: str,
    own_registration: str,
) -> bool:
    """Clear launcher-owned metadata ONLY if this launcher still owns the pane.

    Reads cen_codex_profile from the SAME pane via raw socket and compares it
    to this launcher's exact TAG@PID registration. Cleanup happens only on an
    exact match, so an older exiting launcher can never clobber a newer
    launcher's registration or summary.

    On match: clears cen_codex_profile (launcher-owned) and fail-closes all
    bridge-owned Codex display tokens (cen_codex_identity, both window
    tokens, retired cen_codex_summary), so no account identity or quota
    from this profile lingers on the pane after exit. The fresh time_ns()
    sequence is monotonic for each source; because we only act while still
    owning the registration, no newer session's data can exist on this pane
    at that moment (residual TOCTOU window is milliseconds and self-heals on
    the next event).

    Returns True if cleanup was performed.
    """
    tokens = pane_get_tokens(sock_path, pane_id)
    if tokens is None:
        return False # pane unreadable/gone — touch nothing

    current = tokens.get("cen_codex_profile")
    if current != own_registration:
 # absent, malformed, or owned by another/newer launcher — do NOTHING
        return False

    seq = time.time_ns()
    report_metadata(
        sock_path, pane_id, LAUNCHER_SOURCE, seq,
        {"cen_codex_profile": None},
    )
    report_metadata(
        sock_path, pane_id, BRIDGE_SOURCE, time.time_ns(),
        {
            "cen_codex_identity": "—",
            "cen_codex_window_1": "—",
            "cen_codex_window_2": "—",
            "cen_codex_summary": None,
        },
    )
    return True


def _terminate_child(proc: Optional[subprocess.Popen]) -> None:
    """Bounded child termination: SIGTERM → wait → SIGKILL only if needed."""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=2.0)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=1.0)
        except Exception:
            pass


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cen-codex",
        description="Profile-aware Codex launcher with Herdr HUD integration",
    )
    parser.add_argument(
        "--home",
        metavar="PATH",
        help="Explicit CODEX_HOME path (overrides CODEX_HOME env)",
    )
    args, remaining = parser.parse_known_args()

 # Resolve CODEX_HOME: --home > env > default
    if args.home:
        codex_home = args.home
    elif os.environ.get("CODEX_HOME"):
        codex_home = os.environ["CODEX_HOME"]
    else:
        codex_home = DEFAULT_CODEX_HOME

 # Canonicalize and validate
    canonical_home = os.path.realpath(os.path.expanduser(codex_home))
    if not os.path.isdir(canonical_home):
        print(f"cen-codex: CODEX_HOME directory does not exist: {canonical_home}",
              file=sys.stderr)
        return 1

 # Find real codex binary
    codex_bin = shutil.which("codex")
    if not codex_bin:
        print("cen-codex: codex not found in PATH", file=sys.stderr)
        return 1

 # Compute profile tag
    tag = compute_tag(canonical_home)

 # Detect Herdr
    herdr = detect_herdr()
    registered = False
    own_registration = None

    if herdr:
 # Write profile mapping
        if not write_profile_mapping(tag, canonical_home):
            print("cen-codex: warning: failed to write profile mapping",
                  file=sys.stderr)

 # Register on pane
        launcher_pid = os.getpid()
        own_registration = f"{tag}@{launcher_pid}"
        reg_seq = time.time_ns()

        registered = report_metadata(
            herdr["socket_path"],
            herdr["pane_id"],
            LAUNCHER_SOURCE,
            reg_seq,
            {"cen_codex_profile": own_registration},
        )

 # Build child environment with explicit CODEX_HOME
    child_env = os.environ.copy()
    child_env["CODEX_HOME"] = canonical_home

 # Launch Codex as foreground child (simplest TTY architecture)
 # Child inherits stdin/stdout/stderr and the foreground process group
    proc = None
    exit_code = 1

    try:
        proc = subprocess.Popen(
            [codex_bin] + remaining,
            env=child_env,
 # No start_new_session, no setsid — inherit foreground pgrp
        )
        exit_code = proc.wait()
    except KeyboardInterrupt:
 # Ctrl-C: child receives SIGINT from the terminal too (shared pgrp)
        exit_code = 130 # conventional SIGINT exit code
    except SystemExit:
 # Raised by the SIGTERM handler (143); propagates after finally
        raise
    except Exception:
        exit_code = 1
    finally:
 # Zero-orphan invariant: terminate child on ANY exit path if alive
        _terminate_child(proc)
 # Ownership-aware metadata cleanup (never clobbers newer launchers)
        if herdr and registered and own_registration:
            ownership_aware_cleanup(
                herdr["socket_path"], herdr["pane_id"], own_registration
            )

    return exit_code


def _handle_sigterm(signum, frame):
    """Handle SIGTERM: raise SystemExit to trigger finally blocks."""
    raise SystemExit(128 + signum)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_sigterm)
    sys.exit(main())
