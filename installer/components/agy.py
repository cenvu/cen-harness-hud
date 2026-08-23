"""AGY component: patch ~/.gemini/antigravity-cli/settings.json
statusLine.command → installed integrations/agy/status.py (direct, ).

Conflict policy (CP10):
- statusLine.command missing / empty        → install
- exactly the CEN desired command           → adopt / no-op
- any other non-empty foreign command       → CONFLICT, never overwrite

Preserves every unrelated JSON field. Malformed JSON → FAIL CLOSED before
any mutation. The retired herdr-agent-quota binary is never referenced.
"""

import os
import shlex

from .. import patching
from ..paths import ComponentError, ConflictError


def desired_command(ctx) -> str:
    py = os.environ.get("CEN_INSTALL_PYTHON") or "python3"
    return f"{py} {shlex.quote(ctx.agy_status_installed)}"


def plan(ctx) -> dict:
    path = ctx.agy_settings_path
    cmd = desired_command(ctx)
    actions = []
    notes = []

    if not os.path.exists(path):
        actions.append({
            "type": "ensure_dir",
            "path": os.path.dirname(path),
            "mode": 0o755,
        })
        actions.append({
            "type": "write_json_new",
            "path": path,
            "data": {"statusLine": {"type": "command", "command": cmd}},
        })
        return {"component": "agy", "actions": actions, "notes": [
            "settings.json absent — will be created CEN-owned"], "noop": False}

    try:
        data = patching.load_json(path)
    except Exception as e:
        raise ComponentError(f"agy: malformed JSON in {path}: {e}")

    if not isinstance(data, dict):
        raise ComponentError("agy: settings.json root is not a JSON object")

    sl = data.get("statusLine")
    current = sl.get("command") if isinstance(sl, dict) else None

    if current is None or (isinstance(current, str) and not current.strip()):
        if isinstance(sl, dict):
            updates = {"statusLine": dict(sl, type="command", command=cmd)}
        else:
            updates = {"statusLine": {"type": "command", "command": cmd}}
        actions.append({
            "type": "patch_json", "path": path, "updates": updates,
            "backup_name": "settings.json",
        })
    elif current == cmd:
        notes.append("statusLine.command already CEN-owned — no-op")
    else:
        raise ConflictError(
            "agy: foreign statusLine.command present; refusing to overwrite "
            "(uninstall/restore manually first)"
        )

    return {"component": "agy", "actions": actions, "notes": notes,
            "noop": not actions}
