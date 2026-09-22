"""Herdr component: generated plugin manifest + registry + sidebar rows.

- herdr-plugin.toml is GENERATED from the template with the installed
  product path — never the dev repo, never the retired upstream binary.
- plugins.json: CEN plugin_id absent → add; same registration → no-op;
  same id with foreign path → CONFLICT.
- config.toml rows_by_agent: per-key install/adopt/CONFLICT. Global
  row_gap is NEVER touched (no proven ownership).
- No Herdr restart is performed by the installer.
"""

import json
import os

from .. import patching
from .. import semantic as sem_mod
from ..paths import ComponentError, ConflictError

PLUGIN_ID = "cen-harness-hud-quota"

AGY_ROWS = [
    ["workspace", "tab"],
    ["agent", "state_text"],
    [{"token": "$cen_agy_identity", "bold": True}],
    [{"token": "$cen_agy_quota", "bold": True}],
]
CODEX_ROWS = [
    ["workspace", "tab"],
    ["agent", "state_text"],
    [{"token": "$cen_codex_identity", "bold": True}],
    [{"token": "$cen_codex_weekly", "bold": True}],
]
# Exact Codex row shape produced by public v0.2.0. It is CEN-owned and may
# migrate to the compact weekly-only card.
CODEX_ROWS_LEGACY_V020 = [
    ["workspace", "tab"],
    ["agent", "state_text"],
    [{"token": "$cen_codex_identity", "bold": True}],
    [{"token": "$cen_codex_window_1", "bold": True}],
    [{"token": "$cen_codex_window_2", "bold": True}],
]
# EXACT Codex row shape produced by public product v0.1.0. Recognized
# solely to allow controlled migration of CEN's OWN prior output when no
# manifest ownership record survives; ANY other value stays foreign.
CODEX_ROWS_LEGACY_V010 = [
    ["workspace", "tab"],
    ["agent", "state_text"],
    [{"token": "$cen_codex_summary", "bold": True}],
]
PI_ROWS = [
    ["workspace", "tab"],
    ["agent", "state_text"],
    [{"token": "$cen_ds_balance", "fg": "#6fb5b7", "bold": True}],
]
# OpenCode card uses Herdr-native lifecycle truth: Herdr detects the
# opencode agent and renders its native state; CEN publishes no OpenCode
# metadata.
OPENCODE_ROWS = [
    ["workspace", "tab"],
    ["agent", "state_text"],
]

# desired TOML literal INNER lines (the wrapper `key = [` / `]` is added by
# patching.insert_toml_key)
ROWS_LITERAL = {
    "agy": [
        '  ["workspace", "tab"],',
        '  ["agent", "state_text"],',
        "  [",
        '    { token = "$cen_agy_identity", bold = true }',
        "  ],",
        "  [",
        '    { token = "$cen_agy_quota", bold = true }',
        "  ]",
    ],
    "codex": [
        '  ["workspace", "tab"],',
        '  ["agent", "state_text"],',
        "  [",
        '    { token = "$cen_codex_identity", bold = true }',
        "  ],",
        "  [",
        '    { token = "$cen_codex_weekly", bold = true }',
        "  ]",
    ],
    "pi": [
        '  ["workspace", "tab"],',
        '  ["agent", "state_text"],',
        "  [",
        '    { token = "$cen_ds_balance", fg = "#6fb5b7", bold = true }',
        "  ]",
    ],
    "opencode": [
        '  ["workspace", "tab"],',
        '  ["agent", "state_text"]',
    ],
}
ROWS_TABLE = ("ui", "sidebar", "agents", "rows_by_agent")


def render_plugin_manifest(ctx) -> str:
    tmpl_path = os.path.join(ctx.source_root, "templates",
                             "herdr-plugin.toml.tmpl")
    if not os.path.isfile(tmpl_path):
        # fall back to installed product template (installed runs)
        tmpl_path = os.path.join(ctx.install_root, "templates",
                                 "herdr-plugin.toml.tmpl")
    with open(tmpl_path, "r") as f:
        t = f.read()
    rendered = (
        t.replace("{{VERSION}}", ctx.version)
        .replace("{{PYTHON}}", "python3")
        .replace("{{SCOPED_EVENT}}", ctx.scoped_event_installed)
    )
    # fail closed: an unresolved placeholder would publish broken metadata
    for placeholder in ("{{VERSION}}", "{{PYTHON}}", "{{SCOPED_EVENT}}"):
        if placeholder in rendered:
            raise ComponentError(
                "herdr: unresolved template placeholder %s in generated "
                "plugin manifest" % placeholder)
    return rendered


def _registration(ctx) -> dict:
    return {
        "plugin_id": PLUGIN_ID,
        "name": "CEN Harness HUD Quota",
        "version": ctx.version,
        "min_herdr_version": "0.8.0",
        "description": "Provider-scoped AI agent quota badges for Herdr "
                       "(AGY and Codex)",
        "manifest_path": ctx.generated_plugin_manifest,
        "plugin_root": ctx.install_root,
        "enabled": True,
        "platforms": ["linux", "macos"],
        "events": [
            {
                "id": "cen-agent-detected",
                "on": "pane.agent_detected",
                "command": ["python3", ctx.scoped_event_installed],
            },
            {
                "id": "cen-agent-status-changed",
                "on": "pane.agent_status_changed",
                "command": ["python3", ctx.scoped_event_installed],
            },
        ],
    }


def _row_semantic(cfg_path, key, value, basis):
    return sem_mod.rec(cfg_path, "toml", "TOML_KEY", key, sem_mod.ABSENT,
                       value, basis, table=ROWS_TABLE)


def _reg_semantic(reg_path, reg, basis):
    return sem_mod.rec(reg_path, "json", "JSON_LIST_ENTRY", PLUGIN_ID,
                       sem_mod.ABSENT, reg, basis)


def _is_proven_cen_registration(entry: dict, ctx) -> bool:
    """Narrow proof that a plugins.json entry is an older CEN registration:
    same plugin id plus manifest/plugin paths resolving inside the CEN
    product share root. Foreign same-id entries fail closed."""
    try:
        manifest = os.path.realpath(entry.get("manifest_path") or "")
        root = os.path.realpath(entry.get("plugin_root") or "")
    except Exception:
        return False
    share = os.path.realpath(ctx.share_root)
    if not manifest.startswith(share + os.sep):
        return False
    return root == share or root.startswith(share + os.sep)


def plan(ctx) -> dict:
    actions = []
    notes = []
    claims = []

    # 1. generated plugin manifest into install root
    actions.append({
        "type": "write_text_new",
        "path": ctx.generated_plugin_manifest,
        "text": render_plugin_manifest(ctx),
        "mode": 0o644,
    })

    # 2. plugin registry
    reg_path = ctx.herdr_plugins_path
    reg = _registration(ctx)
    if not os.path.exists(reg_path):
        actions.append({
            "type": "ensure_dir",
            "path": os.path.dirname(reg_path),
            "mode": 0o755,
        })
        actions.append({
            "type": "write_json_new",
            "path": reg_path,
            "data": [reg],
            "semantic": _reg_semantic(reg_path, reg, "insert"),
        })
        notes.append("plugins.json absent — will be created CEN-owned")
    else:
        try:
            data = patching.load_json(reg_path)
        except Exception as e:
            raise ComponentError(f"herdr: malformed JSON in {reg_path}: {e}")
        if not isinstance(data, list):
            raise ComponentError("herdr: plugins.json root is not a JSON array")
        mine = [e for e in data
                if isinstance(e, dict) and e.get("plugin_id") == PLUGIN_ID]
        if not mine:
            actions.append({
                "type": "patch_json", "path": reg_path,
                "updates_list_append": {"entry": reg},
                "backup_name": "plugins.json",
                "semantic": _reg_semantic(reg_path, reg, "insert"),
            })
        elif len(mine) == 1 and mine[0].get("manifest_path") == \
                ctx.generated_plugin_manifest:
            notes.append("plugins.json already CEN-owned — no-op")
            claims.append(_reg_semantic(reg_path, reg, "adopt"))
        elif (len(mine) == 1
                and _is_proven_cen_registration(mine[0], ctx)):
            notes.append("plugins.json holds a proven older CEN "
                         "registration — will migrate in place")
            desired = [e for e in data if not (
                isinstance(e, dict) and e.get("plugin_id") == PLUGIN_ID)]
            desired = desired + [reg]
            actions.append({
                "type": "patch_json_document", "path": reg_path,
                "expected_current_data": data,
                "desired_data": desired,
                "backup_name": "plugins.json",
                "semantic": _reg_semantic(reg_path, reg, "migrate"),
            })
        else:
            raise ConflictError(
                "herdr: plugins.json has foreign cen-harness-hud-quota entry; "
                "refusing to overwrite"
            )

    # 3. sidebar agent rows (per-key conflict safety; row_gap untouched)
    cfg_path = ctx.herdr_config_path
    if not os.path.exists(cfg_path):
        text = ""
        created = True
    else:
        with open(cfg_path, "r") as f:
            text = f.read()
        created = False

    try:
        parsed_ok = patching.toml_value(text)  # malformed → fail closed
    except Exception as e:
        raise ComponentError(
            f"herdr: cannot parse {cfg_path} ({e}); fail-closed"
        )

    desired_values = {"agy": AGY_ROWS, "codex": CODEX_ROWS, "pi": PI_ROWS,
                      "opencode": OPENCODE_ROWS}
    node = parsed_ok
    for t in ROWS_TABLE:
        node = node.get(t) if isinstance(node, dict) else None
        if node is None:
            break
    current_rows = node if isinstance(node, dict) else {}

    if created:
        # Fresh file: write ONLY the CEN-owned rows table (additive, valid
        # standalone TOML); recorded as created-file ownership.
        lines = ["[ui.sidebar.agents.rows_by_agent]"]
        for key, value in desired_values.items():
            lines.append(f"{key} = [")
            lines.extend(ROWS_LITERAL[key])
            lines.append("]")
        actions.append({
            "type": "ensure_dir",
            "path": os.path.dirname(cfg_path),
            "mode": 0o755,
        })
        actions.append({
            "type": "write_text_new",
            "path": cfg_path,
            "text": "\n".join(lines) + "\n",
            "mode": 0o644,
            "semantics": [
                _row_semantic(cfg_path, key, value, "insert")
                for key, value in desired_values.items()
            ],
        })

    any_row_action = False
    for key, value in desired_values.items():
        status = patching.classify_toml_key(
            text, ROWS_TABLE, key, value
        )
        if status == "absent":
            if created:
                notes.append("herdr rows created via new config.toml")
                continue
            actions.append({
                "type": "patch_toml_insert",
                "path": cfg_path,
                "table": ROWS_TABLE,
                "key": key,
                "literal_lines": ROWS_LITERAL[key],
                "desired_value": value,
                "backup_name": "herdr-config.toml",
                "semantic": _row_semantic(cfg_path, key, value, "insert"),
            })
            any_row_action = True
        elif status == "equal":
            notes.append(f"herdr rows[{key}] already CEN-owned — no-op")
            claims.append(_row_semantic(cfg_path, key, value, "adopt"))
        elif key == "codex":
            legacy_value = None
            if patching.classify_toml_key(
                    text, ROWS_TABLE, key, CODEX_ROWS_LEGACY_V020) == "equal":
                legacy_value = CODEX_ROWS_LEGACY_V020
            elif patching.classify_toml_key(
                    text, ROWS_TABLE, key, CODEX_ROWS_LEGACY_V010) == "equal":
                legacy_value = CODEX_ROWS_LEGACY_V010
            if legacy_value is None:
                raise ConflictError(
                    "herdr: foreign rows_by_agent.codex present; refusing to "
                    "overwrite"
                )
            notes.append(
                "herdr rows[codex]: exact prior CEN shape detected — "
                "will migrate in place"
            )
            actions.append({
                "type": "patch_toml_replace_owned",
                "path": cfg_path,
                "table": ROWS_TABLE,
                "key": key,
                "expected_current_value": legacy_value,
                "literal_lines": ROWS_LITERAL[key],
                "desired_value": value,
                "backup_name": "herdr-config.toml",
                "semantic": _row_semantic(cfg_path, key, value, "migrate"),
            })
            any_row_action = True
        else:
            raise ConflictError(
                f"herdr: foreign rows_by_agent.{key} present; refusing to "
                "overwrite"
            )

    return {"component": "herdr", "actions": actions, "claims": claims,
            "notes": notes, "noop": False}
