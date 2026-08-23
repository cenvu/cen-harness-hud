#!/usr/bin/env python3
"""cen-harness-hud — AGY / Antigravity Statusline Integration

Canonical Source: cen-harness-hud/integrations/agy/status.py

Native statusline script for AGY (Antigravity CLI 1.1.18+).
Pure payload-driven design:
- Receives official session JSON from AGY via stdin
- Reads native quota windows (gemini-5h, gemini-weekly, 3p-5h, 3p-weekly)
- Truthful quota semantics: 3p represents shared Claude/GPT third-party quota
- Truthful account identity: Account email/local-part visible across all width modes
- Normalizes remaining quota percentage (LEFT / REMAINING)
- Formats relative reset countdowns from reset_in_seconds / reset_time
- 3-tier adaptive width rendering (Wide, Medium, Narrow)
- Zero external dependencies, zero daemons, zero process scanning, zero caches
- Safe fallback without tracebacks or blocking AGY
- privacy-at-rest: sidecar state dir 0700 / sidecar files 0600
"""

import sys
import os
import json
import re
import select
import shutil
from datetime import datetime, timezone

# ── CEN Herdr side-channel state ───────────────────────────────────────────────
# identity is persisted here so the Herdr sidebar can render the compact
# AGY summary row. Side-channel only: this NEVER alters statusline output.
# the four structured quota pools (Gemini 5H/W, Claude/GPT 5H/W) are
# persisted alongside, straight from the same upstream quota dict parsed for
# the footer. Side-channel only: this NEVER alters statusline output.
HERDR_AGY_STATE_DIR = os.path.expanduser("~/.config/herdr/cen-harness-hud-quota/agy")
IDENTITY_PATH = os.path.join(HERDR_AGY_STATE_DIR, "agy-identity.json")
QUOTA_PATH = os.path.join(HERDR_AGY_STATE_DIR, "agy-quota.json")

# ── ANSI Color Codes ───────────────────────────────────────────────────────────
RESET        = "\033[0m"
BOLD         = "\033[1m"
DIM          = "\033[2m"
GRAY         = "\033[90m"
WHITE        = "\033[37m"
GREEN        = "\033[32m"
YELLOW       = "\033[33m"
ORANGE       = "\033[38;5;208m"
RED          = "\033[31m"
CYAN         = "\033[36m"
BLUE         = "\033[34m"
PURPLE       = "\033[38;5;141m"

def get_color_for_pct(pct: float) -> str:
    """Percentages strictly represent LEFT / REMAINING."""
    if pct >= 50.0:
        return GREEN
    elif pct >= 20.0:
        return YELLOW
    return RED

def format_countdown_from_seconds(sec: int) -> str:
    """Convert remaining seconds into a compact human countdown string."""
    if sec <= 0:
        return "↻now"
    minutes = (sec + 59) // 60
    if minutes < 60:
        return f"↻{minutes}m"
    hours, mins = divmod(minutes, 60)
    if hours >= 24:
        days, rem_h = divmod(hours, 24)
        return f"↻{days}d{rem_h}h" if rem_h else f"↻{days}d"
    return f"↻{hours}h{mins:02d}" if mins else f"↻{hours}h"

def format_countdown_from_iso(iso_str: str) -> str:
    """Fallback: convert UTC ISO timestamp to countdown string."""
    if not iso_str:
        return ""
    try:
        clean_iso = iso_str.replace("Z", "+00:00")
        target_dt = datetime.fromisoformat(clean_iso)
        delta_sec = int((target_dt - datetime.now(timezone.utc)).total_seconds())
        return format_countdown_from_seconds(delta_sec)
    except Exception:
        return ""

def normalize_quota_key(key: str) -> dict:
    """Map native AGY quota keys to truthful human-readable labels across width modes."""
    k = (key or "").strip().lower()

    if k in ("3p-5h", "thirdparty-5h", "3p_5h", "claude-gpt-5h"):
        return {"wide": "Claude/GPT 5H", "med": "3P5", "narrow": "3P5", "order": 3}
    if k in ("3p-weekly", "thirdparty-weekly", "3p_weekly", "claude-gpt-weekly"):
        return {"wide": "Claude/GPT W", "med": "3PW", "narrow": "3PW", "order": 4}
    if k in ("gemini-5h", "g-5h", "gemini_5h"):
        return {"wide": "Gemini 5H", "med": "GM5", "narrow": "G5", "order": 1}
    if k in ("gemini-weekly", "g-weekly", "gemini_weekly"):
        return {"wide": "Gemini W", "med": "GMW", "narrow": "GW", "order": 2}

 # Dynamic fallback for new or unknown quota keys
    clean = re.sub(r"[^a-zA-Z0-9]+", " ", k).strip().title()
    if "-5h" in k or " 5h" in clean.lower() or "_5h" in k:
        wide = f"{clean.replace('5H', '').replace('5h', '').strip()} 5H"
    elif "-weekly" in k or "weekly" in clean.lower() or "_weekly" in k:
        wide = f"{clean.replace('Weekly', '').replace('weekly', '').strip()} W"
    else:
        wide = clean or "Quota"

    med = wide[:7]
    narrow = "".join(w[0].upper() for w in wide.split() if w)[:3] or wide[:2].upper()
    return {"wide": wide, "med": med, "narrow": narrow, "order": 99}

def normalize_plan_tier(raw_plan: str) -> str:
    """Format truthful plan tier without fabricating defaults."""
    if not raw_plan:
        return ""
    p = raw_plan.strip()
    if "pro" in p.lower():
        return "PRO"
    if "team" in p.lower():
        return "TEAM"
    if "enterprise" in p.lower():
        return "ENT"
    if "free" in p.lower():
        return "FREE"
    return p.upper()

def parse_session_payload(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}

    email = (data.get("email") or "").strip()
    raw_plan = (data.get("plan_tier") or "").strip()
    plan = normalize_plan_tier(raw_plan)

 # Terminal width resolution: prefer official payload width, fallback to terminal size
    term_width = 120
    payload_width = data.get("terminal_width")
    if isinstance(payload_width, int) and payload_width > 20:
        term_width = payload_width
    else:
        try:
            term_width = shutil.get_terminal_size((120, 24)).columns
        except Exception:
            pass

    quota_dict = data.get("quota")
    if not isinstance(quota_dict, dict) or not quota_dict:
        return {
            "email": email,
            "plan": plan,
            "term_width": term_width,
            "pools": [],
        }

    pools = []
    for key, qinfo in quota_dict.items():
        if not isinstance(qinfo, dict):
            continue

        raw_frac = qinfo.get("remaining_fraction")
        if raw_frac is None:
            raw_frac = qinfo.get("remainingFraction")
        if raw_frac is None:
            continue

        try:
            frac = float(raw_frac)
            if not (0.0 <= frac <= 1.0):
                frac = max(0.0, min(1.0, frac))
        except (ValueError, TypeError):
            continue

        pct = frac * 100.0

 # Countdown resolution: prefer reset_in_seconds, fallback to reset_time ISO
        countdown = ""
        sec = qinfo.get("reset_in_seconds")
        if isinstance(sec, (int, float)):
            countdown = format_countdown_from_seconds(int(sec))
        else:
            reset_time = qinfo.get("reset_time") or qinfo.get("resetTime")
            if isinstance(reset_time, str):
                countdown = format_countdown_from_iso(reset_time)

        meta = normalize_quota_key(key)
        pools.append({
            "key": key,
            "label_wide": meta["wide"],
            "label_med": meta["med"],
            "label_narrow": meta["narrow"],
            "order": meta["order"],
            "pct": pct,
            "countdown": countdown,
        })

 # Sort pools logically: Gemini 5H, Gemini W, Claude/GPT 5H, Claude/GPT W, others
    pools.sort(key=lambda p: (p["order"], p["label_wide"]))

    return {
        "email": email,
        "plan": plan,
        "term_width": term_width,
        "pools": pools,
    }

def format_identity_header(email: str, plan: str, mode: str) -> str:
    """Format truthful account and plan identity across width modes."""
    local_user = email.split("@")[0] if email else ""

    if mode == "wide":
        user_str = email or local_user
    elif mode == "narrow":
        if len(local_user) > 12:
            user_str = local_user[:10] + "…"
        else:
            user_str = local_user
    else: # medium
        user_str = local_user

    parts = []
    if user_str:
        parts.append(user_str)
    if plan:
        parts.append(plan)

    if not parts:
        return ""
    return " · ".join(parts)

def render_statusline(parsed: dict) -> str:
    email = parsed.get("email", "") if parsed else ""
    plan = parsed.get("plan", "") if parsed else ""
    pools = parsed.get("pools", []) if parsed else []
    term_width = parsed.get("term_width", 120) if parsed else 120

    if not parsed or not pools:
        ident = format_identity_header(email, plan, "medium")
        tag = f"{WHITE}{ident}{RESET} {GRAY}│{RESET} " if ident else ""
        return f"{BOLD}{CYAN}AGY{RESET} {GRAY}│{RESET} {tag}{YELLOW}quota —{RESET}"

 # 1. WIDE MODE (≥ 115 columns)
    if term_width >= 115:
        ident = format_identity_header(email, plan, "wide")
        ident_str = f" {WHITE}{ident}{RESET} {GRAY}│{RESET}" if ident else ""
        pool_segments = []
        for p in pools:
            pct_val = p["pct"]
            col = get_color_for_pct(pct_val)
            cd = f" {GRAY}{p['countdown']}{RESET}" if p["countdown"] else ""
            pool_segments.append(f"{p['label_wide']} {col}{pct_val:.0f}%{RESET}{cd}")

        pools_str = f" {GRAY}│{RESET} ".join(pool_segments)
        return f"{BOLD}{CYAN}AGY{RESET} {GRAY}│{RESET}{ident_str} {pools_str}"

 # 2. MEDIUM MODE (80 - 114 columns)
    if term_width >= 80:
        ident = format_identity_header(email, plan, "medium")
        ident_str = f" {WHITE}{ident}{RESET} {GRAY}│{RESET}" if ident else ""
        pool_segments = []
        for p in pools:
            pct_val = p["pct"]
            col = get_color_for_pct(pct_val)
            cd = f"/{p['countdown'].lstrip('↻')}" if p["countdown"] else ""
            pool_segments.append(f"{p['label_med']} {col}{pct_val:.0f}%{RESET}{GRAY}{cd}{RESET}")

        pools_str = f" {GRAY}│{RESET} ".join(pool_segments)
        return f"{BOLD}{CYAN}AGY{RESET} {GRAY}│{RESET}{ident_str} {pools_str}"

 # 3. NARROW MODE (< 80 columns)
    ident = format_identity_header(email, plan, "narrow")
    ident_str = f" {WHITE}{ident}{RESET} {GRAY}│{RESET}" if ident else ""
    pool_segments = []
    for p in pools:
        pct_val = p["pct"]
        col = get_color_for_pct(pct_val)
        pool_segments.append(f"{p['label_narrow']}:{col}{pct_val:.0f}%{RESET}")

    pools_str = " ".join(pool_segments)
    return f"{BOLD}{CYAN}AGY{RESET} {GRAY}│{RESET}{ident_str} {pools_str}"

def _ensure_private_state_dir(path: str) -> bool:
    """Ensure the CEN-owned state dir exists with mode 0700.

    os.makedirs(mode=0o700) does NOT tighten an already-existing
    directory with broader permissions — chmod deliberately corrects it.
    Only this CEN-owned dir is ever touched; never any parent.
    """
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        os.chmod(path, 0o700)
        return True
    except Exception:
        return False

def _persist_json(path: str, payload) -> None:
    """Shared atomic JSON persistence helper.

    - write-only-when-changed retained
    - atomic tmp + os.replace retained
    - tmp file and final file forced to mode 0600 (also corrects a
      pre-existing CEN-owned sidecar left with broader permissions)
    - fail-safe: never raises, never blocks AGY
    """
    try:
        if not _ensure_private_state_dir(os.path.dirname(path)):
            return
        try:
            same = False
            with open(path, "r") as f:
                same = json.load(f) == payload
 # enforce 0600 even when unchanged, so a pre-existing
 # CEN-owned sidecar left with broader permissions is corrected
 # on every persistence pass.
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
            if same:
                return
        except Exception:
            pass
        tmp_path = path + ".tmp"
        fd = None
        try:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                fd = None
                json.dump(payload, f)
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, path)
            os.chmod(path, 0o600)
        finally:
            if fd is not None:
                os.close(fd)
    except Exception:
        pass

def persist_identity(email: str, plan: str) -> None:
    """Persist compact account identity for the CEN Herdr AGY summary token.

    the CURRENT sanitized truth is always persisted — including empty
    fields — so a valid→empty identity transition cannot leave a previous
    account attributed in the sidebar. Partial identity (email only / plan
    only) remains truthful; the publisher omits missing parts.
    Fail-safe, write-only-when-changed. Never raises, never blocks AGY.
    """
    local = email.split("@")[0].strip() if email else ""
    _persist_json(IDENTITY_PATH, {"email_local": local[:32], "plan": plan[:10]})

QUOTA_POOL_MAP = {
    "gemini-5h": ("gemini", "five_hour"),
    "g-5h": ("gemini", "five_hour"),
    "gemini_5h": ("gemini", "five_hour"),
    "gemini-weekly": ("gemini", "weekly"),
    "g-weekly": ("gemini", "weekly"),
    "gemini_weekly": ("gemini", "weekly"),
    "3p-5h": ("thirdparty", "five_hour"),
    "thirdparty-5h": ("thirdparty", "five_hour"),
    "3p_5h": ("thirdparty", "five_hour"),
    "claude-gpt-5h": ("thirdparty", "five_hour"),
    "3p-weekly": ("thirdparty", "weekly"),
    "thirdparty-weekly": ("thirdparty", "weekly"),
    "3p_weekly": ("thirdparty", "weekly"),
    "claude-gpt-weekly": ("thirdparty", "weekly"),
}

def persist_quota(pools) -> None:
    """Persist the four structured quota pools for the CEN Herdr AGY summary.

    writes the SAME structured upstream quota data already parsed for the
    footer — no re-parsing of rendered text, no new API, no watcher.
    zero usable windows is persisted as {"windows": []} so a
    valid→empty runtime transition cannot leave stale quota on disk (the
    publisher then clears cen_agy_summary — fail-closed end-to-end).
    Fail-safe, write-only-when-changed. Never raises, never blocks AGY.
    """
    entries = []
    seen = set()
    for p in pools or []:
        key = str((p or {}).get("key") or "").strip().lower()
        mapped = QUOTA_POOL_MAP.get(key)
        if not mapped or mapped in seen:
            continue
        try:
            pct = float(p["pct"])
        except (KeyError, ValueError, TypeError):
            continue
        if not (0.0 <= pct <= 100.0):
            continue
        seen.add(mapped)
        entries.append({
            "family": mapped[0],
            "horizon": mapped[1],
            "remaining_percent": round(pct, 4),
        })
    _persist_json(QUOTA_PATH, {"windows": sorted(entries, key=lambda e: (e["family"], e["horizon"]))})

def main():
    session_data = {}
    try:
        rlist, _, _ = select.select([sys.stdin], [], [], 0.05)
        if rlist:
            raw_in = sys.stdin.read()
            if raw_in.strip():
                session_data = json.loads(raw_in)
    except Exception:
        pass

    try:
        if isinstance(session_data, dict):
            persist_identity(
                (session_data.get("email") or "").strip(),
                normalize_plan_tier((session_data.get("plan_tier") or "").strip()),
            )
    except Exception:
        pass

    try:
        parsed = parse_session_payload(session_data)
        if parsed:
            persist_quota(parsed.get("pools"))
        output = render_statusline(parsed)
        print(output)
    except Exception:
 # Ultimate fault tolerance: never throw traceback into AGY
        print(f"{BOLD}{CYAN}AGY{RESET} {GRAY}│{RESET} {YELLOW}quota —{RESET}")

if __name__ == "__main__":
    main()
