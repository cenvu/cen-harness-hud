"""Pi component: extension symlink into installed product copy.

- destination absent                          → create symlink
- symlink already pointing at current product → no-op
- foreign file/symlink occupying the slot     → CONFLICT

Pi credentials are NEVER read.
"""

import os

from .. import patching
from ..paths import ConflictError

EXT_NAME = "deepseek-balance.ts"


def plan(ctx) -> dict:
    actions = []
    notes = []
    ext_dir = ctx.pi_ext_dir
    link = os.path.join(ext_dir, EXT_NAME)
    target = ctx.pi_extension_installed

    if not os.path.isdir(ext_dir):
        actions.append({"type": "ensure_dir", "path": ext_dir, "mode": 0o755})

    cls = patching.classify_symlink(link, target, [ctx.share_root])
    if cls == "absent":
        actions.append({"type": "symlink", "path": link, "target": target})
    elif cls == "equal":
        notes.append("pi extension symlink already correct — no-op")
    else:
        raise ConflictError(
            "pi: ~/.pi/agent/extensions/deepseek-balance.ts exists and is "
            "not the CEN product; refusing to overwrite"
        )

    return {"component": "pi", "actions": actions, "notes": notes,
            "noop": not actions}
