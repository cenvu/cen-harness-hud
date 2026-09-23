"""`cen-hud doctor` — STRICTLY READ-ONLY verification.

Outputs PASS | WARN | FAIL | SKIP per check. Doctor NEVER repairs.
"""

import os

from . import discover, manifest, patching
from .components.agy import desired_command
from .components.claude import desired_command as claude_desired_command
from .components.codex import STATUS_LINE_DESIRED
from .components.herdr import (
    AGY_ROWS,
    CLAUDE_ROWS,
    CLAUDE_ROWS_STATE_ONLY,
    CODEX_ROWS,
    OPENCODE_ROWS,
    PI_ROWS,
)
from .paths import Context


def _check(out, name, status, detail=""):
    out(f"{status:5} {name}" + (f" — {detail}" if detail else ""))
    return status


def _mode(path):
    import stat as st

    return st.S_IMODE(os.stat(path).st_mode)


def run_doctor(ctx: Context, out=print) -> int:
    results = []

    def rec(*args):
        results.append(_check(out, *args))

    # platform / python
    info = discover.platform_info()
    rec("platform", "PASS" if info["platform"] == "darwin" else "WARN",
        f"{info['platform']}/{info['architecture']} python "
        f"{info['python']}")

    # manifest & state root permissions
    if os.path.isfile(ctx.manifest_path):
        m = _mode(ctx.manifest_path)
        rec("manifest-permissions", "PASS" if m == 0o600 else "FAIL",
            f"{format(m, '04o')} (want 0600)")
    else:
        rec("manifest", "SKIP", "not installed")
    if os.path.isdir(ctx.state_root):
        m = _mode(ctx.state_root)
        rec("config-dir-permissions", "PASS" if m == 0o700 else "FAIL",
            f"{format(m, '04o')} (want 0700)")
    if os.path.isdir(ctx.backups_root):
        m = _mode(ctx.backups_root)
        rec("backup-root-permissions", "PASS" if m == 0o700 else "FAIL",
            f"{format(m, '04o')} (want 0700)")

    # installed product root
    installed = os.path.isdir(ctx.install_root)
    if installed:
        rec("product-root", "PASS", ctx.install_root)
    else:
        rec("product-root", "SKIP", "no install root present")

    # command symlinks
    for name in ("cen-hud", "cen-codex-status"):
        link = os.path.join(ctx.bin_dir, name)
        if not os.path.lexists(link):
            rec(f"command:{name}", "SKIP", "absent")
        elif not os.path.islink(link):
            rec(f"command:{name}", "FAIL", "regular file, not a symlink")
        else:
            target = os.readlink(link)
            ok = installed and target.startswith(
                ctx.install_root + os.sep
            )
            rec(f"command:{name}", "PASS" if ok else "FAIL", target)

    # harnesses
    detected = discover.detect_harnesses()
    for name, path in detected.items():
        rec(f"harness:{name}",
            "PASS" if path else "SKIP", path or "not found in PATH")

    # AGY statusline ownership
    p = ctx.agy_settings_path
    if not os.path.exists(p):
        rec("agy:statusline", "SKIP", "settings.json absent")
    else:
        try:
            data = patching.load_json(p)
            cur = (data.get("statusLine") or {}).get("command")
            want = desired_command(ctx)
            if cur is None or cur == "":
                rec("agy:statusline", "SKIP", "unset")
            elif cur == want:
                rec("agy:statusline", "PASS", "CEN-owned")
            else:
                rec("agy:statusline", "FAIL", "foreign command")
        except Exception as e:
            rec("agy:statusline", "FAIL", f"unreadable/malformed: {e}")

    # Claude Code native StatusLine configuration. Exact values are reported
    # neutrally because a matching pre-existing command may be user-owned.
    p = ctx.claude_settings_path
    if not os.path.exists(p):
        rec("claude:statusline", "SKIP", "settings.json absent")
    else:
        try:
            data = patching.load_json(p)
            if not isinstance(data, dict):
                rec("claude:statusline", "FAIL",
                    "settings.json root is not a JSON object")
            elif "statusLine" not in data:
                rec("claude:statusline", "SKIP", "statusLine absent")
            elif not isinstance(data["statusLine"], dict):
                rec("claude:statusline", "FAIL",
                    "statusLine is not a JSON object")
            elif (data["statusLine"].get("type") == "command"
                  and data["statusLine"].get("command")
                  == claude_desired_command(ctx)):
                rec("claude:statusline", "PASS", "expected command")
            else:
                rec("claude:statusline", "FAIL",
                    "foreign or incomplete statusLine")
        except Exception as e:
            rec("claude:statusline", "FAIL", f"unreadable/malformed: {e}")

    # Codex TUI statusline
    p = ctx.codex_config_path
    if not os.path.exists(p):
        rec("codex:tui-status-line", "SKIP", "config.toml absent")
    else:
        try:
            with open(p) as f:
                text = f.read()
            node = patching.toml_value(text).get("tui", {})
            cur = node.get("status_line")
            if cur is None:
                rec("codex:tui-status-line", "SKIP", "unset")
            elif cur == STATUS_LINE_DESIRED:
                rec("codex:tui-status-line", "PASS", "CEN-owned")
            else:
                rec("codex:tui-status-line", "FAIL", "foreign value")
        except Exception as e:
            rec("codex:tui-status-line", "FAIL", f"TOML parse error: {e}")

    # Codex native hook feature + SessionStart telemetry hook
    p = ctx.codex_config_path
    if os.path.exists(p):
        try:
            with open(p) as f:
                parsed = patching.toml_value(f.read())
            hooks_enabled = (parsed.get("features") or {}).get("hooks")
            if hooks_enabled is True:
                rec("codex:hooks-feature", "PASS", "enabled")
            elif hooks_enabled is None:
                rec("codex:hooks-feature", "SKIP", "unset")
            else:
                rec("codex:hooks-feature", "FAIL", "disabled/foreign")
        except Exception as e:
            rec("codex:hooks-feature", "FAIL", f"TOML parse error: {e}")

    p = ctx.codex_hooks_path
    if not os.path.exists(p):
        rec("codex:session-hook", "SKIP", "hooks.json absent")
    else:
        try:
            data = patching.load_json(p)
            hooks = data.get("hooks") if isinstance(data, dict) else None
            entries = hooks.get("SessionStart") if isinstance(hooks, dict) else None
            found = False
            if isinstance(entries, list):
                for entry in entries:
                    nested = entry.get("hooks") if isinstance(entry, dict) else None
                    if not isinstance(nested, list):
                        continue
                    for hook in nested:
                        if (isinstance(hook, dict)
                                and hook.get("type") == "command"
                                and hook.get("command")
                                    == ctx.codex_session_hook_installed):
                            found = True
            rec("codex:session-hook", "PASS" if found else "FAIL",
                "CEN-owned" if found else "missing")
        except Exception as e:
            rec("codex:session-hook", "FAIL", f"malformed: {e}")

    # Pi extension
    link = os.path.join(ctx.pi_ext_dir, "deepseek-balance.ts")
    if not os.path.lexists(link):
        rec("pi:extension", "SKIP", "absent")
    else:
        want = ctx.pi_extension_installed
        good = os.path.islink(link) and os.readlink(link) == want
        rec("pi:extension", "PASS" if good else "FAIL")

    # Herdr plugin registry + rows + socket
    p = ctx.herdr_plugins_path
    if not os.path.exists(p):
        rec("herdr:registry", "SKIP", "plugins.json absent")
    else:
        try:
            entries = patching.load_json(p)
            mine = [e for e in entries
                    if isinstance(e, dict)
                    and e.get("plugin_id") == "cen-harness-hud-quota"]
            if not mine:
                rec("herdr:registry", "SKIP", "CEN plugin absent")
            elif mine[0].get("manifest_path") == \
                    ctx.generated_plugin_manifest and installed:
                rec("herdr:registry", "PASS")
            else:
                rec("herdr:registry", "FAIL", "foreign/missing manifest path")
        except Exception as e:
            rec("herdr:registry", "FAIL", f"malformed: {e}")

    p = ctx.herdr_config_path
    if not os.path.exists(p):
        rec("herdr:rows", "SKIP", "config.toml absent")
    else:
        try:
            with open(p) as f:
                parsed = patching.toml_value(f.read())
            node = parsed
            for t in ("ui", "sidebar", "agents", "rows_by_agent"):
                node = node.get(t) if isinstance(node, dict) else None
            rows = node if isinstance(node, dict) else {}
            for key, want in (("agy", AGY_ROWS), ("codex", CODEX_ROWS),
                              ("pi", PI_ROWS),
                              ("opencode", OPENCODE_ROWS)):
                if key not in rows:
                    rec(f"herdr:rows[{key}]", "SKIP", "absent")
                elif rows[key] == want:
                    rec(f"herdr:rows[{key}]", "PASS", "CEN-owned")
                else:
                    rec(f"herdr:rows[{key}]", "FAIL", "foreign value")
            if "claude" not in rows:
                rec("herdr:rows[claude]", "SKIP", "absent")
            elif rows["claude"] == CLAUDE_ROWS:
                rec("herdr:rows[claude]", "PASS", "expected metadata shape")
            elif rows["claude"] == CLAUDE_ROWS_STATE_ONLY:
                rec("herdr:rows[claude]", "WARN",
                    "compatible state-only shape; metadata rows not rendered")
            else:
                rec("herdr:rows[claude]", "FAIL", "foreign value")
        except Exception as e:
            rec("herdr:rows", "FAIL", f"TOML parse error: {e}")

    sock = os.environ.get("HERDR_SOCKET_PATH") or os.path.join(
        ctx.home, ".config", "herdr", "herdr.sock"
    )
    if not os.path.exists(sock):
        rec("herdr:socket", "SKIP", "no socket (herdr not running?)")
    else:
        import socket

        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.settimeout(1.0)
            s.connect(sock)
            rec("herdr:socket", "PASS", sock)
        except OSError as e:
            rec("herdr:socket", "WARN", f"exists but not connectable: {e}")
        finally:
            s.close()

    # privacy-at-rest invariant
    qroot = ctx.herdr_quota_state_root
    if not os.path.isdir(qroot):
        rec("privacy:state-root", "SKIP", "no runtime state yet")
    else:
        m = _mode(qroot)
        rec("privacy:state-root", "PASS" if m == 0o700 else "FAIL",
            f"{format(m, '04o')} (want 0700)")
        agy_dir = os.path.join(qroot, "agy")
        if os.path.isdir(agy_dir):
            m = _mode(agy_dir)
            rec("privacy:agy-leaf", "PASS" if m == 0o700 else "FAIL",
                f"{format(m, '04o')}")
            for sidecar in ("agy-identity.json", "agy-quota.json"):
                sp = os.path.join(agy_dir, sidecar)
                if os.path.exists(sp):
                    sm = _mode(sp)
                    rec(f"privacy:{sidecar}",
                        "PASS" if sm == 0o600 else "FAIL",
                        f"{format(sm, '04o')}")
        prof = os.path.join(qroot, "profiles")
        if os.path.isdir(prof):
            m = _mode(prof)
            rec("privacy:profiles-leaf", "PASS" if m == 0o700 else "FAIL",
                f"{format(m, '04o')}")
            for fn in os.listdir(prof):
                fp = os.path.join(prof, fn)
                if os.path.isfile(fp):
                    fm = _mode(fp)
                    if fm != 0o600:
                        rec(f"privacy:mapping:{fn}", "FAIL",
                            f"{format(fm, '04o')}")
            else:
                pass

    # PATH guidance
    if ctx.bin_dir not in os.environ.get("PATH", "").split(os.pathsep):
        out("WARN PATH   ~/.local/bin is not in PATH. Add to your shell rc:\n"
            '            export PATH="$HOME/.local/bin:$PATH"')

    fails = sum(1 for r in results if r.startswith("FAIL"))
    warns = sum(1 for r in results if r.startswith("WARN"))
    print()
    print(f"doctor summary: {len(results)} checks — "
          f"{fails} FAIL, {warns} WARN")
    return 1 if fails else 0
