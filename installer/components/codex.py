"""Codex component: native footer, SessionStart telemetry hook, command links.

Known CEN-owned prior values migrate in place. Foreign/custom values fail
closed. auth.json is never read.
"""

import copy
import os

from .. import patching
from .. import semantic as sem_mod
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
        ("cen-codex-status", ctx.codex_status_installed),
    ]


def _session_hook_command(ctx) -> str:
    return ctx.codex_session_hook_installed


SESSION_HOOK_SUFFIX = os.path.join("integrations", "codex",
                                     "session_hook.py")


def _is_proven_cen_hook_command(raw: object, command: str, ctx) -> bool:
    """Narrow proof that a hooks.json command is an older CEN hook: an
    absolute path resolving inside the CEN product share root and ending at
    the session-hook entrypoint, but not equal to the current command.
    Unrelated commands (including foreign session_hook.py paths) fail every
    check and are always preserved."""
    if not isinstance(raw, str) or not raw.strip():
        return False
    if raw == command:
        return False
    candidate = os.path.realpath(os.path.expanduser(raw.strip()))
    share = os.path.realpath(ctx.share_root)
    if candidate != share and not candidate.startswith(share + os.sep):
        return False
    return candidate.endswith(SESSION_HOOK_SUFFIX)


def _strip_legacy_hooks(data: dict, command: str, ctx):
    """Remove proven older CEN SessionStart entries, preserving everything
    else. Returns (document, removed_any)."""
    out = copy.deepcopy(data)
    hooks = out.get("hooks")
    if not isinstance(hooks, dict):
        return out, False
    entries = hooks.get("SessionStart")
    if not isinstance(entries, list):
        return out, False
    removed = False
    survivors = []
    for entry in entries:
        if not isinstance(entry, dict):
            survivors.append(entry)
            continue
        nested = entry.get("hooks")
        if not isinstance(nested, list):
            survivors.append(entry)
            continue
        kept = [h for h in nested
                if not (isinstance(h, dict)
                        and h.get("type") == "command"
                        and _is_proven_cen_hook_command(
                            h.get("command"), command, ctx))]
        if len(kept) != len(nested):
            removed = True
        if kept:
            survivors.append({**entry, "hooks": kept})
        elif set(entry.keys()) != {"hooks"}:
            survivors.append({**entry, "hooks": []})
    if removed:
        if survivors:
            hooks["SessionStart"] = survivors
        else:
            hooks.pop("SessionStart", None)
    return out, removed


def _toml_semantic(path, table, key, value, basis):
    return sem_mod.rec(path, "toml", "TOML_KEY", key, sem_mod.ABSENT,
                       value, basis, table=table)


def _hook_semantic(hooks_path, command, basis, containers=None):
    return sem_mod.rec(hooks_path, "json", "CODEX_SESSION_HOOK", command,
                       sem_mod.ABSENT, True, basis, containers=containers)


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


def _plan_status_line(path, text, actions, notes, claims):
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
            "semantic": _toml_semantic(path, ("tui",), "status_line",
                                       STATUS_LINE_DESIRED, "insert"),
        })
    elif state == "equal":
        notes.append("codex [tui].status_line already CEN-owned — no-op")
        claims.append(_toml_semantic(path, ("tui",), "status_line",
                                     STATUS_LINE_DESIRED, "adopt"))
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
                "semantic": _toml_semantic(path, ("tui",), "status_line",
                                           STATUS_LINE_DESIRED, "migrate"),
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
            "semantic": _toml_semantic(path, ("features",), "hooks",
                                       True, "insert"),
        })
    elif state == "equal":
        notes.append("codex [features].hooks already enabled — no-op")
    else:
        raise ConflictError(
            "codex: foreign [features].hooks=false present; refusing to overwrite"
        )


def _reject_unsafe_custom_file(path: str) -> None:
    """Do not replace a custom profile file through a symlink or directory."""
    if os.path.lexists(path) and (
            os.path.islink(path) or not os.path.isfile(path)):
        raise ConflictError(
            "codex: custom profile target is not a regular file; refusing "
            "to overwrite"
        )


def _plan_session_hook(ctx, hooks_path, actions, notes, claims,
                       reject_unsafe=False):
    if reject_unsafe:
        _reject_unsafe_custom_file(hooks_path)
    command = _session_hook_command(ctx)
    if not os.path.exists(hooks_path):
        desired, _ = _desired_hooks({}, command)
        actions.append({
            "type": "write_json_new",
            "path": hooks_path,
            "data": desired,
            "mode": 0o600 if reject_unsafe else 0o644,
            "semantics": [
                _hook_semantic(
                    hooks_path, command, "insert",
                    containers=[
                        {"path": "hooks", "keys": ["SessionStart"],
                         "values": {"SessionStart": desired["hooks"][
                             "SessionStart"]}},
                        {"path": "hooks.SessionStart",
                         "keys": [], "kind": "list"}]),
            ],
        })
        return

    try:
        current = patching.load_json(hooks_path)
        stripped, legacy_removed = _strip_legacy_hooks(
            current, command, ctx)
        had_hooks = isinstance(current.get("hooks"), dict)
        had_ss = isinstance((current.get("hooks") or {}).get(
            "SessionStart"), list)
        desired, changed = _desired_hooks(stripped, command)
        changed = changed or legacy_removed
    except ComponentError:
        raise
    except Exception as e:
        raise ComponentError(
            f"codex: malformed hooks.json ({e}); fail-closed"
        )
    if changed:
        containers = []
        if not had_hooks:
            containers.append(
                {"path": "hooks", "keys": ["SessionStart"],
                 "values": {"SessionStart": desired["hooks"][
                     "SessionStart"]}})
        if not had_ss:
            containers.append({"path": "hooks.SessionStart",
                               "keys": [], "kind": "list"})
        basis = "migrate" if legacy_removed else "insert"
        actions.append({
            "type": "patch_json_document",
            "path": hooks_path,
            "expected_current_data": current,
            "desired_data": desired,
            "backup_name": "hooks.json",
            "semantic": _hook_semantic(hooks_path, command, basis,
                                       containers=containers),
        })
        if legacy_removed:
            notes.append(
                "codex SessionStart hook: proven older CEN entry "
                "migrated — exactly one current hook afterwards"
            )
    else:
        notes.append(
            "codex SessionStart telemetry hook already CEN-owned — no-op"
        )
        claims.append(_hook_semantic(hooks_path, command, "adopt"))


def _plan_custom_home(ctx, codex_home, actions, notes, claims):
    """Plan only telemetry prerequisites for one explicit extra profile."""
    config_path = os.path.join(codex_home, "config.toml")
    if not os.path.lexists(config_path):
        actions.append({
            "type": "write_text_new",
            "path": config_path,
            "text": "[features]\nhooks = true\n",
            "mode": 0o600,
            "semantics": [
                _toml_semantic(config_path, ("features",), "hooks",
                               True, "insert"),
            ],
        })
    else:
        _reject_unsafe_custom_file(config_path)
        try:
            with open(config_path, "r") as f:
                text = f.read()
        except OSError as e:
            raise ComponentError(
                f"codex: cannot read custom profile config ({e}); "
                "fail-closed"
            )
        _plan_hooks_feature(config_path, text, actions, notes)

    hooks_path = os.path.join(codex_home, "hooks.json")
    _plan_session_hook(ctx, hooks_path, actions, notes, claims,
                       reject_unsafe=True)


def plan(ctx) -> dict:
    actions = []
    notes = []
    claims = []
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
            "semantics": [
                _toml_semantic(path, ("features",), "hooks", True,
                               "insert"),
                _toml_semantic(path, ("tui",), "status_line",
                               STATUS_LINE_DESIRED, "insert"),
            ],
        })
    else:
        with open(path, "r") as f:
            text = f.read()
        _plan_status_line(path, text, actions, notes, claims)
        _plan_hooks_feature(path, text, actions, notes)

    # 2. Native SessionStart hook learns the actual profile and provides a
    # stable /clear completion refresh hook. Existing unrelated hooks survive.
    _plan_session_hook(ctx, ctx.codex_hooks_path, actions, notes, claims)

    # 3. Explicit alternate profiles receive only telemetry prerequisites.
    for codex_home in ctx.codex_extra_homes:
        _plan_custom_home(ctx, codex_home, actions, notes, claims)

    # 4. Command symlinks into installed product root.
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
        "claims": claims,
        "notes": notes,
        "noop": not any(a["type"] != "noop" for a in actions),
    }
