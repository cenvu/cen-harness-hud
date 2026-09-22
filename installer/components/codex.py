"""Codex component: native footer, SessionStart telemetry hook, command links.

Known CEN-owned prior values migrate in place. Foreign/custom values fail
closed. auth.json is never read.
"""

import copy
import os

from .. import patching
from ..paths import ComponentError, ConflictError

STATUS_LINE_LEGACY_V020 = [
    "model-with-reasoning",
    "context-remaining",
    "five-hour-limit",
    "weekly-limit",
]
STATUS_LINE_DESIRED = [
    "model-with-reasoning",
    "status",
    "context-remaining",
    "five-hour-limit",
    "weekly-limit",
]
STATUS_LINE_LITERAL = ['  "{}",'.format(v) for v in STATUS_LINE_DESIRED]


def _command_symlinks(ctx) -> list:
    return [
        ("cen-codex", ctx.codex_launcher_installed),
        ("cen-codex-status", ctx.codex_status_installed),
    ]


def _session_hook_command(ctx) -> str:
    return ctx.codex_session_hook_installed


def _desired_hooks(data, command: str):
    if not isinstance(data, dict):
        raise ComponentError("codex: hooks.json root is not a JSON object")
    out = copy.deepcopy(data)
    hooks = out.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ComponentError("codex: hooks.json hooks is not a JSON object")
    entries = hooks.setdefault("SessionStart", [])
    if not isinstance(entries, list):
        raise ComponentError("codex: SessionStart hooks is not an array")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        nested = entry.get("hooks")
        if not isinstance(nested, list):
            continue
        for hook in nested:
            if (
                isinstance(hook, dict)
                and hook.get("type") == "command"
                and hook.get("command") == command
            ):
                return out, False
    entries.append({
        "hooks": [{
            "type": "command",
            "command": command,
            "timeout": 10,
        }]
    })
    return out, True


def _plan_status_line(path, text, actions, notes):
    try:
        state = patching.classify_toml_key(
            text, ("tui",), "status_line", STATUS_LINE_DESIRED
        )
    except Exception as e:
        raise ComponentError(f"codex: cannot parse {path} ({e}); fail-closed")

    if state == "absent":
        actions.append({
            "type": "patch_toml_insert",
            "path": path,
            "table": ("tui",),
            "key": "status_line",
            "literal_lines": STATUS_LINE_LITERAL,
            "desired_value": STATUS_LINE_DESIRED,
            "backup_name": "config.toml",
        })
    elif state == "equal":
        notes.append("codex [tui].status_line already CEN-owned — no-op")
    else:
        legacy = patching.classify_toml_key(
            text, ("tui",), "status_line", STATUS_LINE_LEGACY_V020
        )
        if legacy == "equal":
            actions.append({
                "type": "patch_toml_replace_owned",
                "path": path,
                "table": ("tui",),
                "key": "status_line",
                "expected_current_value": STATUS_LINE_LEGACY_V020,
                "literal_lines": STATUS_LINE_LITERAL,
                "desired_value": STATUS_LINE_DESIRED,
                "backup_name": "config.toml",
            })
            notes.append(
                "codex [tui].status_line exact prior CEN value — will migrate"
            )
        else:
            raise ConflictError(
                "codex: foreign [tui].status_line present; refusing to overwrite"
            )


def _plan_hooks_feature(path, text, actions, notes):
    try:
        state = patching.classify_toml_key(
            text, ("features",), "hooks", True
        )
    except Exception as e:
        raise ComponentError(f"codex: cannot parse {path} ({e}); fail-closed")
    if state == "absent":
        actions.append({
            "type": "patch_toml_scalar_insert",
            "path": path,
            "table": ("features",),
            "key": "hooks",
            "literal": "true",
            "desired_value": True,
            "backup_name": "config.toml",
        })
    elif state == "equal":
        notes.append("codex [features].hooks already enabled — no-op")
    else:
        raise ConflictError(
            "codex: foreign [features].hooks=false present; refusing to overwrite"
        )


def plan(ctx) -> dict:
    actions = []
    notes = []
    cen_roots = [ctx.share_root]

    # 1. Preserve native run-state/activity plus native quota in the footer.
    path = ctx.codex_config_path
    if not os.path.exists(path):
        actions.append({
            "type": "ensure_dir",
            "path": os.path.dirname(path),
            "mode": 0o755,
        })
        actions.append({
            "type": "write_text_new",
            "path": path,
            "text": "[features]\nhooks = true\n\n[tui]\nstatus_line = [\n"
                    + "\n".join(STATUS_LINE_LITERAL)
                    + "\n]\n",
            "mode": 0o644,
        })
    else:
        with open(path, "r") as f:
            text = f.read()
        _plan_status_line(path, text, actions, notes)
        _plan_hooks_feature(path, text, actions, notes)

    # 2. Native SessionStart hook learns the actual profile and provides a
    # stable /clear completion refresh hook. Existing unrelated hooks survive.
    hooks_path = ctx.codex_hooks_path
    command = _session_hook_command(ctx)
    if not os.path.exists(hooks_path):
        desired, _ = _desired_hooks({}, command)
        actions.append({
            "type": "write_json_new",
            "path": hooks_path,
            "data": desired,
            "mode": 0o644,
        })
    else:
        try:
            current = patching.load_json(hooks_path)
            desired, changed = _desired_hooks(current, command)
        except ComponentError:
            raise
        except Exception as e:
            raise ComponentError(
                f"codex: malformed hooks.json ({e}); fail-closed"
            )
        if changed:
            actions.append({
                "type": "patch_json_document",
                "path": hooks_path,
                "expected_current_data": current,
                "desired_data": desired,
                "backup_name": "hooks.json",
            })
        else:
            notes.append(
                "codex SessionStart telemetry hook already CEN-owned — no-op"
            )

    # 3. Command symlinks into installed product root.
    for name, target in _command_symlinks(ctx):
        link = os.path.join(ctx.bin_dir, name)
        cls = patching.classify_symlink(link, target, cen_roots)
        if cls == "absent":
            actions.append({"type": "symlink", "path": link, "target": target})
        elif cls == "equal":
            notes.append(f"~/.local/bin/{name} already correct — no-op")
        elif cls == "cen_retargetable":
            actions.append({
                "type": "symlink",
                "path": link,
                "target": target,
                "replace_cen_owned": True,
            })
            notes.append(
                f"~/.local/bin/{name} retargeted to current install root"
            )
        else:
            raise ConflictError(
                f"codex: ~/.local/bin/{name} exists and is not CEN-owned; "
                "refusing to overwrite"
            )

    return {
        "component": "codex",
        "actions": actions,
        "notes": notes,
        "noop": not any(a["type"] != "noop" for a in actions),
    }
