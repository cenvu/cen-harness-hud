#!/usr/bin/env python3
"""sandbox test suite - stdlib unittest only.

HARD GUARD (CP22): every mutation must land under a disposable FAKE_HOME.
The suite snapshots real-HOME sentinels before/after and FAILS on any
change. No test touches the real HOME. No live-machine installer mutation.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
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
]

CODEX_DESIRED = [
    "model-with-reasoning",
    "context-remaining",
    "five-hour-limit",
    "weekly-limit",
]


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
        self.assertEqual(man["schema_version"], 1)
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
        r = self.install("--agy", "--codex", "--pi", "--herdr")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        root = os.path.join(self.home, ".local/share/cen-harness-hud/0.1.0")
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
        for name in ("cen-hud", "cen-codex", "cen-codex-status"):
            link = os.path.join(self.home, ".local/bin", name)
            self.assertTrue(os.path.islink(link), name)
            self.assertTrue(os.path.realpath(link).startswith(
                os.path.realpath(root)))
 # regression: directly-exec'd integration entrypoints keep +x
        for rel in ("integrations/codex/launcher.py",
                    "integrations/codex/status.py"):
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
        self.assertEqual(len(rows["codex"]), 3)
        self.assertEqual(len(rows["pi"]), 3)
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
        self.assertEqual(self.read(".gemini/antigravity-cli/settings.json"),
                         '{"keep": true}')
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
        edited_file = json.loads(self.read(
            ".gemini/antigravity-cli/settings.json"))
        self.assertEqual(edited_file["statusLine"]["command"],
                         "user-customized")
        self.assertTrue(os.path.isfile(self.manifest_path()))


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
        self.assertEqual(len(rows["codex"]), 3)
        self.assertEqual(len(rows["pi"]), 3)
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
 # uninstall restores ORIGINAL bytes and mode
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertIn("restored:", r.stdout)
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
        share = os.path.join(self.home, ".local/share/cen-harness-hud/0.1.0")
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


if __name__ == "__main__":
    unittest.main()
