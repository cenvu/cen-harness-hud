#!/usr/bin/env python3
"""Session-native Codex quota telemetry shared by hooks and Herdr rendering.

No credential files are read. Effective Codex profiles are learned only from
the private mapping written by the native CEN SessionStart hook.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from typing import Any, Dict, Optional

try:
    from . import status
except ImportError:
    import status  # type: ignore

REFRESH_INTERVAL_SECONDS = 60.0
SESSION_DEDUP_SECONDS = 5.0
SCHEMA_VERSION = 1


def _root(home: str) -> str:
    return os.path.join(home, ".config", "herdr", "cen-harness-hud-quota")


def _private_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    os.chmod(path, 0o700)


def _atomic_json(path: str, data: Dict[str, Any]) -> None:
    parent = os.path.dirname(path)
    _private_dir(parent)
    fd, tmp = tempfile.mkstemp(prefix=".cen-codex-", dir=parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, separators=(",", ":"))
            f.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def session_key(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8", "surrogatepass")).hexdigest()


def mapping_path(home: str, session_id: str) -> str:
    return os.path.join(_root(home), "codex-sessions", session_key(session_id) + ".json")


def snapshot_path(home: str, session_id: str) -> str:
    return os.path.join(_root(home), "codex-snapshots", session_key(session_id) + ".json")


def lock_path(home: str, session_id: str, kind: str) -> str:
    safe_kind = "".join(ch for ch in kind if ch.isalnum() or ch in "-_")[:24] or "lock"
    return os.path.join(_root(home), "codex-locks", f"{session_key(session_id)}.{safe_kind}.lock")


def write_session_mapping(home: str, session_id: str, codex_home: str,
                          now_epoch: Optional[float] = None) -> bool:
    if not session_id or not codex_home:
        return False
    now_epoch = time.time() if now_epoch is None else float(now_epoch)
    try:
        resolved = os.path.realpath(codex_home)
        _atomic_json(mapping_path(home, session_id), {
            "schema": SCHEMA_VERSION,
            "codex_home": resolved,
            "updated_at": now_epoch,
        })
        return True
    except Exception:
        return False


def read_session_mapping(home: str, session_id: str) -> Optional[str]:
    data = _read_json(mapping_path(home, session_id))
    if not data or data.get("schema") != SCHEMA_VERSION:
        return None
    value = data.get("codex_home")
    if not isinstance(value, str) or not value.strip():
        return None
    return os.path.realpath(value)


def resolve_codex_home(home: str, session_id: Optional[str]) -> Optional[str]:
    """Resolve profile from the native SessionStart mapping only.

    A missing mapping is intentionally fail-closed. CEN never guesses from
    the current directory, launcher tokens, or a scan of possible profiles.
    """
    if not session_id:
        return None
    return read_session_mapping(home, session_id)


def _finite(value: Any) -> Optional[float]:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _normalize_window(window: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(window, dict):
        return None
    used = _finite(window.get("usedPercent"))
    duration = _finite(window.get("windowDurationMins"))
    if used is None or duration is None or duration <= 0:
        return None
    reset = _finite(window.get("resetsAt"))
    return {
        "remaining_percent": max(0.0, min(100.0, 100.0 - used)),
        "duration_minutes": int(duration),
        "reset_at": reset,
    }


def _select_limit_snapshot(rl_res: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(rl_res, dict):
        return None
    by_id = rl_res.get("rateLimitsByLimitId")
    if isinstance(by_id, dict) and by_id:
        for key, value in by_id.items():
            if not isinstance(value, dict):
                continue
            limit_id = str(value.get("limitId") or key).lower()
            if limit_id == "codex":
                return value
        values = [v for _, v in sorted(by_id.items()) if isinstance(v, dict)]
        if len(values) == 1:
            return values[0]
        for value in values:
            if value.get("primary") or value.get("secondary"):
                return value
    fallback = rl_res.get("rateLimits")
    return fallback if isinstance(fallback, dict) else None


def _account_identity(acc_res: Any) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(acc_res, dict) or not isinstance(acc_res.get("account"), dict):
        return None, None
    acc = acc_res["account"]
    acc_type = str(acc.get("type") or "").strip().lower()
    if acc_type == "apikey":
        alias = "API KEY"
    else:
        email = status.sanitize_label(acc.get("email"), 96)
        alias = status.sanitize_label(email.split("@", 1)[0], 40) if email else ""
    plan = status.sanitize_label(str(acc.get("planType") or "").upper(), 24)
    return (alias or None), (plan or None)


def build_snapshot(acc_res: Any, rl_res: Any, now_epoch: Optional[float] = None) -> Optional[Dict[str, Any]]:
    now_epoch = time.time() if now_epoch is None else float(now_epoch)
    alias, plan = _account_identity(acc_res)
    limits = _select_limit_snapshot(rl_res)
    primary = _normalize_window(limits.get("primary")) if limits else None
    secondary = _normalize_window(limits.get("secondary")) if limits else None

    weekly = None
    for candidate in (primary, secondary):
        if candidate and candidate.get("duration_minutes") == 10080:
            weekly = candidate
            break

    if alias is None and plan is None and primary is None and weekly is None:
        return None
    return {
        "schema": SCHEMA_VERSION,
        "account_alias": alias,
        "plan": plan,
        "primary": primary,
        "weekly": weekly,
        "fetched_at": now_epoch,
        "last_attempt_at": now_epoch,
        "stale": False,
    }


def load_snapshot(home: str, session_id: str) -> Optional[Dict[str, Any]]:
    data = _read_json(snapshot_path(home, session_id))
    if not data or data.get("schema") != SCHEMA_VERSION:
        return None
    return data


def refresh_due(snapshot: Optional[Dict[str, Any]], now_epoch: Optional[float] = None,
                interval: float = REFRESH_INTERVAL_SECONDS) -> bool:
    now_epoch = time.time() if now_epoch is None else float(now_epoch)
    if not snapshot:
        return True
    last = _finite(snapshot.get("last_attempt_at"))
    return last is None or (now_epoch - last) >= interval


def refresh_snapshot(home: str, session_id: str, codex_home: str,
                     now_epoch: Optional[float] = None) -> Optional[Dict[str, Any]]:
    now_epoch = time.time() if now_epoch is None else float(now_epoch)
    previous = load_snapshot(home, session_id)
    try:
        acc_res, rl_res = status.query_app_server(codex_home=codex_home)
        current = build_snapshot(acc_res, rl_res, now_epoch)
    except Exception:
        current = None
    if current is not None:
        try:
            _atomic_json(snapshot_path(home, session_id), current)
        except Exception:
            pass
        return current

    if previous:
        previous = dict(previous)
        previous["last_attempt_at"] = now_epoch
        previous["stale"] = True
        try:
            _atomic_json(snapshot_path(home, session_id), previous)
        except Exception:
            pass
        return previous

    marker = {
        "schema": SCHEMA_VERSION,
        "fetched_at": 0,
        "last_attempt_at": now_epoch,
        "stale": True,
    }
    try:
        _atomic_json(snapshot_path(home, session_id), marker)
    except Exception:
        pass
    return marker


def format_identity(snapshot: Optional[Dict[str, Any]]) -> str:
    if not snapshot:
        return "—"
    alias = status.sanitize_label(snapshot.get("account_alias"), 40)
    plan = status.sanitize_label(snapshot.get("plan"), 24)
    if alias and plan:
        return f"{alias} · {plan}"
    if alias:
        return alias
    if plan:
        return f"account · {plan}"
    return "—"


def format_weekly(snapshot: Optional[Dict[str, Any]], now_epoch: Optional[float] = None) -> str:
    if not snapshot or not isinstance(snapshot.get("weekly"), dict):
        return "—"
    win = snapshot["weekly"]
    rem = _finite(win.get("remaining_percent"))
    dur = win.get("duration_minutes")
    if rem is None:
        return "—"
    label = status.format_duration(dur) or "W"
    text = f"{label} {int(round(rem))}%"
    countdown = status.format_countdown(win.get("reset_at"), now_epoch)
    return f"{text} · {countdown}" if countdown else text
