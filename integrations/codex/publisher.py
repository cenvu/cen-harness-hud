#!/usr/bin/env python3
"""cen-harness-hud — Pane-Scoped Codex Profile Publisher (Herdr Bridge)

Canonical Source: cen-harness-hud/integrations/codex/publisher.py

Publishes profile-correct Codex display tokens to EXACTLY ONE Herdr pane:
the pane that triggered the current event.

Flow:
  1. Parse HERDR_PLUGIN_EVENT_JSON → extract pane_id
  2. Capture event_seq at event start (monotonic ordering)
  3. Read cen_codex_profile token from event pane via Herdr Unix socket
  4. Validate TAG@PID registration and profile mapping
  5. Query codex app-server with explicit CODEX_HOME
  6. Revalidate registration before publishing
  7. Publish display tokens to event pane ONLY

Token contract (all sanitized + bounded):
  cen_codex_identity   <local-part> · <PLAN>
  cen_codex_window_1   <duration> <LEFT%> · ↻<countdown>   (primary)
  cen_codex_window_2   <duration> <LEFT%> · ↻<countdown>   (secondary)
  cen_codex_summary    cleared (retired token; no stale residue)

Windows are published in deterministic normal order: primary, secondary.
Each window is independently finite-checked. Missing resetsAt → quota
percentage without countdown. No valid windows → "—" fail-closed.

Ownership:
  Source 'cen-codex-bridge' owns ONLY: cen_codex_identity,
    cen_codex_window_1, cen_codex_window_2, cen_codex_summary
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

IDENTITY_TOKEN = "cen_codex_identity"
WINDOW_TOKENS = ("cen_codex_window_1", "cen_codex_window_2")
RETIRED_SUMMARY_TOKEN = "cen_codex_summary"
EMPTY_STATE = "—"


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


# ── Display Token Formatting ──────────────────────────────────────────────────

def format_identity_token(acc_res: Any) -> str:
    """Format the identity token: <sanitized local-part> · <PLAN>.

    Never renders a full email, account id, CODEX_HOME, or credential
    material — only the local-part of the email plus the plan label.
    """
    from status import sanitize_label

    if not acc_res or not isinstance(acc_res, dict):
        return EMPTY_STATE
    acc = acc_res.get("account")
    if not isinstance(acc, dict):
        return EMPTY_STATE

    parts = []
    email = acc.get("email")
    if isinstance(email, str) and email.strip():
        email_str = email.strip()
        if "@" in email_str:
            local_part = email_str.split("@", 1)[0]
            if local_part:
                parts.append(sanitize_label(local_part, max_len=20))
    plan = acc.get("planType")
    if isinstance(plan, str) and plan.strip():
        parts.append(sanitize_label(plan.strip().upper(), max_len=10))

    if not parts:
        return EMPTY_STATE
    return " · ".join(parts)


def format_window_token(window: Any, now_epoch: Optional[float] = None) -> Optional[str]:
    """Format one quota window token: <duration> <LEFT%> · ↻<countdown>.

    - LEFT = clamp(100 - usedPercent, 0..100), finite-checked
    - duration derived structurally from windowDurationMins (never hardcoded)
    - countdown derived only from resetsAt; absent/invalid → no countdown
    Returns None when the window itself is absent/invalid (fail-closed).
    """
    from status import format_countdown, format_duration

    if not isinstance(window, dict):
        return None
    used = window.get("usedPercent")
    if used is None:
        return None
    try:
        used_f = float(used)
        if not math.isfinite(used_f):
            return None
    except (ValueError, TypeError):
        return None

    rem = max(0.0, min(100.0, 100.0 - used_f))
    dur = format_duration(window.get("windowDurationMins")) or "QUOTA"
    text = f"{dur} {int(round(rem))}%"
    countdown = format_countdown(window.get("resetsAt"), now_epoch)
    if countdown:
        text = f"{text} · {countdown}"
    return text


def collect_windows(rl_res: Any) -> list:
    """Collect quota windows in deterministic normal order: primary, secondary.

    Windows are NOT labeled by assumption — duration labels come strictly
    from each window's structured windowDurationMins.
    """
    windows = []
    if rl_res and isinstance(rl_res, dict):
        entries = []
        limits_dict = rl_res.get("rateLimitsByLimitId")
        if limits_dict and isinstance(limits_dict, dict):
            for _, snap in limits_dict.items():
                if isinstance(snap, dict):
                    entries.append(snap)
        else:
            fallback = rl_res.get("rateLimits")
            if fallback and isinstance(fallback, dict):
                entries.append(fallback)

        for entry in entries:
            for key in ("primary", "secondary"):
                win = entry.get(key)
                if isinstance(win, dict):
                    windows.append(win)
    return windows


def build_display_tokens(
    acc_res: Any,
    rl_res: Any,
    now_epoch: Optional[float] = None,
) -> Dict[str, Optional[str]]:
    """Build the bounded Herdr display token set (sanitized, fail-closed).

    - identity: sanitized <local-part> · <PLAN>, else "—"
    - window_1 / window_2: first two valid windows in normal order;
      invalid/absent slots render as "—"
    - retired summary token is explicitly cleared so an older card layout
      can never keep showing stale combined data
    """
    tokens: Dict[str, Optional[str]] = {
        IDENTITY_TOKEN: format_identity_token(acc_res),
        RETIRED_SUMMARY_TOKEN: None,
    }

    valid = []
    for win in collect_windows(rl_res):
        rendered = format_window_token(win, now_epoch)
        if rendered is not None:
            valid.append(rendered)
        if len(valid) == len(WINDOW_TOKENS):
            break

    for i, tok in enumerate(WINDOW_TOKENS):
        tokens[tok] = valid[i] if i < len(valid) else EMPTY_STATE
    return tokens


# ── Main Publisher Logic ──────────────────────────────────────────────────────

def fail_closed_tokens() -> Dict[str, Optional[str]]:
    """Bounded empty state: identity + both windows "—", retired token cleared."""
    tokens: Dict[str, Optional[str]] = {
        IDENTITY_TOKEN: EMPTY_STATE,
        RETIRED_SUMMARY_TOKEN: None,
    }
    for tok in WINDOW_TOKENS:
        tokens[tok] = EMPTY_STATE
    return tokens


def publish_fail_closed(sock_path: str, pane_id: str, event_seq: int) -> None:
    """Publish fail-closed display tokens to the event pane."""
    report_metadata(
        sock_path,
        pane_id,
        BRIDGE_SOURCE,
        event_seq,
        fail_closed_tokens(),
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

    tokens = build_display_tokens(acc_res, rl_res)
    report_metadata(
        sock_path,
        pane_id,
        BRIDGE_SOURCE,
        event_seq,
        tokens,
    )


if __name__ == "__main__":
    main()
