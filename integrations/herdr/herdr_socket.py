#!/usr/bin/env python3
"""cen-harness-hud — Bounded Herdr Unix Socket Client

Canonical Source: cen-harness-hud/integrations/herdr/herdr_socket.py

Tiny stdlib-only JSON-RPC client over the Herdr Unix socket.
Single connection per request, newline-delimited, hard-bounded read loop.

Security:
  - No shell=True
  - No process-env scraping
"""

import json
import os
import socket
from typing import Any, Dict, Optional

DEFAULT_SOCKET_PATH = os.path.expanduser("~/.config/herdr/herdr.sock")
CONNECT_TIMEOUT_SEC = 1.0
READ_TIMEOUT_SEC = 2.0
MAX_RESPONSE_BYTES = 262144


def resolve_socket_path(explicit: Optional[str] = None) -> Optional[str]:
    """Resolve Herdr socket path: explicit arg > HERDR_SOCKET_PATH > default."""
    candidate = (
        explicit
        or os.environ.get("HERDR_SOCKET_PATH")
        or DEFAULT_SOCKET_PATH
    )
    if candidate and os.path.exists(candidate):
        return candidate
    return None


def rpc(sock_path: str, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Send one newline-delimited JSON request, return parsed response or None."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(CONNECT_TIMEOUT_SEC)
            s.connect(sock_path)
            s.settimeout(READ_TIMEOUT_SEC)
            s.sendall((json.dumps(request) + "\n").encode())
            buf = b""
            while len(buf) < MAX_RESPONSE_BYTES:
                data = s.recv(8192)
                if not data:
                    break
                buf += data
                if b"\n" in buf:
                    break
        raw = buf.decode(errors="replace").strip()
        if raw:
            return json.loads(raw.splitlines()[0])
    except Exception:
        pass
    return None


def report_metadata(
    sock_path: str,
    pane_id: str,
    source: str,
    seq: int,
    tokens: Dict[str, Optional[str]],
) -> bool:
    """Report metadata tokens to one pane. Token value None clears the token."""
    resp = rpc(sock_path, {
        "id": f"{source}:{seq}",
        "method": "pane.report_metadata",
        "params": {
            "pane_id": pane_id,
            "source": source,
            "seq": seq,
            "tokens": tokens,
        },
    })
    return bool(resp and resp.get("result", {}).get("type") == "ok")


def pane_get(sock_path: str, pane_id: str) -> Optional[Dict[str, Any]]:
    """Return the pane object for pane_id, or None on failure."""
    resp = rpc(sock_path, {
        "id": f"pane-get:{pane_id}",
        "method": "pane.get",
        "params": {"pane_id": pane_id},
    })
    if resp and isinstance(resp.get("result"), dict):
        pane = resp["result"].get("pane")
        if isinstance(pane, dict):
            return pane
    return None


def pane_get_tokens(sock_path: str, pane_id: str) -> Optional[Dict[str, str]]:
    """Return the token dict for pane_id, or None on failure."""
    pane = pane_get(sock_path, pane_id)
    if pane is None:
        return None
    tokens = pane.get("tokens")
    return tokens if isinstance(tokens, dict) else {}


def reload_config(sock_path: str) -> Optional[Dict[str, Any]]:
    """Ask the running server to reload config. Returns response or None."""
    return rpc(sock_path, {
        "id": "server.reload_config",
        "method": "server.reload_config",
        "params": {},
    })
