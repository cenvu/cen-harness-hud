"""Whole-run install/uninstall transaction semantics.

DISCOVER → VALIDATE ALL → CONFLICT CHECK ALL → PREPARE BACKUPS →
INSTALL PRODUCT FILES → APPLY PATCHES/SYMLINKS → VERIFY → WRITE MANIFEST.

Any failure during apply/verify rolls back ALL mutations from THIS attempt;
no final manifest is ever written for a partial install.
"""

from __future__ import annotations # keep annotations un-evaluated on py3.9

import os
import shutil
import stat

from . import manifest as manifest_mod
from . import patching
from .components import agy as comp_agy
from .components import codex as comp_codex
from .components import herdr as comp_herdr
from .components import pi as comp_pi
from .paths import (
    ConflictError,
    Context,
    ComponentError,
    atomic_write,
    ensure_dir,
    home_rel,
    private_backup,
    sha256_file,
)

COMPONENTS = {
    "agy": comp_agy.plan,
    "codex": comp_codex.plan,
    "pi": comp_pi.plan,
    "herdr": comp_herdr.plan,
}

PRODUCT_DIRS = ["bin", "installer", "integrations", "templates"]
EXECUTABLE_BASENAMES = {"cen-hud"}


# ── Product file installation ────────────────────────────────────────────────

def _product_ignore(dir_, names):
    """Install ONLY what runtime/manager needs: no private evidence,
    no dev-machine references (integration READMEs / dev plugin manifest)."""
    import fnmatch

    excluded = set()
    for n in names:
        if n == "__pycache__" or fnmatch.fnmatch(n, "*.pyc"):
            excluded.add(n)
        elif fnmatch.fnmatch(n, "*.md"):
            excluded.add(n) # F-2/F-3 deferred sanitization: keep out of product
        elif dir_.endswith(os.sep + "herdr") and n == "herdr-plugin.toml":
            excluded.add(n) # dev manifest; generated version is written instead
    return excluded


def install_product_files(ctx: Context, journal: list) -> None:
 # ownership of a pre-existing install root is UNPROVEN without a
 # manifest; run_install aborts with INSTALL_ROOT_CONFLICT before reaching
 # this point. We therefore only ever CREATE this directory fresh.
    for d in PRODUCT_DIRS:
        src = os.path.join(ctx.source_root, d)
        dst = os.path.join(ctx.install_root, d)
        shutil.copytree(src, dst, ignore=_product_ignore)
        journal.append({"type": "dir", "path": dst})
    shutil.copy2(os.path.join(ctx.source_root, "VERSION"),
                 os.path.join(ctx.install_root, "VERSION"))
    journal.append({"type": "file", "path": os.path.join(ctx.install_root,
                                                         "VERSION")})
 # normalize modes: dirs 0755, files 0644, CLI entry executable
 # live-acceptance fix: ONLY the directly-exec'd integration
 # entrypoints get +x (cen-codex → launcher.py, cen-codex-status →
 # status.py); every other runtime file stays 0644.
    exec_relpaths = {
        os.path.join("integrations", "codex", "launcher.py"),
        os.path.join("integrations", "codex", "status.py"),
        os.path.join("integrations", "codex", "session_hook.py"),
    }
    for root, dirs, files in os.walk(ctx.install_root):
        for name in dirs:
            os.chmod(os.path.join(root, name), 0o755)
        for name in files:
            p = os.path.join(root, name)
            rel = os.path.relpath(p, ctx.install_root)
            exe = (name in EXECUTABLE_BASENAMES
                   or os.path.basename(root) == "bin"
                   or rel in exec_relpaths)
            os.chmod(p, 0o755 if exe else 0o644)


def plan_manager_symlink(ctx: Context) -> dict | None:
    """classify the cen-hud command slot during the READ-ONLY PLAN
    phase — never discover this conflict during APPLY."""
    import os.path as op

    link = op.join(ctx.bin_dir, "cen-hud")
    target = op.join(ctx.install_root, "bin", "cen-hud")
    cls = patching.classify_symlink(link, target, [ctx.share_root])
    if cls == "absent":
        return {"type": "symlink", "path": link, "target": target}
    if cls == "equal":
        return None # already correct; nothing to do
    if cls == "cen_retargetable":
        return {"type": "symlink", "path": link, "target": target,
                "replace_cen_owned": True}
    raise ConflictError(
        "~/.local/bin/cen-hud exists and is not CEN-owned; refusing to "
        "overwrite")


def _backup_once(path: str, backup_dir: str, originals: dict,
                 journal: list) -> dict:
    """invariant: ONE original baseline backup per foreign target path
    per install attempt, regardless of how many patch actions touch it."""
    rec = originals.get(path)
    if rec is not None:
        return rec
    import hashlib

    tag = hashlib.sha256(path.encode()).hexdigest()[:8]
    name = os.path.basename(path) + "." + tag + ".orig"
    bpath, omode, before_sha = private_backup(path, backup_dir, name)
    rec = {"path": path, "backup_path": bpath, "original_mode": omode,
           "before_sha256": before_sha}
    originals[path] = rec
    journal.append({"type": "patched", **rec})
    return rec


# ── Action application ────────────────────────────────────────────────────────

def _apply_action(ctx, act: dict, journal: list,
                  backup_dir: str, originals: dict) -> None:
    kind = act["type"]

    if kind == "ensure_dir":
        ensure_dir(act["path"], act["mode"], journal)
        return

    if kind in ("write_json_new", "write_text_new"):
        if kind == "write_json_new":
            data = patching.dump_json(act["data"])
        else:
            data = act["text"].encode()
        atomic_write(act["path"], data, act.get("mode", 0o644))
        journal.append({"type": "file", "path": act["path"]})
        return

    if kind == "symlink":
        link, target = act["path"], act["target"]
        ensure_dir(os.path.dirname(link), 0o755, journal)
        if act.get("replace_cen_owned"):
            os.unlink(link)
        elif not os.path.lexists(link):
            pass
        else:
            raise ComponentError(f"refusing existing non-CEN path {link}")
        os.symlink(target, link)
        journal.append({"type": "symlink", "path": link, "target": target})
        return

    if kind == "patch_json":
        path = act["path"]
        rec = _backup_once(path, backup_dir, originals, journal)
        data = patching.load_json(path)
        if "updates" in act:
            data = patching.apply_json_updates(data, act["updates"])
        elif "updates_list_append" in act:
            data = list(data) + [act["updates_list_append"]["entry"]]
        else:
            raise ComponentError(f"unknown json update in {path}")
        atomic_write(path, patching.dump_json(data), rec["original_mode"])
        return

    if kind == "patch_json_document":
        path = act["path"]
        current = patching.load_json(path)
        if current != act["expected_current_data"]:
            raise ComponentError(
                "stale plan: JSON document changed before apply in %s" % path)
        rec = _backup_once(path, backup_dir, originals, journal)
        atomic_write(path, patching.dump_json(act["desired_data"]),
                     rec["original_mode"])
        return

    if kind == "patch_toml_insert":
        path = act["path"]
        rec = _backup_once(path, backup_dir, originals, journal)
        with open(path, "r") as f:
            text = f.read()
        new_text = patching.insert_toml_key(
            text, tuple(act["table"]), act["key"], act["literal_lines"]
        )
        atomic_write(path, new_text.encode(), rec["original_mode"])
        return

    if kind == "patch_toml_scalar_insert":
        path = act["path"]
        rec = _backup_once(path, backup_dir, originals, journal)
        with open(path, "r") as f:
            text = f.read()
        if patching.classify_toml_key(
                text, tuple(act["table"]), act["key"],
                act["desired_value"]) != "absent":
            raise ComponentError(
                "stale plan: scalar key %s is no longer absent in %s"
                % (act["key"], path))
        new_text = patching.insert_toml_scalar(
            text, tuple(act["table"]), act["key"], act["literal"])
        patching.toml_value(new_text)
        atomic_write(path, new_text.encode(), rec["original_mode"])
        return

    if kind == "patch_toml_replace_owned":
        path = act["path"]
 # TOCTOU / stale-plan guard: the current PARSED value must still equal
 # the exact owned value this action was planned against; otherwise the
 # file changed underneath us — abort rather than overwrite.
        with open(path, "r") as f:
            text = f.read()
        if patching.classify_toml_key(
                text, tuple(act["table"]), act["key"],
                act["expected_current_value"]) != "equal":
            raise ComponentError(
                "stale plan: owned key %s no longer holds its expected "
                "value in %s" % (act["key"], path))
        rec = _backup_once(path, backup_dir, originals, journal)
        new_text = patching.replace_toml_key(
            text, tuple(act["table"]), act["key"], act["literal_lines"]
        )
 # post-write parse safety BEFORE committing bytes: reject malformed output
        patching.toml_value(new_text)
        atomic_write(path, new_text.encode(), rec["original_mode"])
        return

    raise ComponentError(f"unknown action type {kind}")


def _verify_action(ctx, act: dict) -> None:
    kind = act["type"]
    if kind == "ensure_dir":
        assert os.path.isdir(act["path"])
        return
    if kind == "write_json_new":
        data = patching.load_json(act["path"])
        assert data == act["data"]
        return
    if kind == "write_text_new":
        with open(act["path"]) as f:
            assert f.read() == act["text"]
        return
    if kind == "symlink":
        assert os.path.islink(act["path"])
        assert os.readlink(act["path"]) == act["target"]
        return
    if kind == "patch_json":
        data = patching.load_json(act["path"])
        if "updates" in act:
            for k, v in act["updates"].items():
                assert data.get(k) == v, f"verify failed for key {k}"
        elif "updates_list_append" in act:
            entry = act["updates_list_append"]["entry"]
            assert any(e.get("plugin_id") == entry["plugin_id"]
                       for e in data if isinstance(e, dict))
        return
    if kind == "patch_json_document":
        data = patching.load_json(act["path"])
        assert data == act["desired_data"], "JSON post-mutation verification failed"
        return
    if kind == "patch_toml_insert":
        with open(act["path"]) as f:
            parsed = patching.toml_value(f.read())
        node = parsed
        for t in act["table"]:
            node = node[t]
        assert node[act["key"]] == act["desired_value"], \
            f"TOML post-mutation verification failed for {act['key']}"
        return
    if kind == "patch_toml_scalar_insert":
        with open(act["path"]) as f:
            parsed = patching.toml_value(f.read())
        node = parsed
        for t in act["table"]:
            node = node[t]
        assert node[act["key"]] == act["desired_value"], \
            f"TOML scalar verification failed for {act['key']}"
        return
    if kind == "patch_toml_replace_owned":
        with open(act["path"]) as f:
            parsed = patching.toml_value(f.read())
        node = parsed
        for t in act["table"]:
            node = node[t]
        assert node[act["key"]] == act["desired_value"], \
            f"TOML post-migration verification failed for {act['key']}"
        return
    raise ComponentError(f"unknown action type {kind}")


def _rollback(ctx, journal: list) -> None:
    """Undo every mutation from this attempt, newest first."""
    for rec in reversed(journal):
        try:
            kind = rec["type"]
            if kind == "patched":
                with open(rec["backup_path"], "rb") as f:
                    data = f.read()
                atomic_write(rec["path"], data, rec["original_mode"])
            elif kind == "symlink":
                if os.path.islink(rec["path"]) and os.readlink(
                    rec["path"]
                ) == rec["target"]:
                    os.unlink(rec["path"])
            elif kind == "file":
                if os.path.lexists(rec["path"]):
                    os.unlink(rec["path"])
            elif kind == "dir":
                if os.path.isdir(rec["path"]) and not os.listdir(rec["path"]):
                    os.rmdir(rec["path"])
            elif kind == "dir_mode":
                if os.path.isdir(rec["path"]):
                    os.chmod(rec["path"], rec["original_mode"])
        except OSError:
            pass


# ── Install ───────────────────────────────────────────────────────────────────

def run_install(ctx: Context, selections: list, out=print) -> int:
    """selections: list of (component_name, explicit_bool)."""
 # fix 5: macOS-only gate — BEFORE any mutation.
    from . import discover

    info = discover.platform_info()
    if info["platform"] != "darwin":
        out("UNSUPPORTED_PLATFORM: cen-harness-hud v" + ctx.version +
            " supports macOS only (detected: " + info["platform"] + ").")
        return 5

 # CP20: existing manifest handling
    if manifest_mod.exists(ctx):
        old = manifest_mod.load(ctx)
        if old.get("hud_version") == ctx.version:
            out("ALREADY_INSTALLED: v" + ctx.version +
                " — no mutation performed. Run `cen-hud doctor` to verify.")
            return 0
        out("UPGRADE_UNSUPPORTED: manifest is v%s; this installer is v%s."
            % (old.get("hud_version"), ctx.version))
        out("Run `cen-hud uninstall` first.")
        return 3

 # fix 2: pre-existing install root ownership is unproven without a
 # matching manifest → conflict, ZERO mutation, never auto-rmtree.
    if os.path.isdir(ctx.install_root):
        out("INSTALL_ROOT_CONFLICT: " + ctx.install_root + " already exists "
            "but no valid CEN manifest claims it. Refusing to touch it. "
            "Inspect/remove it manually (it may be foreign).")
        return 1

    detected = _detect()
    components = []
    skipped = {}
    for name, explicit in selections:
        if name not in COMPONENTS:
            continue
        if detected.get(name):
            components.append(name)
        elif explicit:
            skipped[name] = ("harness binary not found in PATH "
                             "(config installed anyway)")
            components.append(name)
        else:
            skipped[name] = "harness not detected"

 # plan ALL components first — conflict checking happens before ANY mutation
    plans = []
    conflicts = []
    all_planned_actions: list = []
    try:
        manager_action = plan_manager_symlink(ctx)
        if manager_action is not None:
            all_planned_actions.append(manager_action)
    except ConflictError as e:
        conflicts.append(str(e))
    for name in components:
        try:
            plans.append(COMPONENTS[name](ctx))
        except ConflictError as e:
            conflicts.append(str(e))
        except ComponentError as e:
            conflicts.append(str(e))
    if not conflicts:
        for p in plans:
            all_planned_actions.extend(p["actions"])
    if conflicts:
        out("INSTALL_ABORTED — conflicts/failures detected; nothing mutated:")
        for c in conflicts:
            out("  CONFLICT: " + c)
        return 1

    journal: list = []
    originals: dict = {}
    install_id = manifest_mod.new_install_id()

    try:
        ensure_dir(ctx.state_root, 0o700, journal)
        backup_dir = os.path.join(ctx.backups_root, install_id)
        ensure_dir(ctx.backups_root, 0o700, journal)
        ensure_dir(backup_dir, 0o700, journal)

 # CEN runtime state root (CP17) — 0700, legacy children untouched
        ensure_dir(ctx.herdr_quota_state_root, 0o700, journal)

        install_product_files(ctx, journal)

        for act in all_planned_actions:
            _apply_action(ctx, act, journal, backup_dir, originals)
        for act in all_planned_actions:
            _verify_action(ctx, act)

 # fix 1: exactly ONE patched_files record per touched target,
 # before_sha256 = pre-install baseline, after_sha256 = final bytes.
        patched_records = []
        for path in sorted(originals):
            rec = originals[path]
            patched_records.append({
                "path": home_rel(path, ctx),
                "backup": home_rel(rec["backup_path"], ctx),
                "original_mode": format(rec["original_mode"], "04o"),
                "before_sha256": rec["before_sha256"],
                "after_sha256": sha256_file(path),
            })

        man = manifest_mod.build(
            ctx, install_id,
            components=[p["component"] for p in plans],
            skipped=skipped,
            journal_records=[r for r in journal if r["type"] != "patched"],
            patched_files=patched_records,
        )
        manifest_mod.save_atomic_0600(ctx, man)

        for p in plans:
            for n in p.get("notes", []):
                out(f"  note[{p['component']}]: {n}")
        for name, why in skipped.items():
            out(f"  skip[{name}]: {why}")
        out("INSTALLED v" + ctx.version + " (install-id " + install_id + ")")
        _warn_path(ctx, out)
        return 0
    except Exception as e:
        out("INSTALL_FAILED: " + str(e) + " — rolling back this attempt")
        _rollback(ctx, journal)
 # no manifest was written for this attempt → product root is ours
        if os.path.isdir(ctx.install_root) and not manifest_mod.exists(ctx):
            shutil.rmtree(ctx.install_root, ignore_errors=True)
        if os.path.isdir(ctx.share_root) and not os.listdir(ctx.share_root):
            os.rmdir(ctx.share_root)
        out("ROLLBACK_COMPLETE — system left as before this attempt")
        return 1


def _detect() -> dict:
    from . import discover

    return discover.detect_harnesses()


def _warn_path(ctx, out) -> None:
    if ctx.bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
        out("WARN: ~/.local/bin is not in your PATH. Add to your shell rc:\n"
            '  export PATH="$HOME/.local/bin:$PATH"')


# ── Uninstall ─────────────────────────────────────────────────────────────────

def run_uninstall(ctx: Context, out=print) -> int:
    if not manifest_mod.exists(ctx):
        out("NO_MANIFEST: nothing to uninstall.")
        return 0
    man = manifest_mod.load(ctx)
    drift = False

    for pf in man.get("patched_files", []):
        path = paths_expand(pf["path"], ctx)
        backup = paths_expand(pf["backup"], ctx)
        if not os.path.exists(path):
            out(f"  gone: {pf['path']}")
            continue
        cur = sha256_file(path)
        if cur == pf.get("after_sha256"):
            with open(backup, "rb") as f:
                data = f.read()
            atomic_write(path, data, int(pf["original_mode"], 8))
            out(f"  restored: {pf['path']}")
        else:
            drift = True
            out(f"  DRIFT (user-edited after install) — NOT overwriting: "
                f"{pf['path']}\n    backup kept at {pf['backup']}")

    for sl in reversed(man.get("created_symlinks", [])):
        link = paths_expand(sl["link"], ctx)
        target = paths_expand(sl["target"], ctx)
        if os.path.islink(link) and os.readlink(link) == target:
            os.unlink(link)
            out(f"  removed link: {sl['link']}")
        elif os.path.lexists(link):
            drift = True
            out(f"  DRIFT link left in place: {sl['link']}")

    for cf in man.get("created_files", []):
        if isinstance(cf, str): # legacy schema
            p, sha = paths_expand(cf, ctx), None
        else:
            p, sha = paths_expand(cf["path"], ctx), cf.get("sha256")
        if os.path.lexists(p):
            if sha and sha256_file(p) != sha:
                drift = True
                out(f"  DRIFT created file edited after install — "
                    f"NOT deleting: {cf['path'] if not isinstance(cf, str) else cf}")
                continue
            os.unlink(p)
            out(f"  removed file: {cf['path'] if not isinstance(cf, str) else cf}")

    for cd in sorted(man.get("created_dirs", []),
                     key=lambda s: -s.count("/")):
        p = paths_expand(cd, ctx)
        if os.path.isdir(p) and not os.listdir(p):
            os.rmdir(p)
            out(f"  removed empty dir: {cd}")

    root = paths_expand(man.get("install_root", ""), ctx)
    if root.startswith(ctx.share_root) and os.path.isdir(root):
        shutil.rmtree(root)
        out(f"  removed product root: {man['install_root']}")
    if os.path.isdir(ctx.share_root) and not os.listdir(ctx.share_root):
        os.rmdir(ctx.share_root)

    if drift:
 # Retire the manifest: drift evidence must survive, but the ACTIVE
 # manifest path must no longer falsely claim the product is installed.
        archive_dir = os.path.join(ctx.state_root, "drift-archive")
        install_id = man.get("install_id") or "undated"
        dest_dir = os.path.join(archive_dir, install_id)
        suffix = 0
        while os.path.lexists(
                os.path.join(dest_dir, "install-manifest.json")):
            suffix += 1
            dest_dir = os.path.join(archive_dir,
                                    "%s.%d" % (install_id, suffix))
        ensure_dir(dest_dir, 0o700)
        os.replace(ctx.manifest_path,
                   os.path.join(dest_dir, "install-manifest.json"))
        os.chmod(os.path.join(dest_dir, "install-manifest.json"), 0o600)
        os.chmod(dest_dir, 0o700)
        os.chmod(archive_dir, 0o700)
        out("UNINSTALL_COMPLETE_WITH_DRIFT — user-edited files preserved; "
            "drift evidence archived (manifest retired) under "
            "~/.config/cen-harness-hud/drift-archive/%s/ ; backups retained "
            "under ~/.config/cen-harness-hud/backups/"
            % os.path.basename(dest_dir))
        return 0

    if os.path.isfile(ctx.manifest_path):
        os.unlink(ctx.manifest_path)
 # remove ONLY this install's own backups; never touch backup dirs
 # belonging to prior drift archives (archived manifests reference them)
    install_id = man.get("install_id")
    if install_id:
        current_backup_dir = os.path.join(ctx.backups_root, install_id)
        if os.path.isdir(current_backup_dir):
            shutil.rmtree(current_backup_dir)
            out(f"  removed backups: ~/.config/cen-harness-hud/"
                f"backups/{install_id}/")
    if os.path.isdir(ctx.backups_root) and not os.listdir(ctx.backups_root):
        os.rmdir(ctx.backups_root)
    if os.path.isdir(ctx.state_root) and not os.listdir(ctx.state_root):
        os.rmdir(ctx.state_root)
    out("UNINSTALL_COMPLETE — personal runtime state under "
        "~/.config/herdr/cen-harness-hud-quota/ was NOT deleted; remove "
        "manually if desired.")
    return 0


def paths_expand(recorded: str, ctx: Context) -> str:
    from .paths import expand_rel

    return expand_rel(recorded, ctx)
