#!/usr/bin/env python3
"""Native Codex SessionStart bridge for session/profile telemetry."""

from __future__ import annotations

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import telemetry  # noqa: E402


def _read_payload() -> dict:
    try:
        raw = sys.stdin.read(262144)
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def main() -> int:
    data = _read_payload()
    if str(data.get("hook_event_name") or "") != "SessionStart":
        return 0
    session_id = data.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        return 0

    home = os.environ.get("HOME")
    if not home:
        return 0
    codex_home = os.environ.get("CODEX_HOME") or os.path.join(home, ".codex")
    telemetry.write_session_mapping(home, session_id, codex_home)

    if not os.environ.get("HERDR_PANE_ID"):
        return 0
    publisher = os.path.join(HERE, "publisher.py")
    if not os.path.isfile(publisher):
        return 0
    source = str(data.get("source") or "startup").strip().lower()
    reason = "clear" if source == "clear" else "session_start"
    env = dict(os.environ)
    env["CEN_CODEX_REFRESH_REASON"] = reason
    env["CEN_CODEX_SESSION_ID"] = session_id
    try:
        subprocess.Popen(
            [sys.executable, publisher],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
