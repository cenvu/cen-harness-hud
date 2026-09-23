"""Centralized HOME/path resolution and filesystem primitives.

Every mutation target in the installer is derived from Ctx.home, which is
taken from the process environment (HOME). No installer code calls
os.path.expanduser directly; this makes the FAKE_HOME guarantee provable.
"""

import hashlib
import os
import stat
import tempfile


class ConflictError(Exception):
    """A foreign customization blocks a planned mutation."""


class ComponentError(Exception):
    """A component cannot proceed safely (fail-closed)."""


class Context:
    def __init__(self, home: str, source_root: str, codex_homes=None):
        self.home = os.path.realpath(home)
        self.source_root = os.path.realpath(source_root)
        self.version = read_version(self.source_root)
        self.codex_extra_homes = normalize_codex_homes(
            self.home, codex_homes
        )
        self.share_root = os.path.join(
            self.home, ".local", "share", "cen-harness-hud"
        )
        self.install_root = os.path.join(self.share_root, self.version)
        self.bin_dir = os.path.join(self.home, ".local", "bin")
        self.state_root = os.path.join(self.home, ".config", "cen-harness-hud")
        self.manifest_path = os.path.join(self.state_root, "install-manifest.json")
        self.backups_root = os.path.join(self.state_root, "backups")

    # ── HOME-derived targets (all explicit, never expanduser) ────────────────
    @property
    def agy_settings_path(self) -> str:
        return os.path.join(self.home, ".gemini", "antigravity-cli", "settings.json")

    @property
    def claude_settings_path(self) -> str:
        return os.path.join(self.home, ".claude", "settings.json")

    @property
    def codex_config_path(self) -> str:
        return os.path.join(self.home, ".codex", "config.toml")

    @property
    def codex_hooks_path(self) -> str:
        return os.path.join(self.home, ".codex", "hooks.json")

    @property
    def pi_ext_dir(self) -> str:
        return os.path.join(self.home, ".pi", "agent", "extensions")

    @property
    def herdr_config_path(self) -> str:
        return os.path.join(self.home, ".config", "herdr", "config.toml")

    @property
    def herdr_plugins_path(self) -> str:
        return os.path.join(self.home, ".config", "herdr", "plugins.json")

    @property
    def herdr_quota_state_root(self) -> str:
        return os.path.join(
            self.home, ".config", "herdr", "cen-harness-hud-quota"
        )

    # installed product files
    @property
    def agy_status_installed(self) -> str:
        return os.path.join(self.install_root, "integrations", "agy", "status.py")

    @property
    def claude_status_installed(self) -> str:
        return os.path.join(self.install_root, "integrations", "claude", "status.py")

    @property
    def codex_launcher_installed(self) -> str:
        return os.path.join(
            self.install_root, "integrations", "codex", "launcher.py"
        )

    @property
    def codex_status_installed(self) -> str:
        return os.path.join(self.install_root, "integrations", "codex", "status.py")

    @property
    def codex_session_hook_installed(self) -> str:
        return os.path.join(
            self.install_root, "integrations", "codex", "session_hook.py"
        )

    @property
    def pi_extension_installed(self) -> str:
        return os.path.join(
            self.install_root, "integrations", "pi", "deepseek-balance.ts"
        )

    @property
    def scoped_event_installed(self) -> str:
        return os.path.join(
            self.install_root, "integrations", "herdr", "scoped_event.py"
        )

    @property
    def generated_plugin_manifest(self) -> str:
        return os.path.join(self.install_root, "herdr-plugin.toml")


def read_version(source_root: str) -> str:
    with open(os.path.join(source_root, "VERSION"), "r") as f:
        v = f.read().strip()
    if not v:
        raise ComponentError("VERSION file is empty")
    return v


def normalize_codex_homes(home: str, values=None) -> tuple:
    """Validate and canonicalize explicitly supplied extra Codex homes.

    Extra profiles are deliberately explicit and must already exist below the
    active HOME. The default ``~/.codex`` is handled by the normal Codex
    component and is therefore removed from the extra set. No filesystem is
    created or changed here; callers can safely invoke this during preflight.
    """
    root = os.path.realpath(home)
    default = os.path.realpath(os.path.join(root, ".codex"))
    result = []
    seen = set()
    for raw in values or ():
        if not isinstance(raw, str) or not raw.strip():
            raise ComponentError("codex: --codex-home requires a non-empty path")
        value = raw.strip()
        if value == "~":
            candidate = root
        elif value.startswith("~/"):
            candidate = os.path.join(root, value[2:])
        else:
            candidate = os.path.expanduser(value)
        if not os.path.isabs(candidate):
            candidate = os.path.abspath(candidate)
        resolved = os.path.realpath(candidate)
        if resolved == default:
            continue
        if resolved == root:
            raise ComponentError(
                "codex: --codex-home must identify a profile directory "
                "below HOME"
            )
        try:
            inside_home = os.path.commonpath((root, resolved)) == root
        except ValueError:
            inside_home = False
        if not inside_home:
            raise ComponentError(
                "codex: --codex-home must be inside HOME"
            )
        if not os.path.isdir(resolved):
            raise ComponentError(
                "codex: --codex-home must be an existing directory"
            )
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return tuple(result)


# ── Filesystem primitives ─────────────────────────────────────────────────────

def ensure_dir(path: str, mode: int, journal=None, private: bool = True) -> None:
    """Create directory with `mode` and correct a pre-existing broader mode.

    Only the exact directory given is ever chmodded — never parents.
    Mode changes to PRE-EXISTING directories are journaled as
    {"type": "dir_mode", path, original_mode} so rollback can restore them;
    newly created directories are journaled as {"type": "dir", path}.
    """
    created = not os.path.isdir(path)
    if created:
        os.makedirs(path, exist_ok=True)
        current = stat.S_IMODE(os.stat(path).st_mode)
        if current != mode:
            os.chmod(path, mode)
        if journal is not None:
            journal.append({"type": "dir", "path": path})
        return
    current = stat.S_IMODE(os.stat(path).st_mode)
    if current != mode:
        os.chmod(path, mode)
        if journal is not None:
            journal.append({"type": "dir_mode", "path": path,
                            "original_mode": current})


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write(path: str, data: bytes, mode: int) -> None:
    """Atomic write: temp file in same dir at `mode`, fsync-free, replace."""
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".cen-tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        # enforce final mode post-replace too (target may have pre-existed)
        os.chmod(path, mode)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def private_backup(src: str, backup_dir: str, name: str):
    """Copy target content into PRIVATE backup: always 0600 regardless of
    source mode. Returns (backup_path, original_mode, original_sha256).

    The original mode is recorded in the manifest so uninstall can restore
    it; backups themselves are NEVER source-mode-widened.
    """
    original_mode = stat.S_IMODE(os.stat(src).st_mode)
    original_sha = sha256_file(src)
    dest = os.path.join(backup_dir, name)
    with open(src, "rb") as f:
        data = f.read()
    atomic_write(dest, data, 0o600)
    return dest, original_mode, original_sha


def home_rel(path: str, ctx: Context) -> str:
    """Render a path HOME-relative ('~/.config/...') when under ctx.home."""
    rel = os.path.relpath(path, ctx.home)
    if rel.startswith(".."):
        return path
    return "~/" + rel


def expand_rel(recorded: str, ctx: Context) -> str:
    """Expand a manifest '~/' record back to an absolute path."""
    if recorded == "~":
        return ctx.home
    if recorded.startswith("~/"):
        return os.path.join(ctx.home, recorded[2:])
    return recorded
