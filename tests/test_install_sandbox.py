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
        self.assertEqual(len(rows["codex"]), 5)
        self.assertEqual(len(rows["pi"]), 3)
 # AGY/Pi rows unchanged; Codex card is exactly 5 rows with new tokens
        self.assertEqual(
            rows["codex"],
            [
                ["workspace", "tab"],
                ["agent", "state_text"],
                [{"token": "$cen_codex_identity", "bold": True}],
                [{"token": "$cen_codex_window_1", "bold": True}],
                [{"token": "$cen_codex_window_2", "bold": True}],
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
        self.assertEqual(len(rows["codex"]), 5)
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


class CodexDisplayTokenTests(unittest.TestCase):
    """Synthetic-fixture coverage for the dual-window Codex card contract.

    All fixtures are synthetic. No real account values, paths, or quotas.
    """

    NOW = 1_000_000_000.0
    IDENTITY = "cen_codex_identity"
    W1 = "cen_codex_window_1"
    W2 = "cen_codex_window_2"
    SUMMARY = "cen_codex_summary"

    @classmethod
    def setUpClass(cls):
        cls._codex_dir = os.path.join(REPO, "integrations", "codex")
        sys.path.insert(0, cls._codex_dir)
        import unittest.mock # noqa: F401
        import publisher as pub
        import launcher as lch
        cls.pub = pub
        cls.lch = lch

    @classmethod
    def tearDownClass(cls):
        if cls._codex_dir in sys.path:
            sys.path.remove(cls._codex_dir)

    # ── synthetic fixture builders ────────────────────────────────────────

    def acc(self, local="demo", plan="PLUS"):
        return {"account": {
            "type": "chatgpt",
            "email": f"{local}@example.invalid",
            "planType": plan,
        }}

    def rl(self, primary=None, secondary=None):
        snap = {}
        if primary is not None:
            snap["primary"] = primary
        if secondary is not None:
            snap["secondary"] = secondary
        return {"rateLimitsByLimitId": {"codex": snap}}

    def dual_rl(self):
        return self.rl(
            primary={
                "usedPercent": 18,
                "windowDurationMins": 300,
                "resetsAt": self.NOW + 2 * 3600 + 14 * 60,
            },
            secondary={
                "usedPercent": 39,
                "windowDurationMins": 10080,
                "resetsAt": self.NOW + 4 * 86400 + 7 * 3600,
            },
        )

    def build(self, acc_res, rl_res):
        return self.pub.build_display_tokens(
            acc_res, rl_res, now_epoch=self.NOW)

    # ── cases 1-3: dual windows, LEFT math, countdown truth ──────────────

    def test_dual_windows_percent_and_countdowns(self):
        tok = self.build(self.acc(), self.dual_rl())
        self.assertEqual(tok[self.IDENTITY], "demo · PLUS")
        self.assertEqual(tok[self.W1], "5H 82% · ↻2h14m")
        self.assertEqual(tok[self.W2], "7D 61% · ↻4d7h")

    def test_left_percent_clamped_bounds(self):
        rl = self.rl(
            primary={"usedPercent": 0, "windowDurationMins": 300},
            secondary={"usedPercent": 150, "windowDurationMins": 10080},
        )
        tok = self.build(self.acc(), rl)
        self.assertEqual(tok[self.W1], "5H 100%")
        self.assertEqual(tok[self.W2], "7D 0%")

    def test_countdown_reset_in_past_renders_now(self):
        rl = self.rl(primary={
            "usedPercent": 50, "windowDurationMins": 300,
            "resetsAt": self.NOW - 60,
        })
        tok = self.build(self.acc(), rl)
        self.assertEqual(tok[self.W1], "5H 50% · ↻now")

    # ── cases 4-7: degraded window sets ──────────────────────────────────

    def test_primary_only_secondary_slot_fails_closed(self):
        rl = self.rl(primary={
            "usedPercent": 18, "windowDurationMins": 300,
            "resetsAt": self.NOW + 8040,
        })
        tok = self.build(self.acc(), rl)
        self.assertEqual(tok[self.W1], "5H 82% · ↻2h14m")
        self.assertEqual(tok[self.W2], "—")

    def test_nonfinite_secondary_fails_closed(self):
        for bad in (float("nan"), float("inf"), "not-a-number"):
            with self.subTest(bad=bad):
                rl = self.rl(
                    primary={"usedPercent": 10, "windowDurationMins": 300},
                    secondary={"usedPercent": bad,
                               "windowDurationMins": 10080},
                )
                tok = self.build(self.acc(), rl)
                self.assertEqual(tok[self.W1], "5H 90%")
                self.assertEqual(tok[self.W2], "—")

    def test_missing_resetsat_quota_without_countdown(self):
        rl = self.rl(
            primary={"usedPercent": 18, "windowDurationMins": 300},
            secondary={"usedPercent": 39, "windowDurationMins": 10080},
        )
        tok = self.build(self.acc(), rl)
        self.assertEqual(tok[self.W1], "5H 82%")
        self.assertNotIn("↻", tok[self.W1])
        self.assertEqual(tok[self.W2], "7D 61%")
        self.assertNotIn("↻", tok[self.W2])

    def test_both_windows_absent_fail_closed(self):
        for rl_res in (None, {}, {"rateLimitsByLimitId": {}}, self.rl()):
            with self.subTest(rl=bool(rl_res)):
                tok = self.build(self.acc(), rl_res)
                self.assertEqual(tok[self.W1], "—")
                self.assertEqual(tok[self.W2], "—")

    # ── cases 8-9: identity sanitization + privacy ───────────────────────

    def test_identity_sanitization(self):
        acc = self.acc(local="de\x1b[31mmo\x07bad", plan="plus ")
        tok = self.build(acc, None)
        self.assertEqual(tok[self.IDENTITY], "demobad · PLUS")

    def test_full_email_never_rendered(self):
        tok = self.build(self.acc(local="someone"), self.dual_rl())
        for key, value in tok.items():
            if value is None:
                continue
            self.assertNotIn("@", value, key)
            self.assertNotIn("example.invalid", value, key)

    def test_unusable_account_fails_closed(self):
        for acc_res in (None, {}, {"account": {}}, {"account": "junk"}):
            with self.subTest(acc=bool(acc_res)):
                tok = self.build(acc_res, self.dual_rl())
                self.assertEqual(tok[self.IDENTITY], "—")

    # ── case 10: two-window → one-window transition clears slot 2 ────────

    def test_switch_to_single_window_clears_second_token(self):
        two = self.build(self.acc(), self.dual_rl())
        one = self.build(self.acc(), self.rl(primary={
            "usedPercent": 18, "windowDurationMins": 300,
            "resetsAt": self.NOW + 8040,
        }))
        expected_keys = {self.IDENTITY, self.W1, self.W2, self.SUMMARY}
        self.assertTrue(expected_keys.issubset(two))
        self.assertTrue(expected_keys.issubset(one))
        self.assertEqual(two[self.W2], "7D 61% · ↻4d7h")
        self.assertEqual(one[self.W2], "—")
        self.assertIsNone(one[self.SUMMARY])
        self.assertIsNone(two[self.SUMMARY])

    # ── case 11: ownership-aware cleanup race protection ─────────────────

    def test_cleanup_skips_newer_registration(self):
        calls = []
        with unittest.mock.patch.object(
                self.lch, "pane_get_tokens",
                return_value={"cen_codex_profile": "NEWERREG@999"}):
            with unittest.mock.patch.object(
                    self.lch, "report_metadata",
                    side_effect=lambda *a: calls.append(a)):
                performed = self.lch.ownership_aware_cleanup(
                    "/tmp/sock-unused", "pane-1", "OLDERREG@123")
        self.assertFalse(performed)
        self.assertEqual(calls, [])

    def test_cleanup_failcloses_all_tokens_when_owner_matches(self):
        recorded = []
        own = "AAAA1111BBBB@4242"
        with unittest.mock.patch.object(
                self.lch, "pane_get_tokens",
                return_value={"cen_codex_profile": own}):
            with unittest.mock.patch.object(
                    self.lch, "report_metadata",
                    side_effect=lambda *a: recorded.append(a)):
                performed = self.lch.ownership_aware_cleanup(
                    "/tmp/sock-unused", "pane-1", own)
        self.assertTrue(performed)
        self.assertEqual(len(recorded), 2)
        profile_call, bridge_call = recorded
        self.assertEqual(profile_call[2], self.lch.LAUNCHER_SOURCE)
        self.assertEqual(profile_call[4], {"cen_codex_profile": None})
        self.assertEqual(bridge_call[2], self.lch.BRIDGE_SOURCE)
        self.assertEqual(bridge_call[4], {
            "cen_codex_identity": "—",
            "cen_codex_window_1": "—",
            "cen_codex_window_2": "—",
            "cen_codex_summary": None,
        })

    def test_cleanup_noop_on_unreadable_pane(self):
        with unittest.mock.patch.object(
                self.lch, "pane_get_tokens", return_value=None):
            with unittest.mock.patch.object(
                    self.lch, "report_metadata") as rep_mock:
                performed = self.lch.ownership_aware_cleanup(
                    "/tmp/sock-unused", "pane-1", "AAAA1111BBBB@4242")
        self.assertFalse(performed)
        rep_mock.assert_not_called()

    # ── fail-closed publisher surface ─────────────────────────────────────

    def test_publisher_fail_closed_token_set(self):
        tok = self.pub.fail_closed_tokens()
        self.assertEqual(tok[self.IDENTITY], "—")
        self.assertEqual(tok[self.W1], "—")
        self.assertEqual(tok[self.W2], "—")
        self.assertIsNone(tok[self.SUMMARY])


class InstallerMigrationTests(Base):
    """Legacy-shape migration + drift-manifest retirement (synthetic only)."""

    CURRENT_CODEX_VALUE = [
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
 # closer per top-level row block: agy, codex, pi)
        self.assertNotIn("$cen_codex_summary", text)
        self.assertEqual(
            sum(1 for ln in text.splitlines() if ln.strip() == "]"), 6)
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
        drifted_path = self._setup_with_drift()
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertIn("UNINSTALL_COMPLETE_WITH_DRIFT", r.stdout)
        self.assertIn("drift-archive", r.stdout)
 # drifted user file untouched (edit preserved, CEN keys intact)
        after = json.loads(open(drifted_path).read())
        self.assertEqual(after["theme"], "ocean")
        self.assertIn("status.py", after["statusLine"]["command"])
 # ACTIVE manifest gone
        self.assertFalse(os.path.exists(self.manifest_path()))
 # archived manifest retained, private modes
        arch_root = os.path.join(self.home,
                                 ".config/cen-harness-hud/drift-archive")
        self.assertTrue(os.path.isdir(arch_root))
        self.assertEqual(stat.S_IMODE(os.stat(arch_root).st_mode), 0o700)
        entries = os.listdir(arch_root)
        self.assertEqual(len(entries), 1)
        dest_dir = os.path.join(arch_root, entries[0])
        self.assertEqual(stat.S_IMODE(os.stat(dest_dir).st_mode), 0o700)
        archived = os.path.join(dest_dir, "install-manifest.json")
        self.assertTrue(os.path.isfile(archived))
        self.assertEqual(stat.S_IMODE(os.stat(archived).st_mode), 0o600)
        self.assertEqual(json.load(open(archived))["schema_version"], 1)
 # backups retained
        self.assertTrue(os.path.isdir(os.path.join(
            self.home, ".config/cen-harness-hud/backups")))
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
        edited["theme"] = "ocean-%d" % len(os.listdir(os.path.join(
            self.home, ".config/cen-harness-hud/drift-archive"))) \
            if os.path.isdir(os.path.join(
                self.home,
                ".config/cen-harness-hud/drift-archive")) else "ocean"
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
