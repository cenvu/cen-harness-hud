#!/usr/bin/env python3
"""sandbox test suite - stdlib unittest only.

HARD GUARD (CP22): every mutation must land under a disposable FAKE_HOME.
The suite snapshots real-HOME sentinels before/after and FAILS on any
change. No test touches the real HOME. No live-machine installer mutation.
"""

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
import unittest.mock
from installer.paths import ComponentError

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(REPO, "bin", "cen-hud")
REAL_HOME = os.environ["HOME"]
MIN_PATH = "/usr/bin:/bin"

SENTINELS = [
    ".config/cen-harness-hud",
    ".local/share/cen-harness-hud",
    ".local/bin/cen-hud",
    ".local/bin/cen-codex",
    ".local/bin/cen-codex-status",
    ".claude/settings.json",
]

CODEX_DESIRED = [
    "model-with-reasoning",
    "status",
    "context-remaining",
    "five-hour-limit",
    "weekly-limit",
]


def _repo_version():
    with open(os.path.join(REPO, "VERSION")) as f:
        return f.read().strip()


def _snapshot():
    snap = {}
    for rel in SENTINELS:
        p = os.path.join(REAL_HOME, rel)
        if os.path.lexists(p):
            st = os.lstat(p)
            kind = ("link:" + os.readlink(p) if os.path.islink(p)
                    else "dir" if os.path.isdir(p) else "file")
            snap[rel] = (stat.S_IMODE(st.st_mode), st.st_size,
                         st.st_mtime_ns, kind)
    return snap


_BEFORE = {}


def setUpModule():
    global _BEFORE
    _BEFORE = _snapshot()


def tearDownModule():
    after = _snapshot()
    if after != _BEFORE:
        diff = set(after.items()) ^ set(_BEFORE.items())
        raise AssertionError(f"REAL-HOME GUARD BREACH: {diff}")


class Base(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="cen-hud-test-")
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def cli(self, *args):
        env = dict(os.environ)
        env["HOME"] = self.home
        env["PATH"] = MIN_PATH
        return subprocess.run(
            [sys.executable, CLI, *args],
            env=env, capture_output=True, text=True,
        )

    def install(self, *flags):
        return self.cli("install", "--source-root", REPO, *flags)

    def write(self, rel, content, mode=0o644):
        p = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
        os.chmod(p, mode)
        return p

    def read(self, rel):
        with open(os.path.join(self.home, rel)) as f:
            return f.read()

    def read_manifest(self):
        with open(self.manifest_path()) as f:
            return json.load(f)

    def manifest_path(self):
        return os.path.join(
            self.home, ".config/cen-harness-hud/install-manifest.json")

    def assertPrivacy(self):
        self.assertTrue(os.path.isfile(self.manifest_path()))
        raw = open(self.manifest_path()).read()
 # denylist literals are constructed to keep THIS file free of
 # raw personal patterns (the export scanner is fail-closed)
        for bad in (REAL_HOME, "/" + "Users/", "cen" + "vu",
                    "@" + "gmail"):
            self.assertNotIn(bad, raw)
        man = json.loads(raw)
        self.assertEqual(man["schema_version"], 2)
        return man

    def assertModes(self):
        cfg = os.path.join(self.home, ".config/cen-harness-hud")
        self.assertEqual(stat.S_IMODE(os.stat(cfg).st_mode), 0o700)
        self.assertEqual(
            stat.S_IMODE(os.stat(self.manifest_path()).st_mode), 0o600)
        qroot = os.path.join(
            self.home, ".config/herdr/cen-harness-hud-quota")
        self.assertTrue(os.path.isdir(qroot))
        self.assertEqual(stat.S_IMODE(os.stat(qroot).st_mode), 0o700)


class InstallTests(Base):
    def test_fresh_all_components(self):
        r = self.install("--agy", "--claude", "--codex", "--pi",
                         "--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        root = os.path.join(self.home, ".local/share/cen-harness-hud",
                            _repo_version())
        for d in ("bin", "installer", "integrations", "templates"):
            self.assertTrue(os.path.isdir(os.path.join(root, d)), d)
        self.assertTrue(os.path.isfile(os.path.join(root, "VERSION")))
        for forbidden in ("handoffs", "PROJECT_STATE.md",
                          "PROMPT_INDEX.md", ".git", "vendor-reference"):
            self.assertFalse(os.path.exists(os.path.join(root, forbidden)),
                             forbidden)
        for dirpath, _, files in os.walk(root):
            for fn in files:
                if fn.endswith((".py", ".toml", ".tmpl")):
                    with open(os.path.join(dirpath, fn)) as f:
                        self.assertNotIn(REPO, f.read())
        for name in ("cen-hud", "cen-codex-status"):
            link = os.path.join(self.home, ".local/bin", name)
            self.assertTrue(os.path.islink(link), name)
            self.assertTrue(os.path.realpath(link).startswith(
                os.path.realpath(root)))
        self.assertFalse(os.path.lexists(
            os.path.join(self.home, ".local/bin/cen-codex")))
 # regression: directly-exec'd integration entrypoints keep +x
        for rel in ("integrations/codex/status.py",
                    "integrations/codex/session_hook.py"):
            installed = os.path.join(root, rel)
            self.assertTrue(os.access(installed, os.X_OK), rel)
            with open(installed, "rb") as f:
                self.assertEqual(f.read(2), b"#!")
 # non-entrypoint runtime files stay 0644
        self.assertFalse(os.access(
            os.path.join(root, "integrations/herdr/herdr_socket.py"),
            os.X_OK))
        agy = json.loads(self.read(".gemini/antigravity-cli/settings.json"))
        self.assertIn("status.py", agy["statusLine"]["command"])
        self.assertNotIn(REPO, agy["statusLine"]["command"])
        claude = json.loads(self.read(".claude/settings.json"))
        self.assertEqual(claude["statusLine"]["type"], "command")
        self.assertIn("integrations/claude/status.py",
                      claude["statusLine"]["command"])
        self.assertNotIn(REPO, claude["statusLine"]["command"])
        codex = tomllib.loads(self.read(".codex/config.toml"))
        self.assertEqual(codex["tui"]["status_line"], CODEX_DESIRED)
        self.assertTrue(os.path.islink(os.path.join(
            self.home, ".pi/agent/extensions/deepseek-balance.ts")))
        plugins = json.loads(self.read(".config/herdr/plugins.json"))
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["plugin_id"], "cen-harness-hud-quota")
        herdr = tomllib.loads(self.read(".config/herdr/config.toml"))
        rows = herdr["ui"]["sidebar"]["agents"]["rows_by_agent"]
        self.assertEqual(len(rows["agy"]), 4)
        self.assertEqual(len(rows["codex"]), 4)
        self.assertEqual(len(rows["pi"]), 3)
        self.assertEqual(len(rows["opencode"]), 2)
        self.assertEqual(len(rows["claude"]), 4)
 # AGY/Pi rows unchanged; Codex card is compact weekly-only
        self.assertEqual(
            rows["codex"],
            [
                ["workspace", "tab"],
                ["agent", "state_text"],
                [{"token": "$cen_codex_identity", "bold": True}],
                [{"token": "$cen_codex_weekly", "bold": True}],
            ],
        )
        self.assertEqual(
            rows["agy"],
            [
                ["workspace", "tab"],
                ["agent", "state_text"],
                [{"token": "$cen_agy_identity", "bold": True}],
                [{"token": "$cen_agy_quota", "bold": True}],
            ],
        )
        self.assertEqual(
            rows["pi"],
            [
                ["workspace", "tab"],
                ["agent", "state_text"],
                [{"token": "$cen_ds_balance", "fg": "#6fb5b7", "bold": True}],
            ],
        )
 # OpenCode card: PURE native Herdr state, exactly two rows, zero CEN tokens
        self.assertEqual(
            rows["opencode"],
            [
                ["workspace", "tab"],
                ["agent", "state_text"],
            ],
        )
        self.assertEqual(
            rows["claude"],
            [
                ["workspace", "tab"],
                ["agent", "state_text"],
                [{"token": "$cen_claude_model_context", "bold": True}],
                [{"token": "$cen_claude_quota", "bold": True}],
            ],
        )
        man = self.assertPrivacy()
        self.assertModes()
        bdir = os.path.join(self.home,
                            ".config/cen-harness-hud/backups",
                            man["install_id"])
        self.assertEqual(stat.S_IMODE(os.stat(bdir).st_mode), 0o700)
        for fn in os.listdir(bdir):
            self.assertEqual(
                stat.S_IMODE(os.stat(os.path.join(bdir, fn)).st_mode),
                0o600, fn)

    def test_single_component_installs(self):
        for flags, probe in (
            (("--agy",), ".gemini/antigravity-cli/settings.json"),
            (("--claude",), ".claude/settings.json"),
            (("--codex",), ".codex/config.toml"),
            (("--pi",), ".pi/agent/extensions/deepseek-balance.ts"),
            (("--herdr",), ".config/herdr/plugins.json"),
        ):
            with self.subTest(flags=flags):
                base = tempfile.mkdtemp(prefix="cen-hud-test-one-")
                try:
                    self.base, self.home = base, os.path.join(base, "home")
                    os.makedirs(self.home)
                    r = self.install(*flags)
                    self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
                    self.assertTrue(os.path.exists(
                        os.path.join(self.home, probe)))
                finally:
                    shutil.rmtree(base, ignore_errors=True)

    def test_same_version_second_install_no_mutation(self):
        assert self.install("--agy").returncode == 0
        with open(self.manifest_path()) as f:
            before = f.read()
        r = self.install("--agy")
        self.assertEqual(r.returncode, 0)
        self.assertIn("ALREADY_INSTALLED", r.stdout)
        with open(self.manifest_path()) as f:
            self.assertEqual(f.read(), before)

    def test_upgrade_rejected(self):
        assert self.install("--agy").returncode == 0
        man = self.read_manifest()
        man["hud_version"] = "9.9.9"
        with open(self.manifest_path(), "w") as f:
            f.write(json.dumps(man))
        r = self.install("--agy")
        self.assertEqual(r.returncode, 3)
        self.assertIn("UPGRADE_UNSUPPORTED", r.stdout)

    def test_missing_harness_explicit_still_installs_with_note(self):
        r = self.install("--agy")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("harness binary not found", r.stdout)


class LegacyLauncherRetirementTests(Base):
    """A v0.6 manager must retire a v0.5 manifest-owned launcher link."""

    def seed_v050_manifest(self):
        root = os.path.realpath(os.path.join(
            self.home, ".local", "share", "cen-harness-hud", "0.5.0"))
        launcher = os.path.join(root, "integrations", "codex", "launcher.py")
        status = os.path.join(root, "integrations", "codex", "status.py")
        os.makedirs(os.path.dirname(launcher), exist_ok=True)
        for path in (launcher, status):
            with open(path, "w") as f:
                f.write("#!/usr/bin/env python3\n")
            os.chmod(path, 0o755)
        bindir = os.path.join(self.home, ".local", "bin")
        os.makedirs(bindir, exist_ok=True)
        launcher_link = os.path.join(bindir, "cen-codex")
        status_link = os.path.join(bindir, "cen-codex-status")
        os.symlink(launcher, launcher_link)
        os.symlink(status, status_link)
        self.write("unrelated-user-file", "keep\n")
        manifest = {
            "schema_version": 2,
            "hud_version": "0.5.0",
            "install_id": "legacy-v050-fixture",
            "install_root": "~/.local/share/cen-harness-hud/0.5.0",
            "installed_components": ["codex"],
            "skipped_components": {},
            "created_dirs": [],
            "created_files": [],
            "created_symlinks": [
                {"link": "~/.local/bin/cen-codex",
                 "target": "~/.local/share/cen-harness-hud/0.5.0/"
                           "integrations/codex/launcher.py"},
                {"link": "~/.local/bin/cen-codex-status",
                 "target": "~/.local/share/cen-harness-hud/0.5.0/"
                           "integrations/codex/status.py"},
            ],
            "patched_files": [],
            "semantic_patches": [],
            "backup_root": "~/.config/cen-harness-hud/backups/"
                          "legacy-v050-fixture",
        }
        self.write(".config/cen-harness-hud/install-manifest.json",
                   json.dumps(manifest), 0o600)

    def test_v050_manifest_uninstall_removes_retired_launcher(self):
        self.seed_v050_manifest()
        launcher_link = os.path.join(self.home, ".local/bin/cen-codex")
        status_link = os.path.join(self.home, ".local/bin/cen-codex-status")
        self.assertTrue(os.path.islink(launcher_link))
        self.assertTrue(os.path.islink(status_link))

        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("UNINSTALL_COMPLETE", r.stdout)
        self.assertFalse(os.path.lexists(launcher_link))
        self.assertFalse(os.path.lexists(status_link))
        self.assertFalse(os.path.exists(os.path.join(
            self.home, ".local/share/cen-harness-hud/0.5.0")))
        self.assertEqual(self.read("unrelated-user-file"), "keep\n")

        r = self.install("--codex")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.islink(status_link))
        self.assertFalse(os.path.lexists(launcher_link))


class CodexCustomHomeTests(Base):
    """Explicit alternate Codex homes receive telemetry-only ownership."""

    def profile(self, name):
        path = os.path.join(self.home, "profiles", name)
        os.makedirs(path, exist_ok=True)
        return path

    def custom_install(self, *homes):
        args = ["--codex"]
        for home in homes:
            args.extend(("--codex-home", home))
        return self.install(*args)

    def cen_hook_command(self):
        from installer.paths import Context

        return Context(self.home, REPO).codex_session_hook_installed

    def hooks_commands(self, profile):
        with open(os.path.join(profile, "hooks.json")) as f:
            data = json.load(f)
        commands = []
        for entry in (data.get("hooks") or {}).get("SessionStart", []):
            for hook in entry.get("hooks", []) if isinstance(entry, dict) else []:
                if isinstance(hook, dict) and hook.get("type") == "command":
                    commands.append(hook.get("command"))
        return commands

    def config_data(self, profile):
        with open(os.path.join(profile, "config.toml")) as f:
            return tomllib.loads(f.read())

    def custom_records(self, profile):
        prefix = "~/" + os.path.relpath(profile, self.home) + "/"
        return [r for r in self.read_manifest()["semantic_patches"]
                if r["path"].startswith(prefix)]

    def test_custom_home_gets_hooks_only_and_default_footer_remains(self):
        profile = self.profile("alpha")
        original_config = '[tui]\nstatus_line = ["user-row"]\n'
        original_hooks = {
            "hooks": {
                "SessionStart": [{"hooks": [{
                    "type": "command", "command": "user-session"
                }]}],
                "Other": [{"hooks": [{
                    "type": "command", "command": "user-other"
                }]}],
            }
        }
        self.write("profiles/alpha/config.toml", original_config, 0o640)
        self.write("profiles/alpha/hooks.json", json.dumps(original_hooks),
                   0o644)

        r = self.custom_install(profile)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        default = tomllib.loads(self.read(".codex/config.toml"))
        self.assertEqual(default["tui"]["status_line"], CODEX_DESIRED)
        custom = self.config_data(profile)
        self.assertEqual(custom["tui"]["status_line"], ["user-row"])
        self.assertTrue(custom["features"]["hooks"])
        commands = self.hooks_commands(profile)
        self.assertEqual(commands.count(self.cen_hook_command()), 1)
        self.assertIn("user-session", commands)
        with open(os.path.join(profile, "hooks.json")) as f:
            installed_hooks = json.load(f)
        self.assertEqual(
            installed_hooks["hooks"]["Other"][0]["hooks"][0]["command"],
            "user-other",
        )
        self.assertEqual(
            {r["op"] for r in self.custom_records(profile)},
            {"TOML_KEY", "CODEX_SESSION_HOOK"},
        )

        self.assertEqual(self.cli("uninstall").returncode, 0)
        with open(os.path.join(profile, "config.toml")) as f:
            self.assertEqual(f.read(), original_config)
        with open(os.path.join(profile, "hooks.json")) as f:
            self.assertEqual(json.load(f), original_hooks)
        self.assertEqual(stat.S_IMODE(os.stat(
            os.path.join(profile, "config.toml")).st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(os.stat(
            os.path.join(profile, "hooks.json")).st_mode), 0o644)

    def test_multiple_duplicate_and_default_custom_paths_are_deduplicated(self):
        alpha = self.profile("alpha")
        beta = self.profile("beta")
        default = os.path.join(self.home, ".codex")
        r = self.custom_install(alpha, beta, alpha, default, default)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for profile in (alpha, beta):
            self.assertTrue(self.config_data(profile)["features"]["hooks"])
            self.assertEqual(
                self.hooks_commands(profile).count(self.cen_hook_command()), 1
            )
            for name in ("config.toml", "hooks.json"):
                self.assertEqual(stat.S_IMODE(os.stat(
                    os.path.join(profile, name)).st_mode), 0o600)
        for profile in (alpha, beta):
            rel = "~/" + os.path.relpath(profile, self.home) + "/"
            records = [r for r in self.read_manifest()["semantic_patches"]
                       if r["path"].startswith(rel)]
            self.assertEqual(len(records), 2)
        self.assertEqual(self.cli("uninstall").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(alpha, "config.toml")))
        self.assertFalse(os.path.exists(os.path.join(beta, "hooks.json")))

    def test_invalid_custom_home_fails_before_any_mutation(self):
        cases = ("missing", "file", "outside")
        for label in cases:
            with self.subTest(label=label):
                self.home = os.path.join(self.base, label)
                os.makedirs(self.home)
                if label == "missing":
                    target = os.path.join(self.home, "profiles", "gone")
                elif label == "file":
                    target = os.path.join(self.home, "profiles", "file")
                    os.makedirs(os.path.dirname(target))
                    with open(target, "w") as f:
                        f.write("foreign")
                else:
                    target = os.path.join(self.base, "outside")
                    os.makedirs(target, exist_ok=True)
                r = self.custom_install(target)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                self.assertFalse(os.path.exists(self.manifest_path()))
                self.assertFalse(os.path.exists(os.path.join(
                    self.home, ".local/share/cen-harness-hud")))

    def test_custom_home_requires_explicit_codex_component(self):
        profile = self.profile("alpha")
        r = self.cli("install", "--source-root", REPO,
                     "--codex-home", profile)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(self.manifest_path()))

    def test_official_herdr_hook_survives_cen_install_and_uninstall(self):
        profile = self.profile("official")
        original_config = "[features]\nhooks = true\n"
        original_hooks = {
            "hooks": {
                "SessionStart": [{"hooks": [{
                    "type": "command",
                    "command": "herdr-agent-state.sh session",
                    "timeout": 10,
                }]}],
                "Other": [{"hooks": [{
                    "type": "command", "command": "user-other"
                }]}],
            }
        }
        self.write("profiles/official/config.toml", original_config, 0o640)
        self.write("profiles/official/hooks.json", json.dumps(original_hooks),
                   0o644)
        r = self.custom_install(profile)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        commands = self.hooks_commands(profile)
        self.assertIn("herdr-agent-state.sh session", commands)
        self.assertEqual(commands.count(self.cen_hook_command()), 1)
        self.assertEqual(self.cli("uninstall").returncode, 0)
        with open(os.path.join(profile, "hooks.json")) as f:
            self.assertEqual(json.load(f), original_hooks)
        self.assertEqual(stat.S_IMODE(os.stat(
            os.path.join(profile, "hooks.json")).st_mode), 0o644)

    def test_existing_true_feature_and_exact_cen_hook_are_not_duplicated(self):
        profile = self.profile("adopt")
        command = self.cen_hook_command()
        self.write("profiles/adopt/config.toml", "[features]\nhooks = true\n")
        self.write("profiles/adopt/hooks.json", json.dumps({
            "hooks": {"SessionStart": [{"hooks": [{
                "type": "command", "command": command
            }]}]}
        }))
        r = self.custom_install(profile)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.hooks_commands(profile).count(command), 1)
        self.assertEqual([
            r["op"] for r in self.custom_records(profile)
        ], ["CODEX_SESSION_HOOK"])

    def test_malformed_custom_config_or_hooks_fails_closed(self):
        for kind in ("config", "hooks"):
            with self.subTest(kind=kind):
                self.home = os.path.join(self.base, kind)
                os.makedirs(self.home)
                profile = self.profile("broken")
                if kind == "config":
                    path = self.write("profiles/broken/config.toml",
                                      "[features\nhooks = true\n")
                else:
                    self.write("profiles/broken/config.toml",
                               "[features]\nhooks = true\n")
                    path = self.write("profiles/broken/hooks.json", "{oops")
                with open(path) as f:
                    before = f.read()
                r = self.custom_install(profile)
                self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
                with open(path) as f:
                    self.assertEqual(f.read(), before)
                self.assertFalse(os.path.exists(self.manifest_path()))

    def test_foreign_hooks_feature_is_not_overwritten(self):
        profile = self.profile("foreign")
        original = "[features]\nhooks = false\n"
        path = self.write("profiles/foreign/config.toml", original)
        r = self.custom_install(profile)
        self.assertEqual(r.returncode, 1)
        with open(path) as f:
            self.assertEqual(f.read(), original)
        self.assertFalse(os.path.exists(self.manifest_path()))

    def test_custom_user_edits_survive_semantic_uninstall(self):
        profile = self.profile("edited")
        self.write("profiles/edited/config.toml", "[model]\nname = \"keep\"\n")
        self.write("profiles/edited/hooks.json", "{}")
        self.assertEqual(self.custom_install(profile).returncode, 0)
        with open(os.path.join(profile, "config.toml"), "a") as f:
            f.write("\n[user]\nnote = \"keep\"\n")
        with open(os.path.join(profile, "hooks.json")) as f:
            hooks = json.load(f)
        hooks["UserEvent"] = [{"hooks": [{"type": "command",
                                             "command": "keep-me"}]}]
        with open(os.path.join(profile, "hooks.json"), "w") as f:
            json.dump(hooks, f)
        self.assertEqual(self.cli("uninstall").returncode, 0)
        config = self.config_data(profile)
        self.assertEqual(config["user"]["note"], "keep")
        self.assertNotIn("features", config)
        with open(os.path.join(profile, "hooks.json")) as f:
            after = json.load(f)
        self.assertIn("UserEvent", after)
        self.assertNotIn("SessionStart", after.get("hooks", {}))

    def test_conflict_in_one_custom_home_prevents_whole_run(self):
        good = self.profile("good")
        bad = self.profile("bad")
        bad_path = self.write("profiles/bad/config.toml",
                              "[features\nbroken = true\n")
        r = self.custom_install(good, bad)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(os.path.exists(os.path.join(good, "config.toml")))
        self.assertFalse(os.path.exists(os.path.join(good, "hooks.json")))
        self.assertFalse(os.path.exists(self.manifest_path()))
        with open(bad_path) as f:
            self.assertEqual(f.read(), "[features\nbroken = true\n")


class ConflictAndMalformedTests(Base):
    FOREIGN_AGY = '{"statusLine":{"type":"command","command":"starship agy"}}'

    def test_foreign_agy_conflict_no_mutation(self):
        self.write(".gemini/antigravity-cli/settings.json", self.FOREIGN_AGY)
        r = self.install("--agy")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        self.assertEqual(self.read(".gemini/antigravity-cli/settings.json"),
                         self.FOREIGN_AGY)
        self.assertFalse(os.path.exists(
            os.path.join(self.home, ".config/cen-harness-hud")))
        self.assertFalse(os.path.exists(
            os.path.join(self.home, ".local/share/cen-harness-hud")))

    def test_malformed_agy_json_fail_closed(self):
        bad = '{"statusLine": '
        self.write(".gemini/antigravity-cli/settings.json", bad)
        r = self.install("--agy")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.read(".gemini/antigravity-cli/settings.json"),
                         bad)

    def test_foreign_codex_status_line_conflict(self):
        original = '[tui]\nstatus_line = ["my-custom-row"]\n'
        self.write(".codex/config.toml", original)
        r = self.install("--codex")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        self.assertEqual(self.read(".codex/config.toml"), original)

    def test_prior_cen_codex_status_line_migrates(self):
        original = (
            "[tui]\n"
            'status_line = ["model-with-reasoning", "context-remaining", '
            '"five-hour-limit", "weekly-limit"]\n'
        )
        self.write(".codex/config.toml", original)
        r = self.install("--codex")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        parsed = tomllib.loads(self.read(".codex/config.toml"))
        self.assertEqual(parsed["tui"]["status_line"], CODEX_DESIRED)
        self.assertIn("status", parsed["tui"]["status_line"])
        self.assertTrue(parsed["features"]["hooks"])

    def test_codex_toml_preserves_comments_and_unrelated_keys(self):
        original = (
            "# top comment\n"
            'model = "gpt-x"\n'
            "[tui]\n"
            "notify_sound = true # keep me\n"
            "\n"
            "[mcp_servers.x]\n"
            'command = "run"\n'
        )
        self.write(".codex/config.toml", original)
        assert self.install("--codex").returncode == 0
        text = self.read(".codex/config.toml")
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["tui"]["status_line"], CODEX_DESIRED)
        self.assertTrue(parsed["tui"]["notify_sound"])
        self.assertEqual(parsed["model"], "gpt-x")
        self.assertEqual(parsed["mcp_servers"]["x"]["command"], "run")
        self.assertIn("# top comment", text)
        self.assertIn("notify_sound = true # keep me", text)

    def test_codex_multiline_array_and_existing_tui(self):
        original = '[tui]\nother = [\n  "a",\n  "b",\n]\n'
        self.write(".codex/config.toml", original)
        assert self.install("--codex").returncode == 0
        parsed = tomllib.loads(self.read(".codex/config.toml"))
        self.assertEqual(parsed["tui"]["other"], ["a", "b"])
        self.assertEqual(parsed["tui"]["status_line"], CODEX_DESIRED)

    def test_malformed_codex_toml_fail_closed(self):
        bad = "[tui\nbroken =="
        self.write(".codex/config.toml", bad)
        r = self.install("--codex")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.read(".codex/config.toml"), bad)

    def test_unrelated_json_keys_preserved(self):
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"theme": "dark", "nested": {"a": [1, 2]}}')
        assert self.install("--agy").returncode == 0
        agy = json.loads(self.read(".gemini/antigravity-cli/settings.json"))
        self.assertEqual(agy["theme"], "dark")
        self.assertEqual(agy["nested"], {"a": [1, 2]})
        self.assertIn("status.py", agy["statusLine"]["command"])


class ClaudeInstallerTests(Base):
    def settings_path(self):
        return os.path.join(self.home, ".claude", "settings.json")

    def desired(self):
        from installer.components.claude import desired_command
        from installer.paths import Context

        return desired_command(Context(self.home, REPO))

    def settings(self):
        with open(self.settings_path()) as f:
            return json.load(f)

    def semantic_records(self):
        return [r for r in self.read_manifest()["semantic_patches"]
                if r["path"] == "~/.claude/settings.json"]

    def doctor_line(self, stdout):
        for line in stdout.splitlines():
            if "claude:statusline" in line:
                return line.strip()
        return None

    def test_absent_settings_creates_native_statusline_and_nested_records(self):
        r = self.install("--claude")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(
            self.settings(),
            {"statusLine": {"type": "command", "command": self.desired()}},
        )
        records = self.semantic_records()
        self.assertEqual(sorted(r["key"] for r in records),
                         ["statusLine.command", "statusLine.type"])
        for record in records:
            self.assertEqual(record["op"], "JSON_KEY")
            self.assertEqual(record["basis"], "insert")
        self.assertEqual(self.read_manifest()["schema_version"], 2)

    def test_existing_settings_and_statusline_siblings_are_preserved(self):
        original = {
            "theme": "dark",
            "statusLine": {
                "padding": 2,
                "refreshInterval": 10,
                "hideVimModeIndicator": True,
                "futureField": {"keep": True},
            },
        }
        self.write(".claude/settings.json", json.dumps(original))
        self.assertEqual(self.install("--claude").returncode, 0)
        after = self.settings()
        self.assertEqual(after["theme"], "dark")
        self.assertEqual(after["statusLine"]["padding"], 2)
        self.assertEqual(after["statusLine"]["refreshInterval"], 10)
        self.assertTrue(after["statusLine"]["hideVimModeIndicator"])
        self.assertEqual(after["statusLine"]["futureField"], {"keep": True})
        self.assertEqual(after["statusLine"]["type"], "command")
        self.assertEqual(after["statusLine"]["command"], self.desired())

    def test_existing_settings_without_statusline_gets_nested_config(self):
        original = {"theme": "dark", "nested": {"keep": [1, 2]}}
        self.write(".claude/settings.json", json.dumps(original))
        self.assertEqual(self.install("--claude").returncode, 0)
        after = self.settings()
        self.assertEqual(after["theme"], "dark")
        self.assertEqual(after["nested"], {"keep": [1, 2]})
        self.assertEqual(after["statusLine"], {
            "type": "command", "command": self.desired(),
        })

    def test_malformed_or_non_object_root_fails_closed(self):
        for raw in ('{"statusLine": ', '[]', 'null'):
            with self.subTest(raw=raw):
                self.write(".claude/settings.json", raw)
                r = self.install("--claude")
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn("CONFLICT", r.stdout)
                self.assertEqual(self.read(".claude/settings.json"), raw)

    def test_preexisting_type_command_missing_command_owns_command_only(self):
        self.write(".claude/settings.json", json.dumps({
            "statusLine": {"type": "command", "padding": 3},
        }))
        self.assertEqual(self.install("--claude").returncode, 0)
        records = self.semantic_records()
        self.assertEqual([r["key"] for r in records], ["statusLine.command"])
        self.assertEqual(self.settings()["statusLine"]["command"],
                         self.desired())
        self.assertEqual(self.cli("uninstall").returncode, 0)
        self.assertEqual(self.settings(), {
            "statusLine": {"type": "command", "padding": 3},
        })

    def test_preexisting_empty_command_is_restored_on_uninstall(self):
        self.write(".claude/settings.json", json.dumps({
            "statusLine": {"type": "command", "command": ""},
        }))
        self.assertEqual(self.install("--claude").returncode, 0)
        record = self.semantic_records()[0]
        self.assertEqual(record["key"], "statusLine.command")
        self.assertEqual(record["before"], {"state": "value", "value": ""})
        self.assertEqual(self.cli("uninstall").returncode, 0)
        self.assertEqual(self.settings()["statusLine"], {
            "type": "command", "command": "",
        })

    def test_statusline_object_without_type_or_command_owns_both(self):
        self.write(".claude/settings.json", json.dumps({
            "theme": "dark", "statusLine": {"padding": 1},
        }))
        self.assertEqual(self.install("--claude").returncode, 0)
        self.assertEqual(sorted(r["key"] for r in self.semantic_records()),
                         ["statusLine.command", "statusLine.type"])
        self.assertEqual(self.cli("uninstall").returncode, 0)
        self.assertEqual(self.settings(), {
            "theme": "dark", "statusLine": {"padding": 1},
        })

    def test_foreign_type_conflicts_without_mutation(self):
        original = json.dumps({"statusLine": {"type": "url"}})
        self.write(".claude/settings.json", original)
        r = self.install("--claude")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        self.assertEqual(self.read(".claude/settings.json"), original)
        self.assertFalse(os.path.exists(self.manifest_path()))
        self.assertFalse(os.path.exists(
            os.path.join(self.home, ".local/share/cen-harness-hud")))

    def test_foreign_command_conflicts_without_mutation(self):
        original = json.dumps({"statusLine": {"command": "my-status"}})
        self.write(".claude/settings.json", original)
        r = self.install("--claude")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        self.assertEqual(self.read(".claude/settings.json"), original)

    def test_non_object_statusline_conflicts_without_mutation(self):
        for value in ("user-status", [], None, 7, False):
            with self.subTest(value=value):
                raw = json.dumps({"keep": True, "statusLine": value})
                self.write(".claude/settings.json", raw)
                r = self.install("--claude")
                self.assertEqual(r.returncode, 1, r.stdout)
                self.assertIn("CONFLICT", r.stdout)
                self.assertEqual(self.read(".claude/settings.json"), raw)

    def test_exact_preexisting_command_is_compatible_but_not_claimed(self):
        original = {"statusLine": {
            "type": "command", "command": self.desired(), "padding": 4,
        }}
        self.write(".claude/settings.json", json.dumps(original))
        self.assertEqual(self.install("--claude").returncode, 0)
        self.assertEqual(self.semantic_records(), [])
        self.assertEqual(self.cli("uninstall").returncode, 0)
        self.assertEqual(self.settings(), original)

    def test_exact_command_without_type_owns_type_only(self):
        original = {"statusLine": {
            "command": self.desired(), "padding": 4,
        }}
        self.write(".claude/settings.json", json.dumps(original))
        self.assertEqual(self.install("--claude").returncode, 0)
        records = self.semantic_records()
        self.assertEqual([r["key"] for r in records], ["statusLine.type"])
        self.assertEqual(self.cli("uninstall").returncode, 0)
        self.assertEqual(self.settings(), original)

    def test_created_settings_file_is_removed_when_untouched(self):
        self.assertEqual(self.install("--claude").returncode, 0)
        self.assertEqual(self.cli("uninstall").returncode, 0)
        self.assertFalse(os.path.exists(self.settings_path()))
        self.assertFalse(os.path.isdir(os.path.join(self.home, ".claude")))

    def test_created_settings_file_preserves_later_user_content(self):
        self.assertEqual(self.install("--claude").returncode, 0)
        data = self.settings()
        data["theme"] = "dark"
        with open(self.settings_path(), "w") as f:
            json.dump(data, f)
        self.assertEqual(self.cli("uninstall").returncode, 0)
        self.assertEqual(self.settings(), {"theme": "dark"})

    def test_owned_command_drift_is_preserved_and_evidence_retained(self):
        self.assertEqual(self.install("--claude").returncode, 0)
        data = self.settings()
        data["statusLine"]["command"] = "user-status"
        with open(self.settings_path(), "w") as f:
            json.dump(data, f)
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertIn("DRIFT", r.stdout)
        after = self.settings()
        self.assertEqual(after["statusLine"]["command"], "user-status")
        self.assertNotIn("type", after["statusLine"])
        self.assertFalse(os.path.exists(self.manifest_path()))
        self.assertTrue(os.path.isdir(os.path.join(
            self.home, ".config/cen-harness-hud/drift-archive")))

    def test_same_attempt_failure_restores_settings_bytes_and_mode(self):
        import unittest.mock
        from installer import transaction as txn
        from installer.paths import Context, ComponentError

        original = '{"theme":"dark","statusLine":{"padding":2}}\n'
        path = self.write(".claude/settings.json", original, mode=0o640)
        ctx = Context(self.home, REPO)
        with unittest.mock.patch.object(
                txn, "_verify_action", side_effect=ComponentError("induced")):
            rc = txn.run_install(ctx, [("claude", True)], out=lambda *_: None)
        self.assertEqual(rc, 1)
        with open(path) as f:
            self.assertEqual(f.read(), original)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)
        self.assertFalse(os.path.exists(self.manifest_path()))

    def test_whole_run_conflict_prevents_other_component_mutation(self):
        original = json.dumps({"statusLine": {"command": "foreign"}})
        self.write(".claude/settings.json", original)
        r = self.install("--claude", "--agy")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.read(".claude/settings.json"), original)
        self.assertFalse(os.path.exists(
            os.path.join(self.home, ".gemini")))
        self.assertFalse(os.path.exists(self.manifest_path()))

    def test_doctor_absent_exact_foreign_and_malformed(self):
        r = self.cli("doctor")
        self.assertEqual(r.returncode, 0)
        self.assertTrue(self.doctor_line(r.stdout).startswith("SKIP"))

        self.write(".claude/settings.json", json.dumps({
            "statusLine": {"type": "command", "command": self.desired()},
        }))
        r = self.cli("doctor")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("expected command", self.doctor_line(r.stdout))

        self.write(".claude/settings.json", '{"statusLine":{"command":"x"}}')
        r = self.cli("doctor")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(self.doctor_line(r.stdout).startswith("FAIL"))

        bad = '{"statusLine": '
        self.write(".claude/settings.json", bad)
        r = self.cli("doctor")
        self.assertEqual(r.returncode, 1)
        self.assertTrue(self.doctor_line(r.stdout).startswith("FAIL"))

    def test_default_selection_auto_selects_detected_claude_only(self):
        from installer import transaction as txn
        from installer.paths import Context

        ctx = Context(self.home, REPO)
        detected = {"agy": None, "claude": "/synthetic/claude",
                    "codex": None, "pi": None, "herdr": None}
        selections = [(name, False) for name in
                      ("agy", "claude", "codex", "pi", "herdr")]
        with unittest.mock.patch.object(txn, "_detect",
                                        return_value=detected):
            self.assertEqual(txn.run_install(ctx, selections,
                                             out=lambda *_: None), 0)
        self.assertTrue(os.path.exists(self.settings_path()))
        self.assertEqual(txn.run_uninstall(ctx, out=lambda *_: None), 0)

    def test_repeated_install_uninstall_leaves_no_statusline_residue(self):
        for _ in range(2):
            self.assertEqual(self.install("--claude").returncode, 0)
            self.assertEqual(self.cli("uninstall").returncode, 0)
        self.assertFalse(os.path.exists(self.settings_path()))

    def test_installed_statusline_command_runs_normal_and_provider_safe(self):
        self.assertEqual(self.install("--claude").returncode, 0)
        command = shlex.split(self.settings()["statusLine"]["command"])
        status_path = command[1]
        payload = json.dumps({
            "model": {"display_name": "Sonnet"},
            "context_window": {"remaining_percentage": 71},
            "rate_limits": {
                "five_hour": {"used_percentage": 25,
                               "resets_at": 4102444800},
                "seven_day": {"used_percentage": 40,
                               "resets_at": 4102444800},
            },
        })
        env = dict(os.environ)
        env["HOME"] = self.home
        env.pop("HERDR_PANE_ID", None)
        normal = subprocess.run(
            [sys.executable, status_path], input=payload,
            env=env, capture_output=True, text=True)
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.assertIn("Sonnet", normal.stdout)
        self.assertIn("CTX 71%", normal.stdout)
        self.assertIn("5H 75%", normal.stdout)
        self.assertIn("7D 60%", normal.stdout)

        env["ANTHROPIC_BASE_URL"] = "synthetic-provider"
        custom = subprocess.run(
            [sys.executable, status_path], input=payload,
            env=env, capture_output=True, text=True)
        self.assertEqual(custom.returncode, 0, custom.stderr)
        self.assertIn("Sonnet", custom.stdout)
        self.assertIn("CTX 71%", custom.stdout)
        self.assertNotIn("5H", custom.stdout)
        self.assertNotIn("7D", custom.stdout)


class ClaudePermissionTests(Base):
    def claude_dir(self):
        return os.path.join(self.home, ".claude")

    def settings_path(self):
        return os.path.join(self.claude_dir(), "settings.json")

    def mode(self, path):
        return stat.S_IMODE(os.stat(path).st_mode)

    def test_preexisting_claude_dir_modes_are_never_normalized(self):
        for wanted in (0o700, 0o750, 0o755):
            with self.subTest(mode=oct(wanted)):
                os.makedirs(self.claude_dir(), exist_ok=True)
                os.chmod(self.claude_dir(), wanted)
                self.assertEqual(self.install("--claude").returncode, 0)
                self.assertEqual(self.mode(self.claude_dir()), wanted)
                self.assertEqual(self.mode(self.settings_path()), 0o600)
                self.assertEqual(self.cli("uninstall").returncode, 0)
                self.assertTrue(os.path.isdir(self.claude_dir()))
                self.assertEqual(self.mode(self.claude_dir()), wanted)
                self.assertFalse(os.path.exists(self.settings_path()))

    def test_cen_created_claude_dir_and_settings_use_private_modes(self):
        self.assertFalse(os.path.lexists(self.claude_dir()))
        self.assertEqual(self.install("--claude").returncode, 0)
        self.assertEqual(self.mode(self.claude_dir()), 0o700)
        self.assertEqual(self.mode(self.settings_path()), 0o600)
        self.assertEqual(self.cli("uninstall").returncode, 0)
        self.assertFalse(os.path.lexists(self.claude_dir()))

    def test_preexisting_settings_modes_are_preserved(self):
        os.makedirs(self.claude_dir())
        for wanted in (0o600, 0o640, 0o644):
            with self.subTest(mode=oct(wanted)):
                self.write(".claude/settings.json", '{"theme":"dark"}\n',
                           mode=wanted)
                self.assertEqual(self.install("--claude").returncode, 0)
                self.assertEqual(self.mode(self.settings_path()), wanted)
                self.assertEqual(self.cli("uninstall").returncode, 0)
                self.assertEqual(self.mode(self.settings_path()), wanted)

    def test_foreign_non_directory_claude_root_fails_closed(self):
        path = self.write(".claude", "foreign-root", mode=0o640)
        r = self.install("--claude")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        with open(path) as f:
            self.assertEqual(f.read(), "foreign-root")
        self.assertEqual(self.mode(path), 0o640)
        self.assertFalse(os.path.exists(self.manifest_path()))
        self.assertFalse(os.path.exists(self.settings_path()))

    def test_directory_symlink_is_not_chmodded_or_removed(self):
        target = os.path.join(self.base, "claude-target")
        os.makedirs(target)
        os.chmod(target, 0o700)
        os.symlink(target, self.claude_dir())
        self.assertEqual(self.install("--claude").returncode, 0)
        self.assertTrue(os.path.islink(self.claude_dir()))
        self.assertEqual(self.mode(target), 0o700)
        self.assertEqual(self.mode(self.settings_path()), 0o600)
        self.assertEqual(self.cli("uninstall").returncode, 0)
        self.assertTrue(os.path.islink(self.claude_dir()))
        self.assertTrue(os.path.isdir(target))
        self.assertEqual(self.mode(target), 0o700)
        self.assertFalse(os.path.exists(self.settings_path()))

    def test_created_claude_dir_with_user_file_is_preserved(self):
        self.assertEqual(self.install("--claude").returncode, 0)
        user_file = os.path.join(self.claude_dir(), "user-note.txt")
        with open(user_file, "w") as f:
            f.write("synthetic user content\n")
        self.assertEqual(self.cli("uninstall").returncode, 0)
        self.assertTrue(os.path.isfile(user_file))
        self.assertTrue(os.path.isdir(self.claude_dir()))
        self.assertEqual(self.mode(self.claude_dir()), 0o700)
        self.assertFalse(os.path.exists(self.settings_path()))

    def test_created_settings_with_user_json_keeps_private_mode(self):
        self.assertEqual(self.install("--claude").returncode, 0)
        with open(self.settings_path()) as f:
            data = json.load(f)
        data["theme"] = "dark"
        with open(self.settings_path(), "w") as f:
            json.dump(data, f)
        self.assertEqual(self.cli("uninstall").returncode, 0)
        with open(self.settings_path()) as f:
            self.assertEqual(json.load(f), {"theme": "dark"})
        self.assertEqual(self.mode(self.settings_path()), 0o600)

    def test_rollback_preserves_preexisting_claude_dir_mode(self):
        import unittest.mock
        from installer import transaction as txn
        from installer.paths import ComponentError, Context

        os.makedirs(self.claude_dir())
        os.chmod(self.claude_dir(), 0o700)
        ctx = Context(self.home, REPO)
        with unittest.mock.patch.object(
                txn, "_verify_action", side_effect=ComponentError("induced")):
            rc = txn.run_install(ctx, [("claude", True)], out=lambda *_: None)
        self.assertEqual(rc, 1)
        self.assertTrue(os.path.isdir(self.claude_dir()))
        self.assertEqual(self.mode(self.claude_dir()), 0o700)
        self.assertFalse(os.path.exists(self.settings_path()))
        self.assertFalse(os.path.exists(self.manifest_path()))

    def test_rollback_removes_new_claude_dir_and_settings(self):
        import unittest.mock
        from installer import transaction as txn
        from installer.paths import ComponentError, Context

        ctx = Context(self.home, REPO)
        with unittest.mock.patch.object(
                txn, "_verify_action", side_effect=ComponentError("induced")):
            rc = txn.run_install(ctx, [("claude", True)], out=lambda *_: None)
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.lexists(self.claude_dir()))
        self.assertFalse(os.path.exists(self.settings_path()))
        self.assertFalse(os.path.exists(self.manifest_path()))


class HerdrConflictTests(Base):
    ROWS_TABLE = "[ui.sidebar.agents.rows_by_agent]\n"

    def test_foreign_herdr_row_conflict(self):
        self.write(".config/herdr/config.toml",
                   self.ROWS_TABLE + 'agy = [["custom"]]\n')
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        self.assertIn("agy", r.stdout)
        self.assertEqual(self.read(".config/herdr/config.toml"),
                         self.ROWS_TABLE + 'agy = [["custom"]]\n')

    def test_row_gap_never_touched(self):
        self.write(".config/herdr/config.toml",
                   '[ui.sidebar.agents]\nrow_gap = 3\n')
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        parsed = tomllib.loads(self.read(".config/herdr/config.toml"))
        self.assertEqual(parsed["ui"]["sidebar"]["agents"]["row_gap"], 3)
        self.assertIn("row_gap = 3", self.read(".config/herdr/config.toml"))

    def test_foreign_plugins_json_entry_conflict(self):
        self.write(".config/herdr/plugins.json",
                   '[{"plugin_id": "cen-harness-hud-quota", '
                   '"manifest_path": "/foreign/herdr-plugin.toml"}]')
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)

    def test_malformed_plugins_json_fail_closed(self):
        bad = "{oops"
        self.write(".config/herdr/plugins.json", bad)
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.read(".config/herdr/plugins.json"), bad)

    def test_foreign_pi_symlink_conflict(self):
        extdir = os.path.join(self.home, ".pi/agent/extensions")
        os.makedirs(extdir, exist_ok=True)
        dest = os.path.join(extdir, "deepseek-balance.ts")
        with open(dest, "w") as f:
            f.write("// my own extension")
        r = self.install("--pi")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        with open(dest) as f:
            self.assertEqual(f.read(), "// my own extension")


class OpenCodeRowTests(Base):
    """CONFIG_ONLY native OpenCode state card (synthetic only).

    Herdr renders OpenCode lifecycle state natively (discovery audit);
    CEN owns only
    the rows_by_agent.opencode shape. No runtime adapter, no tokens.
    """

    ROWS_TABLE = "[ui.sidebar.agents.rows_by_agent]\n"
    OPENCODE = [
        ["workspace", "tab"],
        ["agent", "state_text"],
    ]
    AGY_TOML = (
        'agy = [\n  ["workspace", "tab"],\n  ["agent", "state_text"],\n'
        '  [\n    { token = "$cen_agy_identity", bold = true }\n  ],\n'
        '  [\n    { token = "$cen_agy_quota", bold = true }\n  ]\n]\n'
    )
    CODEX_TOML = (
        'codex = [\n  ["workspace", "tab"],\n  ["agent", "state_text"],\n'
        '  [\n    { token = "$cen_codex_identity", bold = true }\n  ],\n'
        '  [\n    { token = "$cen_codex_weekly", bold = true }\n  ]\n]\n'
    )
    PI_TOML = (
        'pi = [\n  ["workspace", "tab"],\n  ["agent", "state_text"],\n'
        '  [\n    { token = "$cen_ds_balance", fg = "#6fb5b7", '
        'bold = true }\n  ]\n]\n'
    )

    def _rows(self):
        return tomllib.loads(self.read(
            ".config/herdr/config.toml")
        )["ui"]["sidebar"]["agents"]["rows_by_agent"]

    def _doctor_row_line(self, stdout):
        for line in stdout.splitlines():
            if "herdr:rows[opencode]" in line:
                return line.strip()
        return None

    # A — fresh config
    def test_fresh_config_includes_exact_two_row_opencode(self):
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        parsed = tomllib.loads(self.read(".config/herdr/config.toml"))
        rows = parsed["ui"]["sidebar"]["agents"]["rows_by_agent"]
        self.assertEqual(rows["opencode"], self.OPENCODE)
        self.assertEqual(len(rows["opencode"]), 2)

    def _existing_config_with(self, opencode_toml, mode=0o644):
        content = (
            "# user header comment\n"
            '[ui.sidebar]\ncustom_note = "keep me"\n'
            '[ui.sidebar.agents]\nrow_gap = 2\n'
            + self.ROWS_TABLE
            + self.AGY_TOML
            + self.CODEX_TOML
            + self.PI_TOML
            + "# user inline comment\n"
            + opencode_toml
        )
        return self.write(".config/herdr/config.toml", content, mode)

    # B (+ F, G, H, I) — absent → exact insert; everything else preserved
    def test_absent_opencode_inserted_unrelated_content_preserved(self):
        cfg_path = self._existing_config_with("")
        import hashlib
        with open(cfg_path, "rb") as f:
            before = f.read()
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = self.read(".config/herdr/config.toml")
        parsed = tomllib.loads(text)
        rows = parsed["ui"]["sidebar"]["agents"]["rows_by_agent"]
        self.assertEqual(rows["opencode"], self.OPENCODE)
        # unrelated config preserved byte-for-byte except the inserted block
        after = text.encode()
        self.assertEqual(
            hashlib.sha256(
                after.replace(
                    b'opencode = [\n  ["workspace", "tab"],\n'
                    b'  ["agent", "state_text"]\n]\n', b"").replace(
                    b'claude = [\n  ["workspace", "tab"],\n'
                    b'  ["agent", "state_text"],\n'
                    b'  [\n    { token = "$cen_claude_model_context", bold = true }\n'
                    b'  ],\n'
                    b'  [\n    { token = "$cen_claude_quota", bold = true }\n'
                    b'  ]\n]\n', b"")).hexdigest(),
            hashlib.sha256(before).hexdigest())
 # explicit semantic preservation
        self.assertEqual(parsed["ui"]["sidebar"]["agents"]["row_gap"], 2)
        self.assertEqual(parsed["ui"]["sidebar"]["custom_note"], "keep me")
        self.assertEqual(len(rows["agy"]), 4)
        self.assertEqual(len(rows["codex"]), 4)
        self.assertEqual(len(rows["pi"]), 3)
        self.assertIn("# user header comment", text)
        self.assertIn("# user inline comment", text)

    # C — exact shape → no-op
    def test_exact_opencode_rows_noop(self):
        self._existing_config_with(
            'opencode = [\n  ["workspace", "tab"],\n'
            '  ["agent", "state_text"]\n]\n')
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rows[opencode] already CEN-owned", r.stdout)
        self.assertEqual(self._rows()["opencode"], self.OPENCODE)

    # D — foreign custom value → CONFLICT, bytes unchanged
    def test_foreign_custom_opencode_conflicts(self):
        foreign = 'opencode = [["my", "custom", "rows"]]\n'
        cfg_path = self._existing_config_with(foreign)
        import hashlib
        with open(cfg_path, "rb") as f:
            before = f.read()
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        self.assertIn("opencode", r.stdout)
        with open(cfg_path, "rb") as f:
            self.assertEqual(f.read(), before)

    # E — near-match (third row added) → CONFLICT
    def test_near_match_opencode_conflicts(self):
        near = ('opencode = [\n  ["workspace", "tab"],\n'
                '  ["agent", "state_text"],\n'
                '  [{ token = "$my_own_status", bold = true }]\n]\n')
        cfg_path = self._existing_config_with(near)
        import hashlib
        with open(cfg_path, "rb") as f:
            before = f.read()
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        with open(cfg_path, "rb") as f:
            self.assertEqual(f.read(), before)

    # J/K/L — doctor ownership checks
    def _doctor(self):
        return self.cli("doctor")

    def test_doctor_pass_on_exact_opencode_rows(self):
        self._existing_config_with(
            'opencode = [\n  ["workspace", "tab"],\n'
            '  ["agent", "state_text"]\n]\n')
        r = self._doctor()
        line = self._doctor_row_line(r.stdout)
        self.assertIsNotNone(line, r.stdout)
        self.assertTrue(line.startswith("PASS"), line)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_doctor_skips_absent_opencode_rows(self):
        self._existing_config_with("")
        r = self._doctor()
        line = self._doctor_row_line(r.stdout)
        self.assertIsNotNone(line, r.stdout)
        self.assertTrue(line.startswith("SKIP"), line)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_doctor_fails_foreign_opencode_rows(self):
        self._existing_config_with('opencode = [["foreign"]]\n')
        r = self._doctor()
        line = self._doctor_row_line(r.stdout)
        self.assertIsNotNone(line, r.stdout)
        self.assertTrue(line.startswith("FAIL"), line)
        self.assertEqual(r.returncode, 1)

    # M — uninstall applies semantic inverse (schema v2). Pre-existing
    # exact-CEN rows are adopted as CEN ownership (indistinguishable from
    # inherited CEN state, which is the F-3 class): uninstall removes them
    # rather than resurrecting them, while unrelated user keys/mode survive.
    def test_uninstall_restores_pre_opencode_config_bytes_and_mode(self):
        original = ("[ui.sidebar.agents]\nrow_gap = 3\n"
                    + self.ROWS_TABLE + self.AGY_TOML)
        cfg_path = self.write(".config/herdr/config.toml", original,
                              mode=0o640)
        assert self.install("--herdr").returncode == 0
        self.assertEqual(self._rows()["opencode"], self.OPENCODE)
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("semantic-inversed:", r.stdout)
        rows = tomllib.loads(self.read(
            ".config/herdr/config.toml")
        )["ui"]["sidebar"]["agents"].get("rows_by_agent", {})
        self.assertNotIn("agy", rows)
        self.assertNotIn("opencode", rows)
        text = self.read(".config/herdr/config.toml")
        self.assertIn('row_gap = 3', text)
        self.assertEqual(stat.S_IMODE(os.stat(cfg_path).st_mode), 0o640)

    # N — rollback after induced failure restores bytes and mode
    def test_rollback_after_induced_failure_restores_original(self):
        import unittest.mock
        from installer import transaction as txn
        from installer.paths import Context

        original = ("[ui.sidebar.agents]\nrow_gap = 5\n"
                    + self.ROWS_TABLE + self.PI_TOML)
        cfg_path = self.write(".config/herdr/config.toml", original,
                              mode=0o640)
        fake_ctx = Context(home=self.home, source_root=REPO)
        with unittest.mock.patch.object(
                txn, "_verify_action",
                side_effect=ComponentError("induced")):
            rc = txn.run_install(fake_ctx, [("herdr", True)],
                                 out=lambda *_: None)
        self.assertEqual(rc, 1)
        with open(cfg_path) as f:
            self.assertEqual(f.read(), original)
        self.assertEqual(stat.S_IMODE(os.stat(cfg_path).st_mode), 0o640)
        self.assertFalse(os.path.exists(self.manifest_path()))


class ClaudeRowTests(Base):
    """Herdr-native Claude state plus the StatusLine metadata presentation."""

    ROWS_TABLE = "[ui.sidebar.agents.rows_by_agent]\n"
    CLAUDE_STATE_ONLY = [
        ["workspace", "tab"],
        ["agent", "state_text"],
    ]
    CLAUDE = [
        ["workspace", "tab"],
        ["agent", "state_text"],
        [{"token": "$cen_claude_model_context", "bold": True}],
        [{"token": "$cen_claude_quota", "bold": True}],
    ]
    CLAUDE_STATE_ONLY_TOML = (
        'claude = [\n  ["workspace", "tab"],\n'
        '  ["agent", "state_text"]\n]\n'
    )
    CLAUDE_TOML = (
        'claude = [\n  ["workspace", "tab"],\n'
        '  ["agent", "state_text"],\n'
        '  [\n    { token = "$cen_claude_model_context", bold = true }\n'
        '  ],\n'
        '  [\n    { token = "$cen_claude_quota", bold = true }\n'
        '  ]\n]\n'
    )
    AGY_TOML = (
        'agy = [\n  ["workspace", "tab"],\n'
        '  ["agent", "state_text"],\n'
        '  [{ token = "$cen_agy_identity", bold = true }],\n'
        '  [{ token = "$cen_agy_quota", bold = true }]\n]\n'
    )
    CODEX_TOML = (
        'codex = [\n  ["workspace", "tab"],\n'
        '  ["agent", "state_text"],\n'
        '  [{ token = "$cen_codex_identity", bold = true }],\n'
        '  [{ token = "$cen_codex_weekly", bold = true }]\n]\n'
    )
    PI_TOML = (
        'pi = [\n  ["workspace", "tab"],\n'
        '  ["agent", "state_text"],\n'
        '  [{ token = "$cen_ds_balance", fg = "#6fb5b7", bold = true }]\n]\n'
    )
    OPENCODE_TOML = (
        'opencode = [\n  ["workspace", "tab"],\n'
        '  ["agent", "state_text"]\n]\n'
    )

    def _rows(self):
        return tomllib.loads(self.read(
            ".config/herdr/config.toml")
        )["ui"]["sidebar"]["agents"]["rows_by_agent"]

    def _doctor_row_line(self, stdout):
        for line in stdout.splitlines():
            if "herdr:rows[claude]" in line:
                return line.strip()
        return None

    def _config_with(self, claude_toml=""):
        return self.write(
            ".config/herdr/config.toml",
            "# user header\n"
            '[ui.sidebar]\ncustom_note = "keep me"\n'
            '[ui.sidebar.agents]\nrow_gap = 2\n'
            + self.ROWS_TABLE + claude_toml,
        )

    def test_fresh_config_includes_exact_four_row_claude(self):
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._rows()["claude"], self.CLAUDE)
        self.assertEqual(len(self._rows()["claude"]), 4)
        tokens = {
            entry["token"]
            for row in self._rows()["claude"][2:]
            for entry in row
        }
        self.assertEqual(tokens, {
            "$cen_claude_model_context", "$cen_claude_quota",
        })

    def test_existing_unrelated_rows_and_config_preserved(self):
        self._config_with('custom_agent = [["keep"]]\n')
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        parsed = tomllib.loads(self.read(".config/herdr/config.toml"))
        rows = parsed["ui"]["sidebar"]["agents"]["rows_by_agent"]
        self.assertEqual(rows["custom_agent"], [["keep"]])
        self.assertEqual(parsed["ui"]["sidebar"]["agents"]["row_gap"], 2)
        self.assertEqual(parsed["ui"]["sidebar"]["custom_note"], "keep me")

    def test_exact_preexisting_four_row_claude_compatible_not_claimed(self):
        self._config_with(self.CLAUDE_TOML)
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rows[claude] already compatible — left user-owned",
                      r.stdout)
        self.assertEqual(self._rows()["claude"], self.CLAUDE)
        self.assertEqual(list(self._rows()).count("claude"), 1)
        claims = [record for record in self.read_manifest()["semantic_patches"]
                  if record["key"] == "claude"]
        self.assertEqual(claims, [])
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._rows()["claude"], self.CLAUDE)

    def test_exact_preexisting_two_row_claude_is_compatible_not_migrated(self):
        cfg_path = self._config_with(self.CLAUDE_STATE_ONLY_TOML)
        before_rows = self.CLAUDE_STATE_ONLY
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(
            "rows[claude] compatible state-only shape — left user-owned",
            r.stdout,
        )
        self.assertEqual(self._rows()["claude"], before_rows)
        claims = [record for record in self.read_manifest()["semantic_patches"]
                  if record["key"] == "claude"]
        self.assertEqual(claims, [])
        with open(cfg_path, "rb") as f:
            installed = f.read()
        self.assertIn(self.CLAUDE_STATE_ONLY_TOML.encode(), installed)
        self.assertNotIn(b"$cen_claude_", installed)
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self._rows()["claude"], before_rows)
        print("STATE_ONLY_COMPAT_PRESERVED=YES")

    def test_foreign_claude_rows_conflict_without_mutation(self):
        cfg_path = self._config_with('claude = [["foreign"]]\n')
        with open(cfg_path, "rb") as f:
            before = f.read()
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        self.assertIn("claude", r.stdout)
        with open(cfg_path, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_foreign_claude_near_match_conflicts_without_mutation(self):
        foreign = (
            'claude = [\n  ["workspace", "tab"],\n'
            '  ["agent", "state_text"],\n'
            '  [{ token = "$cen_claude_model_context", bold = true }],\n'
            '  [{ token = "$user_claude_quota", bold = true }]\n]\n'
        )
        cfg_path = self._config_with(foreign)
        with open(cfg_path, "rb") as f:
            before = f.read()
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        with open(cfg_path, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_foreign_claude_conflict_aborts_whole_install(self):
        cfg_path = self._config_with('claude = [["foreign"]]\n')
        with open(cfg_path, "rb") as f:
            before = f.read()
        r = self.install("--agy", "--herdr")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        self.assertFalse(os.path.exists(self.manifest_path()))
        self.assertFalse(os.path.exists(os.path.join(
            self.home, ".local/share/cen-harness-hud")))
        with open(cfg_path, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_doctor_skips_absent_claude_rows(self):
        self._config_with()
        r = self.cli("doctor")
        line = self._doctor_row_line(r.stdout)
        self.assertIsNotNone(line, r.stdout)
        self.assertTrue(line.startswith("SKIP"), line)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_doctor_passes_exact_claude_rows(self):
        self._config_with(self.CLAUDE_TOML)
        r = self.cli("doctor")
        line = self._doctor_row_line(r.stdout)
        self.assertIsNotNone(line, r.stdout)
        self.assertTrue(line.startswith("PASS"), line)
        self.assertIn("expected metadata shape", line)
        self.assertNotIn("CEN-owned", line)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_doctor_warns_on_exact_two_row_state_only_claude(self):
        self._config_with(self.CLAUDE_STATE_ONLY_TOML)
        r = self.cli("doctor")
        line = self._doctor_row_line(r.stdout)
        self.assertIsNotNone(line, r.stdout)
        self.assertTrue(line.startswith("WARN"), line)
        self.assertIn("compatible state-only shape; metadata rows not rendered",
                      line)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_doctor_fails_foreign_claude_rows(self):
        self._config_with('claude = [["foreign"]]\n')
        r = self.cli("doctor")
        line = self._doctor_row_line(r.stdout)
        self.assertIsNotNone(line, r.stdout)
        self.assertTrue(line.startswith("FAIL"), line)
        self.assertEqual(r.returncode, 1)

    def test_clean_uninstall_removes_owned_claude_row(self):
        self._config_with()
        self.assertEqual(self.install("--herdr").returncode, 0)
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = tomllib.loads(self.read(
            ".config/herdr/config.toml"))["ui"]["sidebar"]["agents"]
        self.assertNotIn("claude", rows.get("rows_by_agent", {}))

    def test_uninstall_preserves_unrelated_config(self):
        self._config_with('custom_agent = [["keep"]]\n')
        self.assertEqual(self.install("--herdr").returncode, 0)
        self.assertEqual(self.cli("uninstall").returncode, 0)
        parsed = tomllib.loads(self.read(".config/herdr/config.toml"))
        self.assertEqual(parsed["ui"]["sidebar"]["custom_note"], "keep me")
        self.assertEqual(parsed["ui"]["sidebar"]["agents"]["row_gap"], 2)
        self.assertEqual(
            parsed["ui"]["sidebar"]["agents"]["rows_by_agent"]["custom_agent"],
            [["keep"]],
        )
        self.assertNotIn("claude", parsed["ui"]["sidebar"]["agents"]
                         .get("rows_by_agent", {}))

    def test_manifest_records_claude_as_schema_v2_toml_key(self):
        self.assertEqual(self.install("--herdr").returncode, 0)
        man = self.read_manifest()
        self.assertEqual(man["schema_version"], 2)
        records = [record for record in man["semantic_patches"]
                   if record["key"] == "claude"]
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["op"], "TOML_KEY")
        self.assertEqual(record["table"],
                         ["ui", "sidebar", "agents", "rows_by_agent"])
        self.assertEqual(record["before"], {"state": "absent"})
        self.assertEqual(record["format"], "toml")
        self.assertEqual(record["key"], "claude")
        self.assertEqual(record["basis"], "insert")
        self.assertEqual(record["after"], self.CLAUDE)

    def test_repeated_install_uninstall_leaves_zero_claude_residue(self):
        self._config_with()
        for _ in range(2):
            self.assertEqual(self.install("--herdr").returncode, 0)
            self.assertEqual(self.cli("uninstall").returncode, 0)
        text = self.read(".config/herdr/config.toml")
        rows = tomllib.loads(text).get("ui", {}).get("sidebar", {}).get(
            "agents", {}).get("rows_by_agent", {})
        self.assertNotIn("claude", rows)
        self.assertNotIn("claude =", text)

    def test_existing_agent_rows_remain_unchanged(self):
        content = (self.ROWS_TABLE + self.AGY_TOML + self.CODEX_TOML
                   + self.PI_TOML + self.OPENCODE_TOML)
        self.write(".config/herdr/config.toml", content)
        before = tomllib.loads(content)["ui"]["sidebar"]["agents"]
        before_rows = before["rows_by_agent"]
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self._rows()
        for key in ("agy", "codex", "pi", "opencode"):
            self.assertEqual(rows[key], before_rows[key])
        self.assertEqual(rows["claude"], self.CLAUDE)

    def test_mixed_user_claude_and_inserted_rows_have_independent_ownership(self):
        self._config_with(self.CLAUDE_TOML)
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = self._rows()
        self.assertEqual(rows["claude"], self.CLAUDE)
        for key in ("agy", "codex", "pi", "opencode"):
            self.assertIn(key, rows)
        man = self.read_manifest()
        claude_claims = [record for record in man["semantic_patches"]
                         if record["key"] == "claude"]
        self.assertEqual(claude_claims, [])
        self.assertEqual(self.cli("uninstall").returncode, 0)
        with open(os.path.join(self.home, ".config/herdr/config.toml"), "rb") as f:
            after = tomllib.load(f)
        after_rows = after["ui"]["sidebar"]["agents"]["rows_by_agent"]
        self.assertEqual(after_rows["claude"], self.CLAUDE)
        for key in ("agy", "codex", "pi", "opencode"):
            self.assertNotIn(key, after_rows)
        self.assertEqual(after["ui"]["sidebar"]["custom_note"], "keep me")
        self.assertEqual(after["ui"]["sidebar"]["agents"]["row_gap"], 2)


class TransactionTests(Base):
    def test_mid_install_failure_rolls_back(self):
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"theme": "dark"}')
        codex_original = '# my codex\nmodel = "gpt-x"\n'
        self.write(".codex/config.toml", codex_original, mode=0o640)
        with open(os.path.join(self.home, ".pi"), "w") as f:
            f.write("not a dir")
        r = self.install("--agy", "--codex", "--pi")
        self.assertEqual(r.returncode, 1)
        self.assertIn("ROLLBACK_COMPLETE", r.stdout)
        self.assertEqual(self.read(".gemini/antigravity-cli/settings.json"),
                         '{"theme": "dark"}')
        self.assertEqual(self.read(".codex/config.toml"), codex_original)
        self.assertEqual(
            stat.S_IMODE(os.stat(os.path.join(
                self.home, ".codex/config.toml")).st_mode), 0o640)
        self.assertFalse(os.path.exists(os.path.join(
            self.home, ".local/share/cen-harness-hud")))
        self.assertFalse(os.path.lexists(
            os.path.join(self.home, ".local/bin/cen-hud")))
        self.assertFalse(os.path.exists(self.manifest_path()))

    def test_uninstall_clean_restores_bytes_and_modes(self):
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"keep": true}')
        assert self.install("--agy", "--codex", "--pi").returncode == 0
        sidecar_dir = os.path.join(
            self.home, ".config/herdr/cen-harness-hud-quota/agy")
        os.makedirs(sidecar_dir)
        with open(os.path.join(sidecar_dir, "agy-identity.json"), "w") as f:
            f.write('{"email_local": "u", "plan": "PRO"}')
        os.chmod(sidecar_dir, 0o700)
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("UNINSTALL_COMPLETE", r.stdout)
        self.assertNotIn("DRIFT", r.stdout)
        # schema-v2 restores the USER SEMANTIC baseline (parsed equality);
        # JSON bytes are rewritten through the canonical dump, so byte
        # identity is not the contract — CEN residue absence is.
        self.assertEqual(json.loads(self.read(
            ".gemini/antigravity-cli/settings.json")), {"keep": True})
        for name in ("cen-hud", "cen-codex", "cen-codex-status"):
            self.assertFalse(os.path.lexists(
                os.path.join(self.home, ".local/bin", name)))
        self.assertFalse(os.path.exists(os.path.join(
            self.home, ".local/share/cen-harness-hud")))
        self.assertFalse(os.path.exists(
            os.path.join(self.home, ".config/cen-harness-hud")))
        self.assertTrue(os.path.isfile(
            os.path.join(sidecar_dir, "agy-identity.json")))

    def test_uninstall_after_user_edit_no_clobber(self):
        assert self.install("--agy").returncode == 0
        p = os.path.join(self.home, ".gemini/antigravity-cli/settings.json")
        with open(p) as f:
            edited = json.loads(f.read())
        edited["statusLine"]["command"] = "user-customized"
        with open(p, "w") as f:
            f.write(json.dumps(edited))
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertIn("DRIFT", r.stdout)
 # user edit never clobbered
        edited_file = json.loads(self.read(
            ".gemini/antigravity-cli/settings.json"))
        self.assertEqual(edited_file["statusLine"]["command"],
                         "user-customized")
 # manifest RETIRED (not left active, not deleted): evidence archived
        self.assertFalse(os.path.exists(self.manifest_path()))
        arch_root = os.path.join(self.home,
                                 ".config/cen-harness-hud/drift-archive")
        self.assertTrue(os.path.isdir(arch_root))
        entries = os.listdir(arch_root)
        self.assertEqual(len(entries), 1)
        archived = os.path.join(arch_root, entries[0],
                                "install-manifest.json")
        self.assertTrue(os.path.isfile(archived))


class DoctorAndPathTests(Base):
    def test_doctor_read_only_after_install(self):
        assert self.install().returncode == 0
        env = dict(os.environ)
        env["HOME"] = self.home
        env["PATH"] = MIN_PATH + ":" + os.path.join(
            self.home, ".local/bin")
        before = _tree_digest(self.home)
        r = subprocess.run(
            [sys.executable, CLI, "doctor"],
            env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", r.stdout)
        self.assertEqual(_tree_digest(self.home), before)

    def test_path_warning_when_local_bin_missing_from_path(self):
        assert self.install("--agy").returncode == 0
        r = self.cli("doctor")
        self.assertEqual(r.returncode, 0) # WARN only, no FAILs
        self.assertIn("~/.local/bin is not in PATH", r.stdout)
        self.assertIn('export PATH="$HOME/.local/bin:$PATH"', r.stdout)


def _tree_digest(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for n in sorted(dirnames + filenames):
            p = os.path.join(dirpath, n)
            st = os.lstat(p)
            out.append((os.path.relpath(p, root),
                        oct(stat.S_IMODE(st.st_mode)), st.st_size))
    return out


class HardeningTests(Base):
    """transaction ownership/backup hardening."""

    def _herdr_config_original(self):
        content = "[ui.sidebar.agents]\nrow_gap = 3\n"
        return self.write(".config/herdr/config.toml", content, mode=0o640)

    def test_herdr_multi_patch_one_backup_and_uninstall_byte_identical(self):
        original_path = self._herdr_config_original()
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = tomllib.loads(self.read(
            ".config/herdr/config.toml")
        )["ui"]["sidebar"]["agents"]["rows_by_agent"]
        self.assertEqual(len(rows["agy"]), 4)
        self.assertEqual(len(rows["codex"]), 4)
        self.assertEqual(len(rows["pi"]), 3)
        self.assertEqual(len(rows["opencode"]), 2)
        self.assertEqual(len(rows["claude"]), 4)
        man = self.read_manifest()
        herdr_records = [pf for pf in man["patched_files"]
                         if pf["path"].endswith("herdr/config.toml")]
 # exactly ONE record, one backup file, truthful shas
        self.assertEqual(len(herdr_records), 1)
        rec = herdr_records[0]
        backup_abs = os.path.join(
            self.home, ".config/cen-harness-hud/backups",
            man["install_id"], os.path.basename(rec["backup"]))
        self.assertTrue(os.path.isfile(backup_abs))
        backups = os.listdir(os.path.join(
            self.home, ".config/cen-harness-hud/backups",
            man["install_id"]))
        config_backups = [b for b in backups if b.startswith("config.toml")]
        self.assertEqual(len(config_backups), 1)
        with open(backup_abs) as f:
            self.assertEqual(f.read(),
                             "[ui.sidebar.agents]\nrow_gap = 3\n")
        import hashlib
        with open(original_path, "rb") as f:
            cur = hashlib.sha256(f.read()).hexdigest()
        self.assertNotEqual(cur, rec["before_sha256"])
        self.assertEqual(cur, rec["after_sha256"])
 # uninstall restores ORIGINAL bytes and mode via semantic inverse
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertIn("semantic-inversed:", r.stdout)
        p = os.path.join(self.home, ".config/herdr/config.toml")
        with open(p) as f:
            self.assertEqual(f.read(), "[ui.sidebar.agents]\nrow_gap = 3\n")
        self.assertEqual(
            stat.S_IMODE(os.stat(p).st_mode), 0o640)

    def test_herdr_rollback_after_multiple_row_insertions(self):
 # in-process so we can induce a deterministic failure AFTER all
 # three row insertions have been applied
        import unittest.mock
        from installer import transaction as txn
        from installer.paths import Context

        self._herdr_config_original()
        fake_ctx = Context(home=self.home, source_root=REPO)
        with unittest.mock.patch.object(
                txn, "_verify_action",
                side_effect=ComponentError("induced")):
            rc = txn.run_install(fake_ctx, [("herdr", True)], out=lambda *_: None)
        self.assertEqual(rc, 1)
        p = os.path.join(self.home, ".config/herdr/config.toml")
        with open(p) as f:
            self.assertEqual(f.read(), "[ui.sidebar.agents]\nrow_gap = 3\n")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o640)
 # no manifest claiming success
        self.assertFalse(os.path.exists(self.manifest_path()))

    def test_existing_install_root_conflict_zero_mutation(self):
        share = os.path.join(self.home, ".local/share/cen-harness-hud",
                             _repo_version())
        os.makedirs(share)
        foreign = os.path.join(share, "foreign.txt")
        with open(foreign, "w") as f:
            f.write("do-not-touch")
        r = self.install("--agy")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("INSTALL_ROOT_CONFLICT", r.stdout)
        with open(foreign) as f:
            self.assertEqual(f.read(), "do-not-touch")
        self.assertFalse(os.path.exists(self.manifest_path()))
        self.assertFalse(os.path.exists(
            os.path.join(self.home, ".gemini")))
        self.assertFalse(os.path.exists(
            os.path.join(self.home, ".config/cen-harness-hud")))

    def test_cen_hud_slot_conflict_detected_before_mutation(self):
        bindir = os.path.join(self.home, ".local/bin")
        os.makedirs(bindir)
        slot = os.path.join(bindir, "cen-hud")
        with open(slot, "w") as f:
            f.write("#!/bin/sh\necho foreign\n")
        os.chmod(slot, 0o755)
        r = self.install("--agy")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        self.assertIn("cen-hud", r.stdout)
        with open(slot) as f:
            self.assertEqual(f.read(), "#!/bin/sh\necho foreign\n")
        self.assertFalse(os.path.exists(self.manifest_path()))
        self.assertFalse(os.path.exists(
            os.path.join(self.home, ".local/share/cen-harness-hud")))
        self.assertFalse(os.path.exists(
            os.path.join(self.home, ".config/cen-harness-hud")))
        self.assertFalse(os.path.exists(
            os.path.join(self.home, ".gemini")))

    def test_non_darwin_rejected_before_any_mutation(self):
        import unittest.mock
        from installer import transaction as txn
        from installer import discover
        from installer.paths import Context

        fake_ctx = Context(home=self.home, source_root=REPO)
        with unittest.mock.patch.object(
                discover, "platform_info",
                return_value={"platform": "linux", "architecture": "x86_64",
                              "python": "3.14.7"}):
            rc = txn.run_install(fake_ctx, [("herdr", True)], out=lambda *_: None)
        self.assertEqual(rc, 5)
 # absolutely nothing was created under the fake home
        self.assertEqual(os.listdir(self.home), [])
        self.assertFalse(os.path.exists(self.manifest_path()))

    def test_dir_mode_change_restored_on_rollback(self):
        qroot = os.path.join(
            self.home, ".config/herdr/cen-harness-hud-quota")
        os.makedirs(qroot)
        os.chmod(qroot, 0o755)
        with open(os.path.join(self.home, ".pi"), "w") as f:
            f.write("blocker")
        r = self.install("--agy", "--pi")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(
            stat.S_IMODE(os.stat(qroot).st_mode), 0o755)

    def test_dir_mode_tightened_and_kept_on_success(self):
        qroot = os.path.join(
            self.home, ".config/herdr/cen-harness-hud-quota")
        os.makedirs(qroot)
        os.chmod(qroot, 0o755)
        assert self.install("--agy").returncode == 0
        self.assertEqual(stat.S_IMODE(os.stat(qroot).st_mode), 0o700)


class CodexTelemetryDisplayTests(unittest.TestCase):
    """Synthetic display/cache contract for compact weekly Codex card."""

    NOW = 1_000_000_000.0

    @classmethod
    def setUpClass(cls):
        cls._codex_dir = os.path.join(REPO, "integrations", "codex")
        sys.path.insert(0, cls._codex_dir)
        import publisher as pub
        import telemetry as tel
        cls.pub = pub
        cls.tel = tel

    @classmethod
    def tearDownClass(cls):
        if cls._codex_dir in sys.path:
            sys.path.remove(cls._codex_dir)

    def snapshot(self):
        return self.tel.build_snapshot(
            {"account": {"type": "chatgpt",
                         "email": "demo@example.invalid",
                         "planType": "plus"}},
            {"rateLimitsByLimitId": {"codex": {
                "primary": {"usedPercent": 18,
                            "windowDurationMins": 300,
                            "resetsAt": self.NOW + 3600},
                "secondary": {"usedPercent": 39,
                              "windowDurationMins": 10080,
                              "resetsAt": self.NOW + 4 * 86400 + 7 * 3600},
            }}},
            now_epoch=self.NOW,
        )

    def test_sidebar_is_identity_plus_weekly_only(self):
        tok = self.pub.display_tokens(self.snapshot(), now_epoch=self.NOW)
        self.assertEqual(tok["cen_codex_identity"], "demo · PLUS")
        self.assertEqual(tok["cen_codex_weekly"], "7D 61% · ↻4d7h")
        self.assertIsNone(tok["cen_codex_window_1"])
        self.assertIsNone(tok["cen_codex_window_2"])
        self.assertIsNone(tok["cen_codex_summary"])
        self.assertNotIn("5H", " ".join(v for v in tok.values()
                                         if isinstance(v, str)))

    def test_invalid_weekly_fails_closed(self):
        snap = self.tel.build_snapshot(
            {"account": {"type": "chatgpt",
                         "email": "demo@example.invalid",
                         "planType": "plus"}},
            {"rateLimits": {
                "primary": {"usedPercent": 10, "windowDurationMins": 300},
                "secondary": {"usedPercent": float("nan"),
                              "windowDurationMins": 10080},
            }},
            now_epoch=self.NOW,
        )
        tok = self.pub.display_tokens(snap, now_epoch=self.NOW)
        self.assertEqual(tok["cen_codex_weekly"], "—")

    def test_full_email_never_persisted_or_rendered(self):
        snap = self.snapshot()
        self.assertNotIn("@", json.dumps(snap))
        tok = self.pub.display_tokens(snap, now_epoch=self.NOW)
        self.assertNotIn("@", tok["cen_codex_identity"])

    def test_fail_closed_token_set(self):
        tok = self.pub.fail_closed_tokens()
        self.assertEqual(tok["cen_codex_identity"], "—")
        self.assertEqual(tok["cen_codex_weekly"], "—")
        self.assertIsNone(tok["cen_codex_window_1"])
        self.assertIsNone(tok["cen_codex_window_2"])

class InstallerMigrationTests(Base):
    """Legacy-shape migration + drift-manifest retirement (synthetic only)."""

    CURRENT_CODEX_VALUE = [
        ["workspace", "tab"],
        ["agent", "state_text"],
        [{"token": "$cen_codex_identity", "bold": True}],
        [{"token": "$cen_codex_weekly", "bold": True}],
    ]
    LEGACY_V020_CODEX_VALUE = [
        ["workspace", "tab"],
        ["agent", "state_text"],
        [{"token": "$cen_codex_identity", "bold": True}],
        [{"token": "$cen_codex_window_1", "bold": True}],
        [{"token": "$cen_codex_window_2", "bold": True}],
    ]
    LEGACY_CODEX_VALUE = [
        ["workspace", "tab"],
        ["agent", "state_text"],
        [{"token": "$cen_codex_summary", "bold": True}],
    ]

    def _toml_block(self, key, value):
        def render(v):
            if isinstance(v, list):
                inner = ", ".join(render(x) for x in v)
                return "[" + inner + "]"
            if isinstance(v, dict):
                inner = ", ".join(
                    f"{k} = {render(x)}" for k, x in v.items())
                return "{ " + inner + " }"
            if isinstance(v, bool):
                return "true" if v else "false"
            return '"%s"' % v
        lines = [f"{key} = ["] + \
                ["  " + render(row) + "," for row in value[:-1]] + \
                ["  " + render(value[-1])] + ["]"]
        return "\n".join(lines) + "\n"

    def write_herdr_config(self, codex_toml, mode=0o644):
 # deliberately includes unrelated CEN rows, user settings and comments
        content = (
            "# user header comment\n"
            "[ui.sidebar]\ncustom_note = \"keep me\"\n"
            "[ui.sidebar.agents]\nrow_gap = 2\n"
            '[ui.sidebar.agents.rows_by_agent]\n'
            'agy = [\n  ["workspace", "tab"],\n  ["agent", "state_text"],\n'
            '  [\n    { token = "$cen_agy_identity", bold = true }\n  ],\n'
            '  [\n    { token = "$cen_agy_quota", bold = true }\n  ]\n]\n'
            '# user inline comment\n'
            + codex_toml +
            'pi = [\n  ["workspace", "tab"],\n  ["agent", "state_text"],\n'
            '  [\n    { token = "$cen_ds_balance", fg = "#6fb5b7", '
            'bold = true }\n  ]\n]\n'
        )
        self.write(".config/herdr/config.toml", content, mode)
        return os.path.join(self.home, ".config/herdr/config.toml")

    def legacy_codex_toml(self):
 # EXACT textual shape emitted by the public v0.1.0 product's
 # ROWS_LITERAL["codex"] — nested inner row array on separate lines.
        return (
            'codex = [\n'
            '  ["workspace", "tab"],\n'
            '  ["agent", "state_text"],\n'
            '  [\n'
            '    { token = "$cen_codex_summary", bold = true }\n'
            '  ]\n'
            ']\n'
        )

    def test_exact_legacy_codex_migrates_and_preserves_everything_else(self):
        cfg_path = self.write_herdr_config(self.legacy_codex_toml(),
                                           mode=0o640)
        import hashlib
        with open(cfg_path, "rb") as f:
            original_bytes = f.read()
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        text = self.read(".config/herdr/config.toml")
        parsed = tomllib.loads(text)
        rows = parsed["ui"]["sidebar"]["agents"]["rows_by_agent"]
 # 1. migrated to current shape exactly
        self.assertEqual(rows["codex"], self.CURRENT_CODEX_VALUE)
 # canonical legacy token fully gone; no extra bracket residue
 # (valid TOML proven by tomllib.loads above; exactly one bare "]"
 # closer per top-level row block: agy, codex, pi, opencode, claude)
        self.assertNotIn("$cen_codex_summary", text)
 # exactly one bare "]" closer per top-level row block: agy, codex, pi,
 # and the newly inserted opencode and four-row claude block
        self.assertEqual(
            sum(1 for ln in text.splitlines() if ln.strip() == "]"), 9)
 # 2./3./4. unrelated content byte-preserved
        self.assertEqual(rows["agy"][0], ["workspace", "tab"])
        self.assertEqual(len(rows["agy"]), 4)
        self.assertEqual(rows["pi"], [
            ["workspace", "tab"],
            ["agent", "state_text"],
            [{"token": "$cen_ds_balance",
              "fg": "#6fb5b7", "bold": True}],
        ])
        self.assertEqual(parsed["ui"]["sidebar"]["agents"]["row_gap"], 2)
        self.assertEqual(parsed["ui"]["sidebar"]["custom_note"], "keep me")
        self.assertIn("# user header comment", text)
        self.assertIn("# user inline comment", text)
 # original file mode preserved
        self.assertEqual(stat.S_IMODE(os.stat(
            os.path.join(self.home,
                         ".config/herdr/config.toml")).st_mode), 0o640)
 # 7. ONE truthful backup record
        man = self.read_manifest()
        recs = [pf for pf in man["patched_files"]
                if pf["path"].endswith("herdr/config.toml")]
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["before_sha256"],
                         hashlib.sha256(original_bytes).hexdigest())
        with open(cfg_path, "rb") as f:
            self.assertEqual(recs[0]["after_sha256"],
                             hashlib.sha256(f.read()).hexdigest())
        backup_abs = os.path.join(
            self.home, ".config/cen-harness-hud/backups",
            man["install_id"], os.path.basename(recs[0]["backup"]))
        self.assertTrue(os.path.isfile(backup_abs))
        self.assertEqual(stat.S_IMODE(os.stat(backup_abs).st_mode), 0o600)
        with open(backup_abs, "rb") as f:
            self.assertEqual(f.read(), original_bytes)

    def test_near_match_legacy_conflicts(self):
        near = self._toml_block("codex", [
            ["workspace", "tab"],
            ["agent", "state_text"],
            [{"token": "$cen_codex_summary", "bold": False}],
        ])
        self.write_herdr_config(near)
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        self.assertIn("$cen_codex_summary", self.read(
            ".config/herdr/config.toml"))

    def test_user_custom_codex_conflicts(self):
        custom = self._toml_block("codex", [["my", "custom", "rows"]])
        self.write_herdr_config(custom)
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        self.assertEqual(
            tomllib.loads(self.read(
                ".config/herdr/config.toml")
            )["ui"]["sidebar"]["agents"]["rows_by_agent"]["codex"],
            [["my", "custom", "rows"]])

    def test_migration_failure_rolls_back_to_legacy_bytes_and_mode(self):
        self.write_herdr_config(self.legacy_codex_toml(), mode=0o640)
        import unittest.mock
        from installer import transaction as txn
        from installer.paths import Context

        fake_ctx = Context(home=self.home, source_root=REPO)
        with unittest.mock.patch.object(
                txn, "_verify_action",
                side_effect=ComponentError("induced")):
            rc = txn.run_install(fake_ctx, [("herdr", True)],
                                 out=lambda *_: None)
        self.assertEqual(rc, 1)
        p = os.path.join(self.home, ".config/herdr/config.toml")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o640)
        parsed = tomllib.loads(open(p).read())
        self.assertEqual(
            parsed["ui"]["sidebar"]["agents"]["rows_by_agent"]["codex"],
            self.LEGACY_CODEX_VALUE)
        self.assertFalse(os.path.exists(self.manifest_path()))

    def test_v020_five_row_shape_migrates_to_weekly_only(self):
        self.write_herdr_config(self._toml_block(
            "codex", self.LEGACY_V020_CODEX_VALUE))
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = tomllib.loads(self.read(
            ".config/herdr/config.toml")
        )["ui"]["sidebar"]["agents"]["rows_by_agent"]
        self.assertEqual(rows["codex"], self.CURRENT_CODEX_VALUE)
        self.assertNotIn("$cen_codex_window_1",
                         self.read(".config/herdr/config.toml"))

    def test_current_shape_is_noop(self):
        self.write_herdr_config(self._toml_block(
            "codex", self.CURRENT_CODEX_VALUE))
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("rows[codex] already CEN-owned", r.stdout)
        self.assertEqual(
            tomllib.loads(self.read(
                ".config/herdr/config.toml")
            )["ui"]["sidebar"]["agents"]["rows_by_agent"]["codex"],
            self.CURRENT_CODEX_VALUE)

    def test_absent_codex_inserts_normally(self):
        content = ('[ui.sidebar.agents]\nrow_gap = 4\n'
                   '[ui.sidebar.agents.rows_by_agent]\n')
        self.write(".config/herdr/config.toml", content)
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        parsed = tomllib.loads(self.read(".config/herdr/config.toml"))
        rows = parsed["ui"]["sidebar"]["agents"]["rows_by_agent"]
        self.assertEqual(rows["codex"], self.CURRENT_CODEX_VALUE)
        self.assertEqual(parsed["ui"]["sidebar"]["agents"]["row_gap"], 4)


class DriftRetirementTests(Base):
    """Uninstall-with-drift retires the active manifest (synthetic only)."""

    def _setup_with_drift(self):
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"theme": "dark"}')
        assert self.install("--agy").returncode == 0
 # personal runtime quota state marker must survive everything
        qroot = os.path.join(
            self.home, ".config/herdr/cen-harness-hud-quota/profiles")
        os.makedirs(qroot, exist_ok=True)
        with open(os.path.join(qroot, "marker.json"), "w") as f:
            f.write("{}\n")
 # benign user edit (does NOT conflict with CEN ownership)
        p = os.path.join(self.home, ".gemini/antigravity-cli/settings.json")
        edited = json.loads(open(p).read())
        edited["theme"] = "ocean"
        open(p, "w").write(json.dumps(edited))
        return p

    def test_drift_uninstall_retires_manifest_keeps_evidence(self):
        # Schema v2: an UNRELATED user edit no longer forces manifest
        # retirement. The owned surface still inverts cleanly, the user edit
        # survives, and uninstall completes without drift.
        drifted_path = self._setup_with_drift()
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertIn("semantic-inversed:", r.stdout)
        self.assertIn("UNINSTALL_COMPLETE", r.stdout)
        self.assertNotIn("WITH_DRIFT", r.stdout)
 # unrelated user edit untouched; CEN-owned surface removed
        after = json.loads(open(drifted_path).read())
        self.assertEqual(after["theme"], "ocean")
        self.assertNotIn("statusLine", after)
 # CLEAN uninstall removes the active manifest (nothing to retire)
        self.assertFalse(os.path.exists(self.manifest_path()))
        arch_root = os.path.join(self.home,
                                 ".config/cen-harness-hud/drift-archive")
        self.assertFalse(os.path.exists(arch_root))
 # personal runtime quota state untouched
        self.assertTrue(os.path.isfile(os.path.join(
            self.home,
            ".config/herdr/cen-harness-hud-quota/profiles",
            "marker.json")))

    def test_reinstall_after_drift_retirement_proceeds_when_no_conflict(self):
        self._setup_with_drift()
        assert self.cli("uninstall").returncode == 0
        r = self.install("--agy")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("INSTALLED", r.stdout)
 # benign user edit survived the whole cycle
        edited = json.loads(self.read(
            ".gemini/antigravity-cli/settings.json"))
        self.assertEqual(edited["theme"], "ocean")
        self.assertIn("status.py", edited["statusLine"]["command"])

    def test_reinstall_after_drift_still_conflicts_on_real_conflict(self):
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"theme": "dark"}')
        assert self.install("--agy").returncode == 0
        p = os.path.join(self.home, ".gemini/antigravity-cli/settings.json")
        edited = json.loads(open(p).read())
        edited["statusLine"]["command"] = "user-customized"
        open(p, "w").write(json.dumps(edited))
        assert self.cli("uninstall").returncode == 0
        self.assertFalse(os.path.exists(self.manifest_path()))
        r = self.install("--agy")
        self.assertEqual(r.returncode, 1)
        self.assertIn("CONFLICT", r.stdout)
        self.assertEqual(json.loads(open(p).read())["statusLine"]["command"],
                         "user-customized")

    def test_clean_uninstall_semantics_unchanged(self):
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"keep": true}')
        assert self.install("--agy").returncode == 0
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertIn("UNINSTALL_COMPLETE", r.stdout)
        self.assertNotIn("WITH_DRIFT", r.stdout)
        self.assertFalse(os.path.exists(self.manifest_path()))
        self.assertFalse(os.path.isdir(os.path.join(
            self.home, ".config/cen-harness-hud/backups")))

    def test_fake_version_transition_remains_uninstall_first(self):
 # old-manifest version differs from installer's conceptual version →
 # direct upgrade stays unsupported; after retirement-style removal of
 # the stale manifest, normal planning proceeds.
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"theme": "dark"}')
        assert self.install("--agy").returncode == 0
        installed = self.read_manifest()["hud_version"]
        man = self.read_manifest()
        man["hud_version"] = "9.9.9"
        with open(self.manifest_path(), "w") as f:
            json.dump(man, f)
        r = self.install("--agy")
        self.assertEqual(r.returncode, 3)
        self.assertIn("UPGRADE_UNSUPPORTED", r.stdout)
 # simulate the documented uninstall-first lifecycle completing
        man["hud_version"] = installed
        with open(self.manifest_path(), "w") as f:
            json.dump(man, f)
        assert self.cli("uninstall").returncode == 0
        self.assertFalse(os.path.exists(self.manifest_path()))
        r = self.install("--agy")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class DriftEvidenceLifetimeTests(Base):
    """Archived drift evidence must survive later clean uninstalls."""

    def _quota_marker(self):
        qroot = os.path.join(
            self.home, ".config/herdr/cen-harness-hud-quota/profiles")
        os.makedirs(qroot, exist_ok=True)
        marker = os.path.join(qroot, "marker.json")
        with open(marker, "w") as f:
            f.write("{}\n")
        return marker

    def _drift_cycle(self):
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"theme": "dark"}')
        assert self.install("--agy").returncode == 0
        p = os.path.join(self.home, ".gemini/antigravity-cli/settings.json")
        edited = json.loads(open(p).read())
        # drift the CEN-OWNED surface itself so uninstall must retire the
        # manifest (an unrelated-key edit resolves cleanly under schema v2)
        edited["statusLine"]["command"] = "user-customized-%d" % len(
            os.listdir(os.path.join(
                self.home, ".config/cen-harness-hud/drift-archive"))) \
            if os.path.isdir(os.path.join(
                self.home,
                ".config/cen-harness-hud/drift-archive")) else "user-customized"
        open(p, "w").write(json.dumps(edited))
        assert self.cli("uninstall").returncode == 0

    def test_archive_and_backups_survive_later_clean_uninstall(self):
        marker = self._quota_marker()
        self._drift_cycle()
        arch_root = os.path.join(self.home,
                                 ".config/cen-harness-hud/drift-archive")
        archives = os.listdir(arch_root)
        self.assertEqual(len(archives), 1)
        archived_manifest = os.path.join(arch_root, archives[0],
                                         "install-manifest.json")
        man_a = json.load(open(archived_manifest))
 # reinstall B → clean uninstall B
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"theme": "dark"}')
        assert self.install("--agy").returncode == 0
        install_id_b = self.read_manifest()["install_id"]
        assert self.cli("uninstall").returncode == 0
 # archived manifest A STILL exists
        self.assertTrue(os.path.isfile(archived_manifest))
 # backup A STILL exists and manifest references resolve
        for pf in man_a["patched_files"]:
            ref = os.path.join(self.home,
                               pf["backup"].replace("~/", ""))
            self.assertTrue(os.path.isfile(ref), pf["backup"])
            self.assertEqual(stat.S_IMODE(os.stat(ref).st_mode), 0o600)
 # current B backup dir removed; backups_root remains (holds backup A)
        self.assertFalse(os.path.isdir(os.path.join(
            self.home, ".config/cen-harness-hud/backups", install_id_b)))
        self.assertTrue(os.path.isdir(os.path.join(
            self.home, ".config/cen-harness-hud/backups")))
 # archive modes remain private
        self.assertEqual(stat.S_IMODE(os.stat(arch_root).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(
            os.path.dirname(archived_manifest)).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(archived_manifest).st_mode),
                         0o600)
 # no active manifest; personal runtime state untouched
        self.assertFalse(os.path.exists(self.manifest_path()))
        self.assertTrue(os.path.isfile(marker))

    def test_clean_uninstall_without_history_unchanged(self):
        marker = self._quota_marker()
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"keep": true}')
        assert self.install("--agy").returncode == 0
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertIn("UNINSTALL_COMPLETE", r.stdout)
        self.assertNotIn("WITH_DRIFT", r.stdout)
        self.assertFalse(os.path.exists(self.manifest_path()))
        self.assertFalse(os.path.isdir(os.path.join(
            self.home, ".config/cen-harness-hud/backups")))
        self.assertFalse(os.path.exists(
            os.path.join(self.home,
                         ".local/share/cen-harness-hud")))
        self.assertTrue(os.path.isfile(marker))

    def test_multiple_drift_archives_all_survive_final_clean_uninstall(self):
        self._quota_marker()
        self._drift_cycle() # archive 1
        self._drift_cycle() # archive 2
        arch_root = os.path.join(self.home,
                                 ".config/cen-harness-hud/drift-archive")
        self.assertEqual(len(os.listdir(arch_root)), 2)
        archives = sorted(os.listdir(arch_root))
        refs = []
        for a in archives:
            m = json.load(open(os.path.join(
                arch_root, a, "install-manifest.json")))
            refs.extend(pf["backup"] for pf in m["patched_files"])
        # final clean uninstall of a third cycle
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"theme": "dark"}')
        assert self.install("--agy").returncode == 0
        install_id_c = self.read_manifest()["install_id"]
        assert self.cli("uninstall").returncode == 0
 # both historical archives + their backups survive
        self.assertEqual(sorted(os.listdir(arch_root)), archives)
        for ref in refs:
            self.assertTrue(os.path.isfile(os.path.join(
                self.home, ref.replace("~/", ""))), ref)
 # only C's own backup dir was removed
        self.assertFalse(os.path.isdir(os.path.join(
            self.home, ".config/cen-harness-hud/backups", install_id_c)))
        self.assertTrue(os.path.isdir(os.path.join(
            self.home, ".config/cen-harness-hud/backups")))


class VersionWiringTests(Base):
    """VERSION file is the single product-version truth across surfaces."""

    def _surfaces(self, expect_version):
        root = os.path.join(self.home, ".local/share/cen-harness-hud",
                            expect_version)
        with open(os.path.join(root, "VERSION")) as f:
            file_v = f.read().strip()
        man = self.read_manifest()
        plugins = json.loads(self.read(".config/herdr/plugins.json"))
        reg = [e for e in plugins if isinstance(e, dict)
               and e.get("plugin_id") == "cen-harness-hud-quota"]
        import tomllib as tl
        gen = tl.load(open(os.path.join(root, "herdr-plugin.toml"), "rb"))
        return {
            "file": file_v,
            "manifest": man["hud_version"],
            "registry": reg[0]["version"] if reg else None,
            "generated_plugin": gen["version"],
            "generated_manifest_path": os.path.join(root,
                                                    "herdr-plugin.toml"),
        }

    def assert_consistent(self, version):
        s = self._surfaces(version)
        for key in ("file", "manifest", "registry", "generated_plugin"):
            self.assertEqual(s[key], version, key)
 # no unresolved placeholders leaked into the rendered manifest
        with open(s["generated_manifest_path"]) as f:
            text = f.read()
        for placeholder in ("{{VERSION}}", "{{PYTHON}}",
                            "{{SCOPED_EVENT}}"):
            self.assertNotIn(placeholder, text)

    def test_current_version_all_surfaces_agree(self):
        with open(os.path.join(REPO, "VERSION")) as f:
            current = f.read().strip()
        r = self.install("--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assert_consistent(current)

    def test_synthetic_bump_wires_every_surface(self):
 # disposable source copy whose VERSION is bumped — repo VERSION untouched
        with open(os.path.join(REPO, "VERSION")) as f:
            canonical = f.read().strip()
        bumped = "9.8.7"
        src = tempfile.mkdtemp(prefix="cen-hud-src-")
        try:
            for item in ("bin", "installer", "integrations", "templates",
                         "VERSION"):
                s = os.path.join(REPO, item)
                d = os.path.join(src, item)
                if os.path.isdir(s):
                    shutil.copytree(s, d, ignore=shutil.ignore_patterns(
                        "__pycache__", "*.pyc"))
                else:
                    shutil.copy2(s, d)
            with open(os.path.join(src, "VERSION"), "w") as f:
                f.write(bumped + "\n")
            env = dict(os.environ)
            env["HOME"] = self.home
            env["PATH"] = MIN_PATH
            r = subprocess.run(
                [sys.executable, CLI, "install", "--source-root", src,
                 "--herdr"],
                env=env, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assert_consistent(bumped)
        finally:
            shutil.rmtree(src, ignore_errors=True)
 # canonical repo VERSION must remain untouched by this test
        with open(os.path.join(REPO, "VERSION")) as f:
            self.assertEqual(f.read().strip(), canonical)


if __name__ == "__main__":
    unittest.main()
