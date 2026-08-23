#!/usr/bin/env python3
"""
cen-harness-hud — Codex One-Shot Identity & Dynamic Rate Limits Helper

Canonical Source: cen-harness-hud/integrations/codex/status.py
Symlink: ~/.local/bin/cen-codex-status

Provides an on-demand, zero-daemon CLI status snapshot for Codex sessions:
1. Resolves `codex` executable dynamically via shutil.which.
2. Interacts with `codex app-server --stdio` via structured JSON-RPC.
3. Retrieves account identity (`account/read`) and dynamic limits (`account/rateLimits/read`).
4. Zero reading of raw `~/.codex/auth.json` or credential tokens.
5. Normalizes quota windows to LEFT/REMAINING % and formats relative countdowns.
6. Enforces finite numeric validation, true bounded I/O reading, and label sanitization.
7. Displays available rate-limit reset credits separately from quota windows.
"""

import json
import math
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

APP_SERVER_TOTAL_TIMEOUT = 5.0


def sanitize_label(label: Any, max_len: int = 32) -> str:
    """Sanitize display label to prevent terminal control injection."""
    if label is None:
        return ""
    text = str(label)
    text = re.sub(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", text)
    text = "".join(ch for ch in text if ch >= " " and ch != "\x7f")
    text = text.strip()
    return text[:max_len]


def format_duration(mins: Any) -> str:
    """Derive compact duration label from windowDurationMins."""
    if mins is None:
        return ""
    try:
        mins_f = float(mins)
        if not math.isfinite(mins_f) or mins_f <= 0:
            return ""
        mins_i = int(mins_f)
    except (ValueError, TypeError):
        return ""
    if mins_i == 300:
        return "5H"
    if mins_i == 1440:
        return "1D"
    if mins_i == 10080:
        return "7D"
    if mins_i % 1440 == 0:
        return f"{mins_i // 1440}D"
    if mins_i % 60 == 0:
        return f"{mins_i // 60}H"
    return f"{mins_i}M"


def format_countdown(resets_at: Any, now_epoch: Optional[float] = None) -> str:
    """Format Unix timestamp resetsAt into relative countdown."""
    if resets_at is None:
        return ""
    try:
        resets_at_f = float(resets_at)
        if not math.isfinite(resets_at_f):
            return ""
    except (ValueError, TypeError):
        return ""
    if now_epoch is None:
        now_epoch = time.time()
    diff = resets_at_f - now_epoch
    if diff <= 0:
        return "↻now"
    days = int(diff // 86400)
    hours = int((diff % 86400) // 3600)
    mins = int((diff % 3600) // 60)
    if days > 0:
        return f"↻{days}d{hours}h" if hours > 0 else f"↻{days}d"
    if hours > 0:
        return f"↻{hours}h{mins:02d}m" if mins > 0 else f"↻{hours}h"
    return f"↻{max(1, mins)}m"


def normalize_quota_window(window: Any, now_epoch: Optional[float] = None) -> Optional[str]:
    """Normalize a single primary or secondary quota window dict with finite checks."""
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
    dur_str = format_duration(window.get("windowDurationMins"))
    cd_str = format_countdown(window.get("resetsAt"), now_epoch)
    dur_label = dur_str if dur_str else "QUOTA"
    rem_label = f"{int(round(rem))}%"
    parts = [f"{dur_label} LEFT {rem_label}"]
    if cd_str:
        parts.append(cd_str)
    return " ".join(parts)


def format_identity(account_data: Any, term_width: int = 120) -> str:
    """Derive identity string from account/read result."""
    if not account_data or not isinstance(account_data, dict):
        return "account —"
    acc = account_data.get("account")
    if not acc or not isinstance(acc, dict):
        return "account —"
    acc_type = str(acc.get("type") or "").strip().lower()
    raw_email = acc.get("email")
    email = sanitize_label(str(raw_email).strip()) if raw_email else ""
    raw_plan = acc.get("planType")
    plan = sanitize_label(str(raw_plan).strip().upper()) if raw_plan else ""
    if acc_type == "apikey":
        id_part = "API KEY"
    elif email:
        id_part = email if term_width >= 100 else email.split("@")[0]
    else:
        id_part = "account —"
    if plan and id_part != "account —":
        return f"{id_part} · {plan}"
    elif plan:
        return f"account · {plan}"
    return id_part


def format_status(
    acc_res: Any,
    rl_res: Any,
    term_width: Optional[int] = None,
    now_epoch: Optional[float] = None,
) -> str:
    """Format full status line from account and rate limit results."""
    if acc_res is None and rl_res is None:
        return "CDX │ status —"

    if term_width is None:
        term_width = shutil.get_terminal_size((120, 24)).columns
    segments = ["CDX"]

 # 1. Identity segment
    id_str = format_identity(acc_res, term_width)
    segments.append(id_str)

 # 2. Rate limits / Quota segments
    if rl_res is None:
        segments.append("quota —")
    elif isinstance(rl_res, dict):
        limits_dict = rl_res.get("rateLimitsByLimitId")
        limits_list: List[Dict[str, Any]] = []
        if limits_dict and isinstance(limits_dict, dict) and len(limits_dict) > 0:
            for dict_key, snapshot in limits_dict.items():
                if isinstance(snapshot, dict):
                    eff_id = snapshot.get("limitId") or str(dict_key)
                    snap_copy = dict(snapshot)
                    snap_copy["limitId"] = sanitize_label(eff_id)
                    limits_list.append(snap_copy)
        else:
            fallback = rl_res.get("rateLimits")
            if fallback and isinstance(fallback, dict):
                limits_list = [fallback]

        has_multiple_limits = len(limits_list) > 1
        quota_segments_added = 0

        for entry in limits_list:
            raw_limit_id = entry.get("limitId", "")
            limit_id = sanitize_label(raw_limit_id)
            p_win = entry.get("primary")
            s_win = entry.get("secondary")

            p_norm = normalize_quota_window(p_win, now_epoch) if (p_win and isinstance(p_win, dict)) else None
            s_norm = normalize_quota_window(s_win, now_epoch) if (s_win and isinstance(s_win, dict)) else None

            p_dur = p_win.get("windowDurationMins") if (p_win and isinstance(p_win, dict)) else None
            s_dur = s_win.get("windowDurationMins") if (s_win and isinstance(s_win, dict)) else None

            same_dur = (p_norm is not None and s_norm is not None and p_dur is not None and p_dur == s_dur)

            if p_norm:
                p_text = f"P {p_norm}" if same_dur else p_norm
                if has_multiple_limits and limit_id and limit_id != "codex":
                    segments.append(f"[{limit_id}] {p_text}")
                else:
                    segments.append(p_text)
                quota_segments_added += 1

            if s_norm:
                s_text = f"S {s_norm}" if same_dur else s_norm
                if has_multiple_limits and limit_id and limit_id != "codex":
                    segments.append(f"[{limit_id}] {s_text}")
                else:
                    segments.append(s_text)
                quota_segments_added += 1

 # Snapshot-level credits
            credits = entry.get("credits")
            if credits and isinstance(credits, dict):
                has_cr = credits.get("hasCredits", False)
                bal = str(credits.get("balance", "0")).strip()
                if has_cr and bal not in ("0", "0.00", "0.0", ""):
                    c_text = f"CREDITS {sanitize_label(bal)}"
                    if has_multiple_limits and limit_id and limit_id != "codex":
                        segments.append(f"[{limit_id}] {c_text}")
                    else:
                        segments.append(c_text)

 # Reset credits (separated from quota windows)
        rc = rl_res.get("rateLimitResetCredits")
        if rc and isinstance(rc, dict):
            try:
                avail = rc.get("availableCount", 0)
                if avail is not None:
                    avail_f = float(avail)
                    if math.isfinite(avail_f) and int(avail_f) > 0:
                        segments.append(f"RESET ×{int(avail_f)}")
            except (ValueError, TypeError):
                pass

        if quota_segments_added == 0 and len(segments) == 2:
            segments.append("quota —")

    return " │ ".join(segments)


def _stdout_reader_thread(stream: Any, out_queue: queue.Queue) -> None:
    """Background worker to drain stdout lines without blocking the main thread."""
    try:
        for line in iter(stream.readline, ""):
            out_queue.put(line)
    except Exception:
        pass
    finally:
        out_queue.put(None) # Sentinel for EOF


def query_app_server(
    total_timeout_sec: float = APP_SERVER_TOTAL_TIMEOUT,
    codex_home: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """Execute bounded one-shot JSON-RPC query against codex app-server --stdio.

    When codex_home is supplied, the child process inherits a copy of the current
    environment with CODEX_HOME explicitly set.  The global os.environ is NEVER
    mutated.  When omitted, the child inherits the caller's environment unchanged
    (backward-compatible default).
    """
    codex_bin = shutil.which("codex")
    if not codex_bin:
        return None, None

    child_env = None
    if codex_home is not None:
        child_env = os.environ.copy()
        child_env["CODEX_HOME"] = codex_home

    proc = None
    overall_deadline = time.time() + total_timeout_sec
    line_queue: queue.Queue = queue.Queue()
    reader_thread = None

    try:
        proc = subprocess.Popen(
            [codex_bin, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=child_env,
        )

        reader_thread = threading.Thread(
            target=_stdout_reader_thread,
            args=(proc.stdout, line_queue),
            daemon=True,
        )
        reader_thread.start()

        def send_rpc(req: Dict[str, Any]) -> None:
            if proc and proc.stdin:
                proc.stdin.write(json.dumps(req) + "\n")
                proc.stdin.flush()

        def read_response(target_id: int, max_wait_sec: float = 2.0) -> Optional[Dict[str, Any]]:
            now = time.time()
            step_deadline = min(now + max_wait_sec, overall_deadline)
            while True:
                remaining = step_deadline - time.time()
                if remaining <= 0:
                    return None
                try:
                    line = line_queue.get(timeout=remaining)
                except queue.Empty:
                    return None
                if line is None: # EOF
                    return None
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    if msg.get("id") == target_id:
                        return msg.get("result")
                except Exception:
                    pass

 # 1. Handshake
        send_rpc({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "cen-codex-status", "version": "1.0.0"}},
        })
        init_res = read_response(1, max_wait_sec=2.0)
        if init_res is None:
            return None, None

 # 2. account/read
        send_rpc({"jsonrpc": "2.0", "id": 2, "method": "account/read", "params": {}})
        acc_res = read_response(2, max_wait_sec=2.0)

 # 3. account/rateLimits/read
        send_rpc({"jsonrpc": "2.0", "id": 3, "method": "account/rateLimits/read", "params": {}})
        rl_res = read_response(3, max_wait_sec=2.5)

        return acc_res, rl_res

    except Exception:
        return None, None
    finally:
        if proc:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=0.4)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=0.4)
                except Exception:
                    pass
        if reader_thread and reader_thread.is_alive():
            reader_thread.join(timeout=0.2)


def main() -> None:
    try:
        codex_bin = shutil.which("codex")
        if not codex_bin:
            print("CDX │ unavailable")
            return

        acc_res, rl_res = query_app_server()
        if acc_res is None and rl_res is None:
            print("CDX │ status —")
            return

        line = format_status(acc_res, rl_res)
        print(line)
    except Exception:
        print("CDX │ status —")


if __name__ == "__main__":
    main()
