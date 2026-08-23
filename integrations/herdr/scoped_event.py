#!/usr/bin/env python3
"""cen-harness-hud — Scoped Herdr Event Gateway

Canonical Source: cen-harness-hud/integrations/herdr/scoped_event.py

Hard provider firewall & concurrency isolation for Herdr events:
- Receives Herdr event JSON via HERDR_PLUGIN_EVENT_JSON
- Validates agent provider: permits ONLY 'agy' / 'antigravity' or 'codex'
- Silently exits 0 on all deferred providers (claude, grok, pi, unknown)
- AGY / antigravity (, native-first):
    The pinned upstream herdr-agent-quota collector is NO LONGER part of the
    active runtime path. The native AGY statusline (integrations/agy/status.py)
    persists identity + four-pool quota sidecars directly from the AGY session
    payload, and this gateway invokes exactly ONE publisher invocation:
      integrations/herdr/agy_publisher.py → cen_agy_identity, cen_agy_quota
    Legacy: refresh/watch --provider agy and the upstream snapshot cache are
    inert rollback-only artifacts (see /docs).
- CODEX:
    Exactly ONE invocation of the CEN pane-scoped publisher:
    integrations/codex/publisher.py
    The publisher owns ALL Codex quota publication for the event pane.
    ABSOLUTE: no `refresh --provider codex`, no `watch --provider codex`.
- Zero calls with '--provider all'
- Zero mutations to Claude, Grok, Pi, or deferred harness configurations
"""

import os
import sys
import json
import subprocess

CODEX_PUBLISHER = os.path.join(
    os.path.dirname(os.path.realpath(os.path.abspath(__file__))),
    "..", "codex", "publisher.py"
)
AGY_PUBLISHER = os.path.join(
    os.path.dirname(os.path.realpath(os.path.abspath(__file__))),
    "agy_publisher.py"
)

CODEX_PROVIDERS = {"codex"}
AGY_PROVIDERS = {
    "agy": "agy",
    "antigravity": "agy",
}

def find_field_recursive(obj, keys):
    if isinstance(obj, dict):
        for k in keys:
            if k in obj and isinstance(obj[k], str):
                return obj[k]
        for v in obj.values():
            res = find_field_recursive(v, keys)
            if res:
                return res
    elif isinstance(obj, list):
        for item in obj:
            res = find_field_recursive(item, keys)
            if res:
                return res
    return None

def main():
    raw_event = os.environ.get("HERDR_PLUGIN_EVENT_JSON", "")
    if not raw_event.strip():
        sys.exit(0)

    try:
        event_data = json.loads(raw_event)
    except Exception:
        sys.exit(0)

    raw_agent = find_field_recursive(event_data, ["agent", "provider"])
    if not raw_agent:
        sys.exit(0)

    provider_key = raw_agent.strip().lower()

    if provider_key in CODEX_PROVIDERS:
 # CODEX: exactly one pane-scoped CEN publisher invocation.
 # ABSOLUTE FIREWALL: never invoke upstream refresh/watch for codex.
        if not os.path.isfile(CODEX_PUBLISHER):
            sys.exit(0)
        try:
            subprocess.run(
                [sys.executable, CODEX_PUBLISHER],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                check=False,
            )
        except Exception:
            pass
        sys.exit(0)

    if provider_key not in AGY_PROVIDERS:
 # STRICT FIREWALL: Exit immediately on claude, grok, pi, unknown
        sys.exit(0)

 # AGY / antigravity: exactly one compact-summary publisher invocation.
 # Native-first: no upstream refresh, no watch, no state-dir env.
    if not os.path.isfile(AGY_PUBLISHER):
        sys.exit(0)
    try:
        subprocess.run(
            [sys.executable, AGY_PUBLISHER],
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
    except Exception:
        pass

    sys.exit(0)

if __name__ == "__main__":
    main()
