"""Install manifest: schema v1, atomic writes, mode 0600, minimized facts.

Stored ONLY ownership/rollback facts. Never stored: email, plan, quota,
balance, API key, key fingerprint, CODEX account/profiles, hostname,
pane id, Cockpit id.
"""

import json
import os
import time

from . import paths

SCHEMA_VERSION = 1


def exists(ctx: paths.Context) -> bool:
    return os.path.isfile(ctx.manifest_path)


def load(ctx: paths.Context):
    with open(ctx.manifest_path, "r") as f:
        return json.load(f)


def build(ctx: paths.Context, install_id: str, components: list,
          skipped: dict, journal_records: list, patched_files: list) -> dict:
 # fix 5: platform/architecture derive from actual runtime truth.
    from . import discover

    info = discover.platform_info()
    return {
        "schema_version": SCHEMA_VERSION,
        "hud_version": ctx.version,
        "platform": info["platform"],
        "architecture": info["architecture"],
        "install_id": install_id,
        "install_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "installed_components": sorted(components),
        "skipped_components": skipped,
        "install_root": paths.home_rel(ctx.install_root, ctx),
        "created_dirs": [
            paths.home_rel(r["path"], ctx)
            for r in journal_records
            if r["type"] == "dir"
            and not r["path"].startswith(ctx.backups_root + os.sep)
        ],
        "created_files": [
            {
                "path": paths.home_rel(r["path"], ctx),
                "sha256": _sha(r["path"]),
            }
            for r in journal_records
            if r["type"] == "file"
        ],
        "created_symlinks": [
            {
                "link": paths.home_rel(r["path"], ctx),
                "target": paths.home_rel(r["target"], ctx),
            }
            for r in journal_records
            if r["type"] == "symlink"
        ],
        "patched_files": patched_files,
        "backup_root": paths.home_rel(
            os.path.join(ctx.backups_root, install_id), ctx
        ),
    }


def save_atomic_0600(ctx: paths.Context, manifest: dict) -> None:
    data = (json.dumps(manifest, indent=2) + "\n").encode()
    paths.ensure_dir(ctx.state_root, 0o700)
    paths.atomic_write(ctx.manifest_path, data, 0o600)


def _sha(path: str) -> str:
    try:
        return paths.sha256_file(path)
    except OSError:
        return ""


def new_install_id() -> str:
    import secrets

    return time.strftime("%Y%m%d%H%M%S", time.localtime()) + "-" + secrets.token_hex(4)
