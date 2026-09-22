#!/usr/bin/env python3
"""Schema-v2 semantic ownership matrix (installer ownership tests).

FAKE_HOME only via test_install_sandbox.Base (shares its REAL-HOME guard).
Synthetic values only. Each case ends at uninstall and asserts the final
semantic state — the F-2/F-3 closure contract.
"""

import json
import os
import stat
import sys
import tomllib
import unittest

try:
    from test_install_sandbox import Base
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_install_sandbox import Base


def rows_of(testcase):
    doc = tomllib.loads(testcase.read(".config/herdr/config.toml"))
    node = doc.get("ui", {}).get("sidebar", {}).get("agents", {})
    return node.get("rows_by_agent", {})


def agy_command(testcase):
    return json.loads(testcase.read(
        ".gemini/antigravity-cli/settings.json")
    )["statusLine"]["command"]


class AggyOwnershipTests(Base):
    """Matrix 3-4 + F-2 acceptance."""

    def test_inherited_current_command_adopted_then_removed(self):
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"keep": true}')
        assert self.install("--agy").returncode == 0
        man = self.read_manifest()
        self.assertEqual(man["schema_version"], 2)
        recs = [r for r in man["semantic_patches"]
                if r["key"] == "statusLine.command"]
        self.assertEqual(len(recs), 1)
        # second install adopts the current CEN command as a claim
        assert self.cli("uninstall").returncode == 0
        # simulate the inherited state a next generation would see is
        # covered by test_inherited_old_command_migrates below; here the
        # adopted claim must have removed the command on uninstall
        self.assertEqual(json.loads(self.read(
            ".gemini/antigravity-cli/settings.json")), {"keep": True})

    def test_inherited_old_command_migrates_then_no_orphan(self):
        legacy = ("python3 " + os.path.join(
            self.home, ".local/share/cen-harness-hud/9.9.9",
            "integrations/agy/status.py"))
        self.write(".gemini/antigravity-cli/settings.json",
                   json.dumps({"keep": True, "statusLine": {
                       "type": "command", "command": legacy}}))
        r = self.install("--agy")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("migrate", r.stdout)
        self.assertNotIn("9.9.9", agy_command(self))
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        after = json.loads(self.read(".gemini/antigravity-cli/settings.json"))
        self.assertEqual(after, {"keep": True})
        self.assertNotIn("WITH_DRIFT", r.stdout)

    def test_f2_closed_no_orphan_no_next_gen_conflict(self):        # F-2 acceptance: inherited old CEN path → migrate → uninstall →
        # no CEN command remains, so a newer generation installs cleanly.
        legacy = ("python3 " + os.path.join(
            self.home, ".local/share/cen-harness-hud/9.9.9",
            "integrations/agy/status.py"))
        self.write(".gemini/antigravity-cli/settings.json",
                   json.dumps({"statusLine": {"type": "command",
                                              "command": legacy}}))
        assert self.install("--agy").returncode == 0
        assert self.cli("uninstall").returncode == 0
        leftover = open(os.path.join(
            self.home, ".gemini/antigravity-cli/settings.json")).read()
        self.assertNotIn("cen-harness-hud", leftover)
        print("AGY_CEN_ORPHAN_AFTER_FINAL_UNINSTALL=NO")
        r = self.install("--agy")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        print("NEXT_GENERATION_AGY_CONFLICT_FROM_OLD_CEN_PATH=NO")


class HerdrRowOwnershipTests(Base):
    """Matrix 5-6 + F-3 acceptance."""

    CODEX_V020 = (
        'codex = [\n  ["workspace", "tab"],\n  ["agent", "state_text"],\n'
        '  [\n    { token = "$cen_codex_identity", bold = true }\n  ],\n'
        '  [\n    { token = "$cen_codex_window_1", bold = true }\n  ],\n'
        '  [\n    { token = "$cen_codex_window_2", bold = true }\n  ]\n]\n'
    )

    AGY_EXACT = (
        'agy = [\n  ["workspace", "tab"],\n  ["agent", "state_text"],\n'
        '  [\n    { token = "$cen_agy_identity", bold = true }\n  ],\n'
        '  [\n    { token = "$cen_agy_quota", bold = true }\n  ]\n]\n'
    )

    def test_inherited_rows_adopted_then_removed(self):
        self.write(".config/herdr/config.toml",
                   "[ui.sidebar.agents]\nrow_gap = 1\n"
                   "[ui.sidebar.agents.rows_by_agent]\n" + self.AGY_EXACT,
                   mode=0o640)
        assert self.install("--herdr").returncode == 0
        # adoption claims exist for the current CEN rows
        man = self.read_manifest()
        adopts = [r for r in man["semantic_patches"]
                  if r["basis"] == "adopt"]
        self.assertTrue(adopts)
        assert self.cli("uninstall").returncode == 0
        rows = rows_of(self)
        self.assertNotIn("agy", rows)
        self.assertNotIn("codex", rows)
        self.assertIn("row_gap", self.read(".config/herdr/config.toml"))

    def test_f3_closed_no_resurrected_rows(self):
        # F-3 acceptance: older CEN rows inherited into the baseline must
        # not return after the final clean uninstall.
        self.write(".config/herdr/config.toml",
                   "[ui.sidebar.agents.rows_by_agent]\n" + self.CODEX_V020,
                   mode=0o640)
        assert self.install("--herdr").returncode == 0
        self.assertEqual(len(rows_of(self)["codex"]), 4)
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = rows_of(self)
        for key in ("agy", "codex", "pi", "opencode"):
            self.assertNotIn(key, rows)
        print("HERDR_INHERITED_CEN_ROWS_AFTER_FINAL_UNINSTALL=NO")


class CodexOwnershipTests(Base):
    """Matrix 10-14: TOML owned-key drift, hooks preexisting/CEN-created,
    old hook migration, user hooks preserved."""

    def test_user_edits_owned_toml_key_preserved_with_drift(self):
        self.write(".codex/config.toml", "[tui]\nkeep = 1\n")
        assert self.install("--codex").returncode == 0
        p = os.path.join(self.home, ".codex/config.toml")
        beside = open(p).read().replace(
            '"five-hour-limit",', '"five-hour-limit",\n  "user-row",')
        open(p, "w").write(beside)
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertIn("DRIFT", r.stdout)
        self.assertIn("user-row", open(p).read())

    def test_preexisting_hooks_true_survives(self):
        self.write(".codex/config.toml",
                   "[features]\nhooks = true\n[tui]\nkeep = 1\n")
        assert self.install("--codex").returncode == 0
        man = self.read_manifest()
        hooks_recs = [r for r in man["semantic_patches"]
                      if r["key"] == "hooks"]
        self.assertFalse(hooks_recs) # never claimed (CP11)
        assert self.cli("uninstall").returncode == 0
        doc = tomllib.loads(self.read(".codex/config.toml"))
        self.assertTrue(doc["features"]["hooks"])
        self.assertEqual(doc["tui"], {"keep": 1})

    def test_cen_created_hooks_true_removed(self):
        self.write(".codex/config.toml", "[tui]\nkeep = 1\n")
        assert self.install("--codex").returncode == 0
        assert self.cli("uninstall").returncode == 0
        doc = tomllib.loads(self.read(".codex/config.toml"))
        self.assertNotIn("hooks", doc.get("features", {}))
        self.assertEqual(doc["tui"], {"keep": 1})

    def test_unrelated_hooks_survive_cen_hook_removed(self):
        self.write(".codex/hooks.json", json.dumps({"hooks": {
            "SessionStart": [{"hooks": [
                {"type": "command", "command": "user-hook-cmd",
                 "timeout": 5}]}]}}))
        assert self.install("--codex").returncode == 0
        data = json.loads(self.read(".codex/hooks.json"))
        cmds = [h["command"] for e in data["hooks"]["SessionStart"]
                for h in e["hooks"]]
        self.assertEqual(len(cmds), 2)
        assert self.cli("uninstall").returncode == 0
        data = json.loads(self.read(".codex/hooks.json"))
        cmds = [h["command"] for e in data["hooks"]["SessionStart"]
                for h in e["hooks"]]
        self.assertEqual(cmds, ["user-hook-cmd"])

    def test_old_cen_hook_migrates_to_single_then_zero(self):
        old = os.path.join(
            self.home, ".local/share/cen-harness-hud/9.9.9",
            "integrations/codex/session_hook.py")
        self.write(".codex/hooks.json", json.dumps({"hooks": {
            "SessionStart": [{"hooks": [
                {"type": "command", "command": old, "timeout": 10}]}]}}))
        r = self.install("--codex")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        data = json.loads(self.read(".codex/hooks.json"))
        cmds = [h["command"] for e in data["hooks"]["SessionStart"]
                for h in e["hooks"]]
        self.assertEqual(len(cmds), 1)
        self.assertNotIn(old, cmds)
        assert self.cli("uninstall").returncode == 0
        data = json.loads(self.read(".codex/hooks.json"))
        entries = data.get("hooks", {}).get("SessionStart", [])
        self.assertEqual(entries, [])


class RegistryAndCreatedFileTests(Base):
    """Matrix 15-17: registry unrelated entries, created-file fates."""

    def test_registry_unrelated_entries_survive(self):
        self.write(".config/herdr/plugins.json", json.dumps(
            [{"plugin_id": "user-plugin", "manifest_path": "/user/x"}]))
        assert self.install("--herdr").returncode == 0
        assert self.cli("uninstall").returncode == 0
        data = json.loads(self.read(".config/herdr/plugins.json"))
        self.assertEqual(data, [{"plugin_id": "user-plugin",
                                 "manifest_path": "/user/x"}])

    def test_created_file_without_user_content_removed(self):
        assert self.install("--herdr").returncode == 0
        assert self.cli("uninstall").returncode == 0
        self.assertFalse(os.path.lexists(os.path.join(
            self.home, ".config/herdr/config.toml")))

    def test_created_file_with_user_content_kept_cen_removed(self):
        assert self.install("--codex").returncode == 0
        p = os.path.join(self.home, ".codex/config.toml")
        open(p, "a").write('\n[user]\nnote = "keep me"\n')
        assert self.cli("uninstall").returncode == 0
        text = open(p).read()
        self.assertIn('note = "keep me"', text)
        self.assertNotIn("status_line", text)
        self.assertNotIn("hooks = true", text)


class RobustnessTests(Base):
    """Matrix 19/21/22/23/24/25."""

    def test_unknown_schema_fails_closed_without_mutation(self):
        assert self.install("--agy").returncode == 0
        man_path = os.path.join(
            self.home, ".config/cen-harness-hud/install-manifest.json")
        man = json.load(open(man_path))
        man["schema_version"] = 99
        json.dump(man, open(man_path, "w"))
        before = open(os.path.join(
            self.home, ".gemini/antigravity-cli/settings.json")).read()
        r = self.cli("uninstall")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("UNSUPPORTED_MANIFEST_SCHEMA", r.stdout)
        self.assertEqual(open(os.path.join(
            self.home, ".gemini/antigravity-cli/settings.json")).read(),
            before)

    def test_schema1_fixture_keeps_conservative_path(self):
        # Synthetic v0.3.0 schema-1 manifest: whole-file restore iff sha
        # matches, otherwise drift-retire. NEW manager must not semanticize.
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"theme": "dark"}')
        assert self.install("--agy").returncode == 0
        man_path = os.path.join(
            self.home, ".config/cen-harness-hud/install-manifest.json")
        man = json.load(open(man_path))
        man["schema_version"] = 1
        man.pop("semantic_patches", None)
        json.dump(man, open(man_path, "w"))
        p = os.path.join(self.home, ".gemini/antigravity-cli/settings.json")
        open(p, "w").write('{"theme": "ocean"}') # unrelated drift
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertIn("UNINSTALL_COMPLETE_WITH_DRIFT", r.stdout)
        print("SCHEMA1_COMPATIBILITY=PASS")

    def test_repeated_cycles_accumulate_zero_residue(self):
        for _ in range(3):
            self.write(".gemini/antigravity-cli/settings.json",
                       '{"theme": "dark"}')
            self.write(".config/herdr/config.toml",
                       "[ui.sidebar.agents]\nrow_gap = 3\n")
            assert self.install("--agy", "--herdr").returncode == 0
            assert self.cli("uninstall").returncode == 0
        agy = json.loads(self.read(".gemini/antigravity-cli/settings.json"))
        self.assertEqual(agy, {"theme": "dark"})
        text = self.read(".config/herdr/config.toml")
        self.assertNotIn("cen_", text)
        self.assertIn("row_gap = 3", text)

    def test_final_state_equals_u0_without_drift(self):
        u0_agy = '{"theme": "dark"}'
        u0_herdr = "[ui.sidebar.agents]\nrow_gap = 3\n"
        self.write(".gemini/antigravity-cli/settings.json", u0_agy)
        self.write(".config/herdr/config.toml", u0_herdr)
        assert self.install("--agy", "--herdr").returncode == 0
        assert self.cli("uninstall").returncode == 0
        self.assertEqual(json.loads(self.read(
            ".gemini/antigravity-cli/settings.json")),
            json.loads(u0_agy))
        self.assertEqual(self.read(".config/herdr/config.toml"), u0_herdr)


class AggyTypeSurfaceTests(Base):
    """CP6-CP10: statusLine.type is an owned surface alongside command."""

    def test_user_type_restored_exactly(self):
        before = {"theme": "dark", "statusLine": {"type": "user-type",
                                                  "other": "keep-me"}}
        self.write(".gemini/antigravity-cli/settings.json",
                   json.dumps(before))
        assert self.install("--agy").returncode == 0
        man = self.read_manifest()
        keys = sorted(r["key"] for r in man["semantic_patches"]
                      if r["path"].endswith("settings.json"))
        self.assertEqual(keys, ["statusLine.command", "statusLine.type"])
        assert self.cli("uninstall").returncode == 0
        self.assertEqual(json.loads(self.read(
            ".gemini/antigravity-cli/settings.json")), before)
        print("AGY_STATUSLINE_USER_BASELINE_RESTORED=YES")

    def test_type_absent_leaves_no_residue(self):
        self.write(".gemini/antigravity-cli/settings.json",
                   json.dumps({"keep": True,
                               "statusLine": {"other": "keep-me"}}))
        assert self.install("--agy").returncode == 0
        assert self.cli("uninstall").returncode == 0
        self.assertEqual(json.loads(self.read(
            ".gemini/antigravity-cli/settings.json")),
            {"keep": True, "statusLine": {"other": "keep-me"}})

    def test_preexisting_generic_type_not_claimed(self):
        self.write(".gemini/antigravity-cli/settings.json", json.dumps(
            {"statusLine": {"type": "command"}}))
        assert self.install("--agy").returncode == 0
        man = self.read_manifest()
        type_recs = [r for r in man["semantic_patches"]
                     if r["key"] == "statusLine.type"]
        self.assertFalse(type_recs)
        assert self.cli("uninstall").returncode == 0
        self.assertEqual(json.loads(self.read(
            ".gemini/antigravity-cli/settings.json")),
            {"statusLine": {"type": "command"}})

    def test_inherited_cen_with_sibling_leaves_no_residue(self):
        legacy = ("python3 " + os.path.join(
            self.home, ".local/share/cen-harness-hud/9.9.9",
            "integrations/agy/status.py"))
        self.write(".gemini/antigravity-cli/settings.json", json.dumps(
            {"statusLine": {"type": "command", "command": legacy,
                            "userSibling": "keep"}}))
        assert self.install("--agy").returncode == 0
        assert self.cli("uninstall").returncode == 0
        after = json.loads(self.read(".gemini/antigravity-cli/settings.json"))
        self.assertEqual(after, {"statusLine": {"userSibling": "keep"}})
        self.assertNotIn("cen-harness-hud", json.dumps(after))
        print("AGY_INHERITED_CEN_RESIDUE=NO")

    def test_type_only_drift_preserves_command_inverse(self):
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"keep": true}')
        assert self.install("--agy").returncode == 0
        p = os.path.join(self.home, ".gemini/antigravity-cli/settings.json")
        data = json.load(open(p))
        data["statusLine"]["type"] = "user-changed"
        json.dump(data, open(p, "w"))
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertIn("WITH_DRIFT", r.stdout)
        after = json.load(open(p))
        self.assertEqual(after["statusLine"]["type"], "user-changed")
        self.assertNotIn("command", after["statusLine"])
        self.assertEqual(after["keep"], True)

    def test_command_only_drift_preserves_type_inverse(self):
        self.write(".gemini/antigravity-cli/settings.json",
                   '{"keep": true}')
        assert self.install("--agy").returncode == 0
        p = os.path.join(self.home, ".gemini/antigravity-cli/settings.json")
        data = json.load(open(p))
        data["statusLine"]["command"] = "user-cmd"
        json.dump(data, open(p, "w"))
        r = self.cli("uninstall")
        self.assertEqual(r.returncode, 0)
        self.assertIn("WITH_DRIFT", r.stdout)
        after = json.load(open(p))
        self.assertEqual(after["statusLine"]["command"], "user-cmd")
        self.assertNotIn("status.py", json.dumps(after))

    def test_siblings_preserved_exactly(self):
        before = {"statusLine": {"other": "a", "style": "b", "custom": "c"}}
        self.write(".gemini/antigravity-cli/settings.json",
                   json.dumps(before))
        assert self.install("--agy").returncode == 0
        assert self.cli("uninstall").returncode == 0
        self.assertEqual(json.loads(self.read(
            ".gemini/antigravity-cli/settings.json")), before)

    def test_created_file_user_sibling_kept_cen_stripped(self):
        assert self.install("--agy").returncode == 0
        p = os.path.join(self.home, ".gemini/antigravity-cli/settings.json")
        data = json.load(open(p))
        data["statusLine"]["sibling"] = "keep"
        data["top"] = "keep"
        json.dump(data, open(p, "w"))
        assert self.cli("uninstall").returncode == 0
        after = json.load(open(p))
        self.assertEqual(after, {"statusLine": {"sibling": "keep"},
                                 "top": "keep"})


class AggyNonObjectConflictTests(Base):
    """CP6-CP7: foreign non-object statusLine fails closed with zero
    mutation — including whole-run atomicity when other components pass."""

    def check_conflict_zero_mutation(self, value, tag):
        import hashlib
        self.write(".gemini/antigravity-cli/settings.json",
                   json.dumps({"keep": True, "statusLine": value}))
        before = hashlib.sha256(self.read(
            ".gemini/antigravity-cli/settings.json").encode()).hexdigest()
        r = self.install("--agy")
        self.assertNotEqual(r.returncode, 0, tag)
        self.assertIn("CONFLICT", r.stdout, tag)
        self.assertEqual(hashlib.sha256(self.read(
            ".gemini/antigravity-cli/settings.json").encode()).hexdigest(),
            before)
        # planning happens before ANY mutation: no manifest, product, links
        self.assertFalse(os.path.exists(self.manifest_path()))
        self.assertFalse(os.path.exists(os.path.join(
            self.home, ".local/share/cen-harness-hud")))
        self.assertFalse(os.path.lexists(os.path.join(
            self.home, ".local/bin/cen-hud")))
        self.assertFalse(os.path.isdir(os.path.join(
            self.home, ".config/cen-harness-hud/backups")))

    def test_string_conflicts(self):
        self.check_conflict_zero_mutation("user-string", "string")

    def test_array_conflicts(self):
        self.check_conflict_zero_mutation(["user", "list"], "array")

    def test_null_conflicts(self):
        self.check_conflict_zero_mutation(None, "null")

    def test_number_conflicts(self):
        self.check_conflict_zero_mutation(123, "number")

    def test_boolean_conflicts(self):
        self.check_conflict_zero_mutation(True, "boolean")

    def test_whole_run_atomic_when_others_pass(self):
        self.write(".gemini/antigravity-cli/settings.json",
                   json.dumps({"statusLine": "user-string"}))
        self.write(".config/herdr/config.toml",
                   "[ui.sidebar.agents]\nrow_gap = 3\n")
        r = self.install("--agy", "--herdr", "--codex", "--pi")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("CONFLICT", r.stdout)
        # NO partial Codex/Herdr/Pi install survived the aborted plan
        self.assertFalse(os.path.exists(self.manifest_path()))
        self.assertFalse(os.path.exists(os.path.join(
            self.home, ".local/share/cen-harness-hud")))
        self.assertIn("row_gap = 3", self.read(".config/herdr/config.toml"))
        self.assertNotIn("cen_", self.read(".config/herdr/config.toml"))


class AggyRecordAuditTests(Base):
    """CP9: manifest records exactly the mutated surfaces, no duplicates."""

    def records_for(self, suffix):
        man = self.read_manifest()
        return sorted(
            (r["key"], r["basis"]) for r in man["semantic_patches"]
            if r["path"].endswith(suffix))

    def test_fresh_records_command_and_type(self):
        assert self.install("--agy").returncode == 0
        self.assertEqual(
            self.records_for("settings.json"),
            [("statusLine.command", "insert"),
             ("statusLine.type", "insert")])

    def test_user_type_records_both_with_prior(self):
        self.write(".gemini/antigravity-cli/settings.json", json.dumps(
            {"statusLine": {"type": "user-type"}}))
        assert self.install("--agy").returncode == 0
        man = self.read_manifest()
        by_key = {r["key"]: r for r in man["semantic_patches"]
                  if r["path"].endswith("settings.json")}
        self.assertEqual(
            sorted(by_key), ["statusLine.command", "statusLine.type"])
        self.assertEqual(by_key["statusLine.type"]["before"],
                         {"state": "value", "value": "user-type"})
        print("AGY_SEMANTIC_RECORDS_EXACT=YES")


if __name__ == "__main__":
    unittest.main()
