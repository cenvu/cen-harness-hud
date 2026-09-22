"""AGY component: patch ~/.gemini/antigravity-cli/settings.json
statusLine.command → installed integrations/agy/status.py (direct, ).

Conflict policy (CP10):
- statusLine.command missing / empty        → install
- exactly the CEN desired command           → adopt as semantic claim
- old CEN command (proven product-share-root path) → migrate + claim
- any other non-empty foreign command       → CONFLICT, never overwrite

Schema-v2 semantic surfaces: statusLine.command and, only when CEN actually
creates/changes it or it proves inherited CEN ownership, statusLine.type.
A pre-existing non-object statusLine (string/list/null/number/boolean) is
never replaced: install fails closed with CONFLICT.

Preserves every unrelated JSON field. Malformed JSON → FAIL CLOSED before
any mutation. The retired herdr-agent-quota binary is never referenced.
"""

import os
import shlex

from .. import patching
from .. import semantic as sem_mod
from ..paths import ComponentError, ConflictError


def desired_command(ctx) -> str:
    py = os.environ.get("CEN_INSTALL_PYTHON") or "python3"
    return f"{py} {shlex.quote(ctx.agy_status_installed)}"


STATUS_ENTRYPOINT_SUFFIX = os.path.join("integrations", "agy", "status.py")


def is_proven_cen_command(raw: str, ctx) -> bool:
    """Narrow structural proof that a statusLine command is CEN-authored.

    Accepts ONLY `<py> <quoted-or-bare path>` where the path resolves inside
    the CEN product share root and ends at the AGY status entrypoint. Never
    matches by loose substring; foreign status.py paths fail closed.
    """
    if not isinstance(raw, str) or not raw.strip():
        return False
    try:
        argv = shlex.split(raw.strip())
    except Exception:
        return False
    if len(argv) != 2:
        return False
    if not argv[0] or "/" in argv[0]:
        return False
    candidate = os.path.realpath(os.path.expanduser(argv[1]))
    root = os.path.realpath(ctx.share_root)
    if candidate != root and not candidate.startswith(root + os.sep):
        return False
    return candidate.endswith(STATUS_ENTRYPOINT_SUFFIX)


def _semantic(path: str, key: str, before, value, basis: str,
              containers: list = None) -> dict:
    return sem_mod.rec(path, "json", "JSON_KEY", key,
                       before, value, basis, containers=containers)


def _statusline_containers(cmd: str) -> list:
    return [{"path": "statusLine", "keys": ["type", "command"],
             "values": {"type": "command", "command": cmd}}]


def plan(ctx) -> dict:
    path = ctx.agy_settings_path
    cmd = desired_command(ctx)
    actions = []
    notes = []
    claims = []

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
            "semantics": [
                _semantic(path, "statusLine.command", sem_mod.ABSENT,
                          cmd, "insert",
                          containers=_statusline_containers(cmd)),
                _semantic(path, "statusLine.type", sem_mod.ABSENT,
                          "command", "insert"),
            ],
            "mode": 0o644,
        })
        return {"component": "agy", "actions": actions, "claims": claims,
                "notes": [
                    "settings.json absent — will be created CEN-owned"],
                "noop": False}

    try:
        data = patching.load_json(path)
    except Exception as e:
        raise ComponentError(f"agy: malformed JSON in {path}: {e}")

    if not isinstance(data, dict):
        raise ComponentError("agy: settings.json root is not a JSON object")

    sl = data.get("statusLine")
    has_status_line = "statusLine" in data
    if has_status_line and not isinstance(sl, dict):
        raise ConflictError(
            "agy: foreign non-object statusLine present; refusing to "
            "overwrite (uninstall/restore manually first)"
        )
    sl_dict = sl if isinstance(sl, dict) else None
    current = sl_dict.get("command") if sl_dict is not None else None

    def _type_records() -> list:
        """Type surface records for an insert that (re)writes type."""
        if sl_dict is None or "type" not in sl_dict:
            return [_semantic(path, "statusLine.type", sem_mod.ABSENT,
                              "command", "insert")]
        if sl_dict.get("type") == "command":
            return [] # pre-existing generic type: leave user-owned (D)
        return [_semantic(path, "statusLine.type",
                          sem_mod.value_before(sl_dict["type"]),
                          "command", "insert")]

    if current is None or (isinstance(current, str) and not current.strip()):
        if sl_dict is not None:
            updates = {"statusLine": dict(sl_dict, type="command",
                                           command=cmd)}
            containers = []
            before = (sem_mod.ABSENT if "command" not in sl_dict
                      else sem_mod.value_before(sl_dict["command"]))
        else:
            updates = {"statusLine": {"type": "command", "command": cmd}}
            containers = _statusline_containers(cmd)
            before = sem_mod.ABSENT
        semantics = [
            _semantic(path, "statusLine.command", before, cmd, "insert",
                      containers=containers),
        ]
        semantics.extend(_type_records())
        actions.append({
            "type": "patch_json", "path": path, "updates": updates,
            "backup_name": "settings.json",
            "semantics": semantics,
        })
    elif current == cmd:
        notes.append("statusLine.command already CEN-owned — no-op")
        claims.append(_semantic(path, "statusLine.command", sem_mod.ABSENT,
                                cmd, "adopt",
                                containers=_statusline_containers(cmd)))
        if sl_dict is not None and sl_dict.get("type") == "command":
            claims.append(_semantic(path, "statusLine.type", sem_mod.ABSENT,
                                    "command", "adopt"))
    elif is_proven_cen_command(current, ctx):
        notes.append("statusLine.command is a proven older CEN command — "
                     "will migrate in place")
        updates = {"statusLine": dict(sl_dict, type="command", command=cmd)}
        semantics = [
            _semantic(path, "statusLine.command", sem_mod.ABSENT, cmd,
                      "migrate", containers=_statusline_containers(cmd)),
        ]
        if sl_dict.get("type") == "command" or "type" not in sl_dict:
            # inherited CEN type (or type CEN is about to write): the user
            # baseline is ABSENT, so uninstall removes CEN residue.
            semantics.append(_semantic(path, "statusLine.type",
                                       sem_mod.ABSENT, "command", "migrate"))
        else:
            semantics.append(_semantic(path, "statusLine.type",
                                       sem_mod.value_before(sl_dict["type"]),
                                       "command", "migrate"))
        actions.append({
            "type": "patch_json", "path": path, "updates": updates,
            "backup_name": "settings.json",
            "semantics": semantics,
        })
    else:
        raise ConflictError(
            "agy: foreign statusLine.command present; refusing to overwrite "
            "(uninstall/restore manually first)"
        )

    return {"component": "agy", "actions": actions, "claims": claims,
            "notes": notes, "noop": not actions}
