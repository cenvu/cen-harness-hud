"""Codex component: [tui].status_line TOML patch + command symlinks.

TOML conflict policy (CP10/CP16):
- status_line absent in parsed doc        → insert into [tui] (section
  created at EOF when absent), preserving every existing line/comment
- exactly the CEN desired list            → adopt / no-op
- different/custom                        → CONFLICT, never overwrite

Malformed TOML → FAIL CLOSED. auth.json is NEVER read.
"""

import os

from .. import patching
from ..paths import ComponentError, ConflictError

STATUS_LINE_DESIRED = [
    "model-with-reasoning",
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


def plan(ctx) -> dict:
    actions = []
    notes = []
    cen_roots = [ctx.share_root]

    # 1. config.toml [tui].status_line
    path = ctx.codex_config_path
    if not os.path.exists(path):
        text = ""
        created = True
    else:
        with open(path, "r") as f:
            text = f.read()
        created = False

    try:
        status = patching.classify_toml_key(
            text, ("tui",), "status_line", STATUS_LINE_DESIRED
        )
    except Exception as e:
        raise ComponentError(f"codex: cannot parse {path} ({e}); fail-closed")

    if status == "absent":
        if created:
            actions.append({
                "type": "ensure_dir",
                "path": os.path.dirname(path),
                "mode": 0o755,
            })
            actions.append({
                "type": "write_text_new",
                "path": path,
                "text": "[tui]\nstatus_line = [\n"
                + "\n".join(STATUS_LINE_LITERAL)
                + "\n]\n",
                "mode": 0o644,
            })
        else:
            actions.append({
                "type": "patch_toml_insert",
                "path": path,
                "table": ("tui",),
                "key": "status_line",
                "literal_lines": STATUS_LINE_LITERAL,
                "desired_value": STATUS_LINE_DESIRED,
                "backup_name": "config.toml",
            })
    elif status == "equal":
        if not created:
            notes.append("codex [tui].status_line already CEN-owned — no-op")
    else:
        raise ConflictError(
            "codex: foreign [tui].status_line present; refusing to overwrite"
        )

    # 2. command symlinks into installed product root (never the dev repo)
    for name, target in _command_symlinks(ctx):
        link = os.path.join(ctx.bin_dir, name)
        cls = patching.classify_symlink(link, target, cen_roots)
        if cls == "absent":
            actions.append({"type": "symlink", "path": link, "target": target})
        elif cls == "equal":
            notes.append(f"~/.local/bin/{name} already correct — no-op")
        elif cls == "cen_retargetable":
            actions.append({
                "type": "symlink", "path": link, "target": target,
                "replace_cen_owned": True,
            })
            notes.append(f"~/.local/bin/{name} retargeted to current install root")
        else:
            raise ConflictError(
                f"codex: ~/.local/bin/{name} exists and is not CEN-owned; "
                "refusing to overwrite"
            )

    return {"component": "codex", "actions": actions, "notes": notes,
            "noop": not any(a["type"] != "noop" for a in actions)}
