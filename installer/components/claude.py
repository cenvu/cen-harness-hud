"""Claude Code component: native StatusLine command in settings.json.

Claude is first-generation CEN configuration.  An exact pre-existing
command is compatible but ownership is unproven, so it is never adopted.
Only nested JSON surfaces actually inserted or changed by this install are
recorded for schema-v2 uninstall.
"""

import copy
import os
import shlex

from .. import patching
from .. import semantic as sem_mod
from ..paths import ComponentError, ConflictError


def desired_command(ctx) -> str:
    py = os.environ.get("CEN_INSTALL_PYTHON") or "python3"
    return f"{py} {shlex.quote(ctx.claude_status_installed)}"


def _semantic(path: str, key: str, before, value: str,
              containers=None) -> dict:
    return sem_mod.rec(path, "json", "JSON_KEY", key, before, value,
                       "insert", containers=containers)


def _created_statusline_container(command: str) -> list:
    return [{
        "path": "statusLine",
        "keys": ["type", "command"],
        "values": {"type": "command", "command": command},
    }]


def _statusline_semantics(path: str, status_line: dict, command: str,
                          own_command: bool, own_type: bool,
                          command_before=None, type_before=None,
                          container=False) -> list:
    records = []
    if own_command:
        if command_before is None:
            command_before = sem_mod.ABSENT
        records.append(_semantic(
            path, "statusLine.command", command_before, command,
            containers=(_created_statusline_container(command)
                        if container else None),
        ))
    if own_type:
        if type_before is None:
            type_before = sem_mod.ABSENT
        records.append(_semantic(
            path, "statusLine.type", type_before, "command"))
    return records


def _planned_document(data: dict, status_line: dict, command: str) -> dict:
    out = copy.deepcopy(data)
    out["statusLine"] = dict(status_line)
    out["statusLine"]["type"] = "command"
    out["statusLine"]["command"] = command
    return out


def _parent_actions(path: str) -> list:
    """Create the Claude config root only when it is genuinely absent.

    A pre-existing directory is user-owned, including a symlink resolving to
    a directory.  It must not be passed to ensure_dir(), whose contract also
    normalizes modes on existing directories.
    """
    parent = os.path.dirname(path)
    if not os.path.lexists(parent):
        return [{
            "type": "ensure_dir",
            "path": parent,
            "mode": 0o700,
        }]
    if not os.path.isdir(parent):
        raise ConflictError(
            "claude: ~/.claude exists but is not a directory; refusing to "
            "replace it"
        )
    return []


def plan(ctx) -> dict:
    path = ctx.claude_settings_path
    command = desired_command(ctx)
    actions = []
    notes = []

    if not os.path.exists(path):
        actions.extend(_parent_actions(path))
        actions.append(
            {
                "type": "write_json_new",
                "path": path,
                "data": {
                    "statusLine": {
                        "type": "command",
                        "command": command,
                    }
                },
                "mode": 0o600,
                "semantics": _statusline_semantics(
                    path, {}, command, True, True,
                    container=True,
                ),
            }
        )
        notes.append("settings.json absent — will be created CEN-owned")
        return {"component": "claude", "actions": actions,
                "claims": [], "notes": notes, "noop": False}

    try:
        data = patching.load_json(path)
    except Exception as e:
        raise ComponentError(f"claude: malformed JSON in {path}: {e}")
    if not isinstance(data, dict):
        raise ComponentError(
            "claude: settings.json root is not a JSON object"
        )

    if "statusLine" not in data:
        status_line = {}
        desired = _planned_document(data, status_line, command)
        semantics = _statusline_semantics(
            path, status_line, command, True, True, container=True)
        actions.append({
            "type": "patch_json_document",
            "path": path,
            "expected_current_data": data,
            "desired_data": desired,
            "backup_name": "settings.json",
            "semantics": semantics,
        })
        return {"component": "claude", "actions": actions,
                "claims": [], "notes": notes, "noop": False}

    status_line = data["statusLine"]
    if not isinstance(status_line, dict):
        raise ConflictError(
            "claude: foreign non-object statusLine present; refusing to "
            "overwrite (uninstall/restore manually first)"
        )

    has_type = "type" in status_line
    type_value = status_line.get("type")
    if has_type and type_value != "command":
        raise ConflictError(
            "claude: foreign statusLine.type present; refusing to overwrite"
        )

    has_command = "command" in status_line
    current = status_line.get("command")
    if has_command and not isinstance(current, str):
        raise ConflictError(
            "claude: foreign non-string statusLine.command present; "
            "refusing to overwrite"
        )

    if has_command and current == command:
        if type_value == "command":
            notes.append(
                "statusLine.command already compatible — left user-owned"
            )
            return {"component": "claude", "actions": [], "claims": [],
                    "notes": notes, "noop": True}

        # The exact command is compatible, but type is absent and therefore
        # the only CEN-owned surface is the type inserted by this attempt.
        desired = copy.deepcopy(data)
        desired["statusLine"]["type"] = "command"
        actions.append({
            "type": "patch_json_document",
            "path": path,
            "expected_current_data": data,
            "desired_data": desired,
            "backup_name": "settings.json",
            "semantics": _statusline_semantics(
                path, status_line, command, False, True,
                type_before=sem_mod.ABSENT,
            ),
        })
        notes.append(
            "statusLine.command already compatible — type inserted; "
            "command left user-owned"
        )
        return {"component": "claude", "actions": actions,
                "claims": [], "notes": notes, "noop": False}

    is_empty = has_command and current == ""
    is_absent = not has_command
    if not (is_empty or is_absent):
        raise ConflictError(
            "claude: foreign statusLine.command present; refusing to "
            "overwrite (uninstall/restore manually first)"
        )

    desired = _planned_document(data, status_line, command)
    command_before = (sem_mod.value_before(current) if is_empty
                      else sem_mod.ABSENT)
    type_before = sem_mod.ABSENT if not has_type else None
    semantics = _statusline_semantics(
        path, status_line, command, True, not has_type,
        command_before=command_before, type_before=type_before,
    )
    actions.append({
        "type": "patch_json_document",
        "path": path,
        "expected_current_data": data,
        "desired_data": desired,
        "backup_name": "settings.json",
        "semantics": semantics,
    })
    return {"component": "claude", "actions": actions,
            "claims": [], "notes": notes, "noop": False}
