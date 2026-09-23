#!/usr/bin/env python3
"""Pane-scoped Codex telemetry publisher for Herdr.

Identity/quota resolution is session-native: the active Codex session id and
the private mapping written by CEN's SessionStart hook determine the account.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any, Dict, Optional

HERE = os.path.dirname(os.path.realpath(os.path.abspath(__file__)))
HERDR_DIR = os.path.realpath(os.path.join(HERE, "..", "herdr"))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if HERDR_DIR not in sys.path:
    sys.path.insert(0, HERDR_DIR)

import telemetry  # noqa: E402
from herdr_socket import pane_get, report_metadata  # noqa: E402

BRIDGE_SOURCE = "cen-codex-bridge"
TOKEN_IDENTITY = "cen_codex_identity"
TOKEN_WEEKLY = "cen_codex_weekly"
RETIRED_TOKENS = (
    "cen_codex_window_1",
    "cen_codex_window_2",
    "cen_codex_summary",
)
WORKER_ARG = "--active-worker"
WORKER_STALE_SECONDS = 180.0


def _socket_path() -> Optional[str]:
    p = os.environ.get("HERDR_SOCKET_PATH")
    if p:
        return p
    home = os.environ.get("HOME")
    return os.path.join(home, ".config", "herdr", "herdr.sock") if home else None


def _native_pane_session_id(pane: Any) -> Optional[str]:
    """Native-only Codex session truth for a pane: ignores hook env pins."""
    if not isinstance(pane, dict):
        return None
    s = pane.get("agent_session")
    if not isinstance(s, dict):
        return None
    if str(s.get("agent") or "").lower() != "codex":
        return None
    if str(s.get("source") or "") != "herdr:codex":
        return None
    value = s.get("value")
    return value if isinstance(value, str) and value.strip() else None


def _session_id(pane: Dict[str, Any]) -> Optional[str]:
    env_session = os.environ.get("CEN_CODEX_SESSION_ID")
    if env_session:
        return env_session
    s = pane.get("agent_session")
    if not isinstance(s, dict):
        return None
    if str(s.get("agent") or "").lower() != "codex":
        return None
    if str(s.get("source") or "") != "herdr:codex":
        return None
    value = s.get("value")
    return value if isinstance(value, str) and value.strip() else None


def fail_closed_tokens() -> Dict[str, Optional[str]]:
    return {
        TOKEN_IDENTITY: "—",
        TOKEN_WEEKLY: "—",
        "cen_codex_window_1": None,
        "cen_codex_window_2": None,
        "cen_codex_summary": None,
    }


def display_tokens(snapshot: Optional[Dict[str, Any]], now_epoch: Optional[float] = None) -> Dict[str, Optional[str]]:
    return {
        TOKEN_IDENTITY: telemetry.format_identity(snapshot),
        TOKEN_WEEKLY: telemetry.format_weekly(snapshot, now_epoch),
        "cen_codex_window_1": None,
        "cen_codex_window_2": None,
        "cen_codex_summary": None,
    }


def _report(sock: str, pane_id: str, tokens: Dict[str, Optional[str]]) -> None:
    # Monotonic ns sequence: prior bridge/launcher reports used time_ns().
    # A millisecond seq would compare smaller and lose ordering — never use it.
    report_metadata(sock, pane_id, BRIDGE_SOURCE, time.time_ns(), tokens)


def _try_lock(path: str, stale_after: float = 20.0) -> bool:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.chmod(os.path.dirname(path), 0o700)
    for attempt in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(str(os.getpid()))
            os.chmod(path, 0o600)
            return True
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(path)
                if attempt == 0 and age > stale_after:
                    os.unlink(path)
                    continue
            except OSError:
                pass
            return False
        except OSError:
            return False
    return False


def _unlock(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _refresh_if_due(home: str, session_id: str, codex_home: str,
                    reason: str, now_epoch: Optional[float] = None) -> Optional[Dict[str, Any]]:
    now_epoch = time.time() if now_epoch is None else float(now_epoch)
    snapshot = telemetry.load_snapshot(home, session_id)
    due = telemetry.refresh_due(snapshot, now_epoch)
    if reason in ("session_start", "clear"):
        last = snapshot.get("last_attempt_at") if snapshot else None
        try:
            recently_attempted = (
                last is not None
                and now_epoch - float(last) < telemetry.SESSION_DEDUP_SECONDS
            )
        except (TypeError, ValueError):
            recently_attempted = False
        due = not recently_attempted
    elif reason not in ("working", "agent_detected"):
        due = False

    if not due:
        return snapshot
    lock = telemetry.lock_path(home, session_id, "refresh")
    if not _try_lock(lock, stale_after=20.0):
        return telemetry.load_snapshot(home, session_id) or snapshot
    try:
        return telemetry.refresh_snapshot(home, session_id, codex_home, now_epoch)
    finally:
        _unlock(lock)


def publish_once(reason: str = "event", start_worker: bool = True,
                 now_epoch: Optional[float] = None) -> bool:
    sock = _socket_path()
    pane_id = os.environ.get("HERDR_PANE_ID")
    home = os.environ.get("HOME")
    if not sock or not pane_id or not home:
        return False
    pane = pane_get(sock, pane_id)
    if not isinstance(pane, dict):
        return False
    if str(pane.get("agent") or "").lower() != "codex":
        return False

    session_id = _session_id(pane)
    codex_home = telemetry.resolve_codex_home(home, session_id)
    if not session_id or not codex_home:
        _report(sock, pane_id, fail_closed_tokens())
        return False

    state = str(pane.get("agent_status") or "").lower()
    effective_reason = reason
    if reason in ("event", "state_changed") and state == "working":
        effective_reason = "working"
    snapshot = _refresh_if_due(home, session_id, codex_home, effective_reason, now_epoch)

    # Post-fetch revalidation: the quota fetch above may take seconds, and
    # the pane may have switched sessions (e.g. /clear) mid-fetch. Never
    # publish an old session's snapshot into a different/new session.
    pane_after = pane_get(sock, pane_id)
    if (
        not isinstance(pane_after, dict)
        or str(pane_after.get("agent") or "").lower() != "codex"
        or _native_pane_session_id(pane_after) != session_id
    ):
        return False

    _report(sock, pane_id, display_tokens(snapshot, now_epoch))

    if start_worker and state == "working":
        _spawn_worker()
    return True


def _worker_lock(home: str, pane_id: str) -> str:
    key = telemetry.session_key(pane_id)
    return os.path.join(
        home, ".config", "herdr", "cen-harness-hud-quota",
        "codex-workers", key + ".lock",
    )


def _spawn_worker() -> None:
    try:
        # A long-lived worker must follow the CURRENT native pane session
        # each cycle — never stay pinned to a hook-inherited session id.
        child_env = dict(os.environ)
        child_env.pop("CEN_CODEX_SESSION_ID", None)
        child_env.pop("CEN_CODEX_REFRESH_REASON", None)
        subprocess.Popen(
            [sys.executable, os.path.realpath(__file__), WORKER_ARG],
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception:
        pass


def active_worker() -> int:
    home = os.environ.get("HOME")
    pane_id = os.environ.get("HERDR_PANE_ID")
    sock = _socket_path()
    if not home or not pane_id or not sock:
        return 0
    lock = _worker_lock(home, pane_id)
    if not _try_lock(lock, stale_after=WORKER_STALE_SECONDS):
        return 0
    try:
        while True:
            pane = pane_get(sock, pane_id)
            if not isinstance(pane, dict):
                break
            if str(pane.get("agent") or "").lower() != "codex":
                break
            if str(pane.get("agent_status") or "").lower() != "working":
                break
            session_id = _session_id(pane)
            if not session_id:
                break
            snap = telemetry.load_snapshot(home, session_id)
            last = snap.get("last_attempt_at") if snap else None
            try:
                age = (
                    time.time() - float(last)
                    if last is not None
                    else telemetry.REFRESH_INTERVAL_SECONDS
                )
            except (TypeError, ValueError):
                age = telemetry.REFRESH_INTERVAL_SECONDS
            wait = max(1.0, telemetry.REFRESH_INTERVAL_SECONDS - age)
            time.sleep(wait)
            pane = pane_get(sock, pane_id)
            if (
                not isinstance(pane, dict)
                or str(pane.get("agent_status") or "").lower() != "working"
            ):
                break
            publish_once(reason="working", start_worker=False)
        return 0
    finally:
        _unlock(lock)


def main() -> int:
    try:
        if len(sys.argv) > 1 and sys.argv[1] == WORKER_ARG:
            return active_worker()
        reason = (
            os.environ.get("CEN_CODEX_REFRESH_REASON", "event").strip().lower()
            or "event"
        )
        publish_once(reason=reason)
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
