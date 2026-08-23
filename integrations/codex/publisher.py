#!/usr/bin/env python3
"""cen-harness-hud — Pane-Scoped Codex Profile Publisher (Herdr Bridge)

Canonical Source: cen-harness-hud/integrations/codex/publisher.py

Publishes profile-correct Codex quota summary to EXACTLY ONE Herdr pane:
the pane that triggered the current event.

Flow:
  1. Parse HERDR_PLUGIN_EVENT_JSON → extract pane_id
  2. Capture event_seq at event start (monotonic ordering)
  3. Read cen_codex_profile token from event pane via Herdr Unix socket
  4. Validate TAG@PID registration and profile mapping
  5. Query codex app-server with explicit CODEX_HOME
  6. Revalidate registration before publishing
  7. Publish cen_codex_summary to event pane ONLY

Ownership:
  Source 'cen-codex-bridge' owns ONLY: cen_codex_summary
  Source 'cen-codex-launcher' owns ONLY: cen_codex_profile
  Bridge MUST NOT clear or modify cen_codex_profile

Security:
  - AUTH.JSON READ = NO
  - No process-env scraping (ps eww, /proc)
  - No provider-wide pane enumeration
  - No shell=True
"""

import hashlib
import json
import math
import os
import re
import sys
import time
from typing import Any, Dict, Optional, Tuple

sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.realpath(os.path.abspath(__file__))),
        "..", "herdr",
    ),
)

from herdr_socket import pane_get_tokens, report_metadata, resolve_socket_path # noqa: E402

# ── Constants ──────────────────────────────────────────────────────────────────

BRIDGE_SOURCE = "cen-codex-bridge"
PROFILES_DIR = os.path.expanduser(
    "~/.config/herdr/cen-harness-hud-quota/profiles"
)
TAG_PATTERN = re.compile(r"^[0-9A-F]{12}$")
PID_PATTERN = re.compile(r"^[0-9]+$")


# ── Profile Validation ────────────────────────────────────────────────────────

def parse_registration(raw: Optional[str]) -> Optional[Tuple[str, int]]:
    """Parse cen_codex_profile = TAG@PID. Returns (tag, pid) or None."""
    if not raw or not isinstance(raw, str):
        return None
    parts = raw.strip().split("@", 1)
    if len(parts) != 2:
        return None
    tag, pid_str = parts
    if not TAG_PATTERN.match(tag):
        return None
    if not PID_PATTERN.match(pid_str):
        return None
    pid = int(pid_str)
    if pid <= 0 or pid > 4194304: # reasonable PID range
        return None
    return tag, pid


def is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still alive (non-secret liveness)."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
 # Process exists but we can't signal it — it's alive
        return True
    except Exception:
        return False


def load_and_validate_mapping(tag: str) -> Optional[str]:
    """Load profile mapping for TAG, validate, return canonical codex_home or None."""
    mapping_path = os.path.join(PROFILES_DIR, f"{tag}.json")
    try:
        with open(mapping_path, "r") as f:
            data = json.load(f)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None
    if data.get("schema") != 1:
        return None
    if data.get("tag") != tag:
        return None

    codex_home = data.get("codex_home")
    if not codex_home or not isinstance(codex_home, str):
        return None

 # Recompute tag from stored codex_home and verify
    canonical = os.path.realpath(codex_home)
    recomputed_tag = hashlib.sha256(canonical.encode()).hexdigest()[:12].upper()
    if recomputed_tag != tag:
        return None

 # Verify directory exists
    if not os.path.isdir(canonical):
        return None

    return canonical


# ── Quota Formatting ──────────────────────────────────────────────────────────

def format_compact_summary(acc_res: Any, rl_res: Any) -> str:
    """Format a compact Herdr sidebar summary from account/read and rateLimits/read.

    UI contract: identity · plan · <duration> <LEFT%>.
    No reset countdowns, no reset-credit count, no context details
    (those remain in cen-codex-status detailed output).

    Example: user.primary · PLUS · 7D 0%
    """
 # Import formatting helpers from status.py (safe: guarded by __name__ check)
    from status import (
        format_duration,
        normalize_quota_window,
        sanitize_label,
    )

    parts = []

 # 1. Identity: prefer local-part over full email
    if acc_res and isinstance(acc_res, dict):
        acc = acc_res.get("account", {})
        if isinstance(acc, dict):
            email = acc.get("email")
            if email and isinstance(email, str):
                email_str = email.strip()
                local_part = email_str.split("@")[0] if "@" in email_str else email_str
                parts.append(sanitize_label(local_part, max_len=20))
            plan = acc.get("planType")
            if plan and isinstance(plan, str):
                parts.append(sanitize_label(plan.strip().upper(), max_len=10))

 # 2. Rate limits
    if rl_res and isinstance(rl_res, dict):
        limits_dict = rl_res.get("rateLimitsByLimitId")
        limits_list = []
        if limits_dict and isinstance(limits_dict, dict):
            for dk, snap in limits_dict.items():
                if isinstance(snap, dict):
                    eff_id = snap.get("limitId") or str(dk)
                    snap_copy = dict(snap)
                    snap_copy["limitId"] = sanitize_label(eff_id)
                    limits_list.append(snap_copy)
        else:
            fallback = rl_res.get("rateLimits")
            if fallback and isinstance(fallback, dict):
                limits_list = [fallback]

        has_multi = len(limits_list) > 1

        for entry in limits_list:
            limit_id = sanitize_label(entry.get("limitId", ""))
            p_win = entry.get("primary")
            if p_win and isinstance(p_win, dict):
                used = p_win.get("usedPercent")
                if used is not None:
                    try:
                        used_f = float(used)
                        if math.isfinite(used_f):
                            rem = max(0.0, min(100.0, 100.0 - used_f))
                            dur = format_duration(p_win.get("windowDurationMins"))
                            segment = f"{dur} {int(round(rem))}%"
                            if has_multi and limit_id and limit_id != "codex":
                                segment = f"[{limit_id}] {segment}"
                            parts.append(segment)
                    except (ValueError, TypeError):
                        pass

    if not parts:
        return "quota —"

    return " · ".join(parts)


# ── Main Publisher Logic ──────────────────────────────────────────────────────

def publish_fail_closed(sock_path: str, pane_id: str, event_seq: int) -> None:
    """Publish fail-closed state: quota — to the event pane."""
    report_metadata(
        sock_path,
        pane_id,
        BRIDGE_SOURCE,
        event_seq,
        {"cen_codex_summary": "quota —"},
    )


def main() -> None:
 # 1. Parse event
    raw_event = os.environ.get("HERDR_PLUGIN_EVENT_JSON", "")
    if not raw_event.strip():
        sys.exit(0)

    try:
        event_data = json.loads(raw_event)
    except Exception:
        sys.exit(0)

 # Extract pane_id from event
    pane_id = None
    if isinstance(event_data, dict):
        data = event_data.get("data", event_data)
        pane_id = data.get("pane_id") or data.get("paneId")
    if not pane_id:
        sys.exit(0)

 # 2. Capture event_seq at START (monotonic ordering)
    event_seq = time.time_ns()

 # 3. Herdr socket
    sock_path = resolve_socket_path()
    if not sock_path:
        sys.exit(0)

 # 4. Read registration from event pane
    tokens = pane_get_tokens(sock_path, pane_id)
    if tokens is None:
        publish_fail_closed(sock_path, pane_id, event_seq)
        sys.exit(0)

    raw_profile = tokens.get("cen_codex_profile")
    parsed = parse_registration(raw_profile)
    if parsed is None:
        publish_fail_closed(sock_path, pane_id, event_seq)
        sys.exit(0)

    tag, launcher_pid = parsed

 # 5. Validate launcher PID alive
    if not is_pid_alive(launcher_pid):
        publish_fail_closed(sock_path, pane_id, event_seq)
        sys.exit(0)

 # 6. Validate profile mapping
    codex_home = load_and_validate_mapping(tag)
    if codex_home is None:
        publish_fail_closed(sock_path, pane_id, event_seq)
        sys.exit(0)

 # 7. Query Codex app-server with explicit CODEX_HOME
 #    Import from status.py (safe: guarded by __name__ check)
    from status import query_app_server

    acc_res, rl_res = query_app_server(codex_home=codex_home)

 # 8. REVALIDATION: re-read registration before publishing
    tokens2 = pane_get_tokens(sock_path, pane_id)
    if tokens2 is None:
        sys.exit(0) # pane gone — let next event handle

    raw_profile2 = tokens2.get("cen_codex_profile")
    parsed2 = parse_registration(raw_profile2)
    if parsed2 is None or parsed2[0] != tag or parsed2[1] != launcher_pid:
 # Registration changed during query — old result rejected
        sys.exit(0)

 # Re-check launcher still alive
    if not is_pid_alive(launcher_pid):
        sys.exit(0)

 # 9. Format and publish to event pane ONLY
    if acc_res is None and rl_res is None:
        publish_fail_closed(sock_path, pane_id, event_seq)
        sys.exit(0)

    summary = format_compact_summary(acc_res, rl_res)
    report_metadata(
        sock_path,
        pane_id,
        BRIDGE_SOURCE,
        event_seq,
        {"cen_codex_summary": summary},
    )


if __name__ == "__main__":
    main()
