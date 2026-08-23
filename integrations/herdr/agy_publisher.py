#!/usr/bin/env python3
"""cen-harness-hud — Compact AGY Herdr Summary Publisher

Canonical Source: cen-harness-hud/integrations/herdr/agy_publisher.py

UI contract: the AGY agent card is FOUR rows. This publisher owns the
bottom TWO, as independent tokens built directly from the two independent
sidecars (never combined-then-split):

    ROW 3  cen_agy_identity = <account-local-part> · <PLAN>
    ROW 4  cen_agy_quota    = G <G5H>/<GW> · C/G <C5H>/<CW>

Identity examples:
  user.alt · PRO        (both present)
  user.alt              (email only)
  PRO                   (plan only)
  null                  (both missing -> token cleared, row disappears)

Quota semantics (, unchanged): all values REMAINING / LEFT percentages,
NO min() collapse —
  G   A/B = Gemini 5H LEFT% / Gemini Weekly LEFT%
  C/G A/B = shared Claude/GPT 5H LEFT% / Claude/GPT Weekly LEFT%

Quota examples:
  G 87/64 · C/G 41/72
  G 100/—               (missing horizon renders a truthful dash)
  C/G 40/51             (absent Gemini family omitted)
  null                  (no usable windows -> token cleared, row disappears)

Missing values are NEVER converted to 0 and NEVER min-collapsed.
fail-closed sidecar behavior is preserved exactly.

Data sources (read-only):
  ~/.config/herdr/cen-harness-hud-quota/agy/agy-quota.json       (/side-channel,
      written by integrations/agy/status.py from the SAME structured upstream
      quota dict it already parses for the native footer)
  ~/.config/herdr/cen-harness-hud-quota/agy/agy-identity.json     (integrations/agy/status.py)

Ownership:
  Sources 'cen-agy-summary' own ONLY: cen_agy_identity, cen_agy_quota.
  The legacy combined token cen_agy_summary is retired: it was
  explicitly cleared once during migration and is never published again.
  It MUST NOT touch upstream herdr-agent-quota tokens or the native footer.

No reset countdowns, no reset timestamps, no RESET credits, no context —
those remain inside the native AGY footer.

Fail-closed: a missing/empty sidecar clears its own token only; the other
row survives independently.
"""

import json
import math
import os
import sys
import time
from typing import Any, Dict, Optional

sys.path.insert(
    0,
    os.path.dirname(os.path.realpath(os.path.abspath(__file__))),
)

from herdr_socket import report_metadata, resolve_socket_path # noqa: E402

SOURCE = "cen-agy-summary"
TOKEN_IDENTITY = "cen_agy_identity"
TOKEN_QUOTA = "cen_agy_quota"
STATE_DIR = os.path.expanduser("~/.config/herdr/cen-harness-hud-quota/agy")
QUOTA_PATH = os.path.join(STATE_DIR, "agy-quota.json")
IDENTITY_PATH = os.path.join(STATE_DIR, "agy-identity.json")

MAX_PART_LEN = 32
MISSING = "—"


def sanitize(text: Any) -> str:
    if text is None:
        return ""
    s = str(text)
    s = "".join(ch for ch in s if ch >= " " and ch != "\x7f")
    return s.strip()[:MAX_PART_LEN]


def load_json(path: str) -> Optional[Any]:
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _fmt_pct(value: Any) -> Optional[str]:
    """Normalize one remaining-percent value to a 0-100 int string, else None."""
    try:
        rem_f = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(rem_f):
        return None
    rem_f = max(0.0, min(100.0, rem_f))
    return str(int(round(rem_f)))


def format_family_segment(pools_by_key: Dict[str, Dict[str, Any]], prefix: str) -> Optional[str]:
    """Build '<prefix> <5H>/<W>' for one family; None when both horizons are absent."""
    five = _fmt_pct(pools_by_key.get("five_hour", {}).get("remaining_percent"))
    weekly = _fmt_pct(pools_by_key.get("weekly", {}).get("remaining_percent"))
    if five is None and weekly is None:
        return None
    return f"{prefix} {five or MISSING}/{weekly or MISSING}"


def collect_pools(snapshot: Any) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Return {'gemini': {horizon: pool}, 'thirdparty': {horizon: pool}}."""
    result: Dict[str, Dict[str, Dict[str, Any]]] = {}
    windows = snapshot.get("windows") if isinstance(snapshot, dict) else None
    if not isinstance(windows, list):
        return result
    for win in windows:
        if not isinstance(win, dict):
            continue
        family = win.get("family")
        horizon = win.get("horizon")
        if family not in ("gemini", "thirdparty"):
            continue
        if horizon not in ("five_hour", "weekly"):
            continue
        if _fmt_pct(win.get("remaining_percent")) is None:
            continue
        result.setdefault(family, {})[horizon] = win
    return result


def format_agy_identity(identity: Any) -> Optional[str]:
    """Build '<local> · <PLAN>' (or single part); None when both parts missing."""
    if not isinstance(identity, dict):
        return None
    local = sanitize(identity.get("email_local"))
    plan = sanitize(identity.get("plan"))
    parts = []
    if local:
        parts.append(local)
    if plan:
        parts.append(plan)
    if not parts:
        return None
    return " · ".join(parts)


def format_agy_quota(quota_snapshot: Any) -> Optional[str]:
    """Build 'G <a>/<b> · C/G <a>/<b>'; None when no usable window exists."""
    pools = collect_pools(quota_snapshot)
    if not pools:
        return None
    parts = []
    gemini_seg = format_family_segment(pools.get("gemini", {}), "G")
    if gemini_seg:
        parts.append(gemini_seg)
    thirdparty_seg = format_family_segment(pools.get("thirdparty", {}), "C/G")
    if thirdparty_seg:
        parts.append(thirdparty_seg)
    if not parts:
        return None
    return " · ".join(parts)


def main() -> None:
    raw_event = os.environ.get("HERDR_PLUGIN_EVENT_JSON", "")
    if not raw_event.strip():
        sys.exit(0)

    try:
        event_data = json.loads(raw_event)
    except Exception:
        sys.exit(0)

    pane_id = None
    if isinstance(event_data, dict):
        data = event_data.get("data", event_data)
        pane_id = data.get("pane_id") or data.get("paneId")
    if not pane_id:
        sys.exit(0)

    sock_path = resolve_socket_path()
    if not sock_path:
        sys.exit(0)

    identity = format_agy_identity(load_json(IDENTITY_PATH)) # None clears the token -> row disappears
    quota = format_agy_quota(load_json(QUOTA_PATH)) # None clears the token -> row disappears
    report_metadata(
        sock_path,
        pane_id,
        SOURCE,
        time.time_ns(),
        {
            TOKEN_IDENTITY: identity,
            TOKEN_QUOTA: quota,
        },
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
