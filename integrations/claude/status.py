#!/usr/bin/env python3
"""Claude Code StatusLine adapter.

The adapter consumes one Claude Code StatusLine JSON object from stdin,
renders a compact native status string, and optionally reports only the two
CEN Claude metadata tokens to the matching Herdr pane. It has no provider
client, credential access, persistence, or polling loop.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from typing import Any, Dict, Mapping, Optional, Tuple

HERE = os.path.dirname(os.path.realpath(os.path.abspath(__file__)))
HERDR_DIR = os.path.realpath(os.path.join(HERE, "..", "herdr"))
if HERDR_DIR not in sys.path:
    sys.path.insert(0, HERDR_DIR)

from herdr_socket import (  # noqa: E402
    pane_get,
    report_metadata,
    resolve_socket_path,
)

MAX_STDIN_BYTES = 256 * 1024
MAX_MODEL_LENGTH = 80
MAX_SESSION_ID_LENGTH = 256

CUSTOM_PROVIDER_ROUTE_ENV = "ANTHROPIC_BASE_URL"
REPORT_SOURCE = "cen-claude-status"
TOKEN_MODEL_CONTEXT = "cen_claude_model_context"
TOKEN_QUOTA = "cen_claude_quota"

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WINDOWS = (("five_hour", "5H"), ("seven_day", "7D"))


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _percent(value: Any) -> Optional[int]:
    number = _finite_number(value)
    if number is None:
        return None
    number = max(0.0, min(100.0, number))
    return int(round(number))


def _used_to_left(value: Any) -> Optional[int]:
    used = _finite_number(value)
    if used is None:
        return None
    return _percent(100.0 - used)


def sanitize_text(value: Any, max_length: int = MAX_MODEL_LENGTH) -> Optional[str]:
    """Return bounded display text with control characters removed."""
    if not isinstance(value, str):
        return None
    value = _CONTROL_CHARS.sub(" ", value)
    value = " ".join(value.split()).strip()
    if not value:
        return None
    return value[:max_length]


def normalize_model(payload: Mapping[str, Any]) -> Optional[str]:
    model = payload.get("model")
    if not isinstance(model, dict):
        return None
    display = sanitize_text(model.get("display_name"))
    return display or sanitize_text(model.get("id"))


def normalize_context(payload: Mapping[str, Any]) -> Optional[int]:
    context = payload.get("context_window")
    if not isinstance(context, dict):
        return None

    if "remaining_percentage" in context:
        return _percent(context.get("remaining_percentage"))

    if "used_percentage" in context:
        return _used_to_left(context.get("used_percentage"))
    return None


def format_countdown(resets_at: Any, now_epoch: Optional[float] = None) -> Optional[str]:
    """Format a non-negative snapshot countdown from Unix epoch seconds."""
    target = _finite_number(resets_at)
    if target is None:
        return None
    now = time.time() if now_epoch is None else _finite_number(now_epoch)
    if now is None:
        return None
    remaining = target - now
    if remaining <= 0:
        return None

    seconds = int(math.ceil(remaining))
    minutes = (seconds + 59) // 60
    if minutes < 60:
        return f"↻{minutes}m"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"↻{hours}h{mins:02d}" if mins else f"↻{hours}h"
    days, rem_hours = divmod(hours, 24)
    return f"↻{days}d{rem_hours}h" if rem_hours else f"↻{days}d"


def normalize_rate_limits(
    payload: Mapping[str, Any], now_epoch: Optional[float] = None
) -> Dict[str, Dict[str, Any]]:
    """Normalize only Claude subscription five-hour and seven-day windows."""
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict):
        return {}

    normalized: Dict[str, Dict[str, Any]] = {}
    for key, label in _WINDOWS:
        window = limits.get(key)
        if not isinstance(window, dict):
            continue
        left = _used_to_left(window.get("used_percentage"))
        if left is None:
            continue
        entry: Dict[str, Any] = {"label": label, "left": left}
        countdown = format_countdown(window.get("resets_at"), now_epoch)
        if countdown:
            entry["countdown"] = countdown
        normalized[key] = entry
    return normalized


def format_model_context(model: Optional[str], context_left: Optional[int]) -> Optional[str]:
    if model and context_left is not None:
        return f"{model} · CTX {context_left}%"
    if model:
        return model
    if context_left is not None:
        return f"CTX {context_left}%"
    return None


def format_quota(windows: Mapping[str, Mapping[str, Any]]) -> Optional[str]:
    parts = []
    for key, label in _WINDOWS:
        window = windows.get(key)
        if not isinstance(window, dict):
            continue
        left = window.get("left")
        if not isinstance(left, int) or isinstance(left, bool):
            continue
        part = f"{label} {left}%"
        countdown = window.get("countdown")
        if isinstance(countdown, str) and countdown:
            part += f" {countdown}"
        parts.append(part)
    return " · ".join(parts) or None


def custom_provider_mode(env: Optional[Mapping[str, str]] = None) -> bool:
    values = os.environ if env is None else env
    route = values.get(CUSTOM_PROVIDER_ROUTE_ENV)
    return isinstance(route, str) and bool(route.strip())


def render_native_status(
    model_context: Optional[str], quota: Optional[str]
) -> str:
    parts = ["CLAUDE"]
    if model_context:
        parts.append(model_context)
    if quota:
        parts.append(quota)
    return " │ ".join(parts)


def build_status(
    payload: Mapping[str, Any],
    now_epoch: Optional[float] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[str, Dict[str, Optional[str]]]:
    model_context = format_model_context(
        normalize_model(payload), normalize_context(payload)
    )
    quota = None
    if not custom_provider_mode(env):
        quota = format_quota(normalize_rate_limits(payload, now_epoch))
    tokens = {
        TOKEN_MODEL_CONTEXT: model_context,
        TOKEN_QUOTA: quota,
    }
    return render_native_status(model_context, quota), tokens


def _valid_session_id(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    if not value.strip() or len(value) > MAX_SESSION_ID_LENGTH:
        return None
    return value


def _pane_matches_session(pane: Any, session_id: str) -> bool:
    if not isinstance(pane, dict):
        return False
    if str(pane.get("agent") or "").lower() != "claude":
        return False
    native = pane.get("agent_session")
    if not isinstance(native, dict):
        return False
    if str(native.get("agent") or "").lower() != "claude":
        return False
    if native.get("source") != "herdr:claude":
        return False
    return native.get("value") == session_id


def publish_metadata(
    payload: Mapping[str, Any],
    tokens: Dict[str, Optional[str]],
    env: Optional[Mapping[str, str]] = None,
) -> bool:
    """Publish only after two native pane/session validations."""
    values = os.environ if env is None else env
    pane_id = values.get("HERDR_PANE_ID")
    session_id = _valid_session_id(payload.get("session_id"))
    if not isinstance(pane_id, str) or not pane_id.strip() or not session_id:
        return False

    try:
        socket_path = resolve_socket_path(values.get("HERDR_SOCKET_PATH"))
        if not socket_path:
            return False
        pane_before = pane_get(socket_path, pane_id)
        if not _pane_matches_session(pane_before, session_id):
            return False
        pane_after = pane_get(socket_path, pane_id)
        if not _pane_matches_session(pane_after, session_id):
            return False
        return bool(report_metadata(
            socket_path, pane_id, REPORT_SOURCE, time.time_ns(), tokens
        ))
    except Exception:
        return False


def process_payload(
    payload: Mapping[str, Any],
    now_epoch: Optional[float] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Tuple[str, Dict[str, Optional[str]], bool]:
    native, tokens = build_status(payload, now_epoch, env)
    published = publish_metadata(payload, tokens, env)
    return native, tokens, published


def read_bounded_stdin(stream: Any = None) -> Optional[bytes]:
    stream = sys.stdin if stream is None else stream
    try:
        source = getattr(stream, "buffer", stream)
        raw = source.read(MAX_STDIN_BYTES + 1)
        if isinstance(raw, str):
            raw = raw.encode("utf-8", errors="replace")
        if not isinstance(raw, bytes) or len(raw) > MAX_STDIN_BYTES:
            return None
        return raw
    except Exception:
        return None


def parse_stdin(stream: Any = None) -> Optional[Dict[str, Any]]:
    raw = read_bounded_stdin(stream)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def main() -> int:
    try:
        payload = parse_stdin()
        if payload is None:
            return 0
        native, _, _ = process_payload(payload)
        if native:
            sys.stdout.write(native + "\n")
    except Exception:
        # StatusLine commands must fail open and never leak a traceback.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
