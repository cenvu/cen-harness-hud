#!/usr/bin/env python3
"""Focused synthetic coverage for Codex quota telemetry v2."""

import json
import os
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODEX_DIR = os.path.join(REPO, "integrations", "codex")
if CODEX_DIR not in sys.path:
    sys.path.insert(0, CODEX_DIR)

import publisher
import session_hook
import status
import telemetry


class TelemetryResolverTests(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="cen-cdx-v2-")
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def test_native_session_mapping_beats_launcher_fallback(self):
        session = "thr_native"
        native = os.path.join(self.home, ".codex-native")
        legacy = os.path.join(self.home, ".codex-legacy")
        self.assertTrue(
            telemetry.write_session_mapping(self.home, session, native, 100.0)
        )
        with mock.patch.object(telemetry.status, "thread_exists") as probe:
            got = telemetry.resolve_codex_home(
                self.home, session, launcher_home=legacy)
        self.assertEqual(got, os.path.realpath(native))
        probe.assert_not_called()

    def test_generic_session_resolves_default_profile_without_launcher_token(self):
        session = "thr_generic"
        default = os.path.realpath(os.path.join(self.home, ".codex"))
        with mock.patch.object(
                telemetry.status, "thread_exists",
                side_effect=lambda sid, codex_home=None: (
                    sid == session and os.path.realpath(codex_home) == default)):
            got = telemetry.resolve_codex_home(self.home, session, None)
        self.assertEqual(got, default)
        self.assertEqual(
            telemetry.read_session_mapping(self.home, session), default)

    def test_generic_resolution_is_cwd_independent(self):
        session = "thr_cwd"
        default = os.path.realpath(os.path.join(self.home, ".codex"))
        a = tempfile.mkdtemp(dir=self.base)
        b = tempfile.mkdtemp(dir=self.base)
        old = os.getcwd()
        try:
            with mock.patch.object(
                    telemetry.status, "thread_exists",
                    return_value=True):
                os.chdir(a)
                first = telemetry.resolve_codex_home(
                    self.home, session, None)
            os.unlink(telemetry.mapping_path(self.home, session))
            with mock.patch.object(
                    telemetry.status, "thread_exists",
                    return_value=True):
                os.chdir(b)
                second = telemetry.resolve_codex_home(
                    self.home, session, None)
        finally:
            os.chdir(old)
        self.assertEqual(first, default)
        self.assertEqual(second, default)

    def test_known_profile_can_be_resolved_without_launcher_registration(self):
        session = "thr_alt"
        alt = os.path.realpath(os.path.join(self.home, "profiles", "alt"))
        pdir = os.path.join(
            self.home, ".config", "herdr",
            "cen-harness-hud-quota", "profiles")
        os.makedirs(pdir)
        with open(os.path.join(pdir, "ABC.json"), "w") as f:
            json.dump({"profile_tag": "ABC", "codex_home": alt}, f)
        default = os.path.realpath(os.path.join(self.home, ".codex"))
        def owns(sid, codex_home=None):
            return sid == session and os.path.realpath(codex_home) == alt
        with mock.patch.object(telemetry.status, "thread_exists",
                               side_effect=owns):
            got = telemetry.resolve_codex_home(self.home, session, None)
        self.assertNotEqual(got, default)
        self.assertEqual(got, alt)

    def test_unknown_session_fails_closed_without_guessing(self):
        with mock.patch.object(telemetry.status, "thread_exists",
                               return_value=False):
            self.assertIsNone(
                telemetry.resolve_codex_home(
                    self.home, "thr_unknown", None))

    def test_session_mapping_is_private_and_hash_keyed(self):
        session = "thr_secretish_identifier"
        telemetry.write_session_mapping(
            self.home, session, os.path.join(self.home, ".codex"))
        path = telemetry.mapping_path(self.home, session)
        self.assertNotIn(session, path)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode),
                         0o700)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


class SnapshotAndRefreshTests(unittest.TestCase):
    NOW = 1_000_000_000.0

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="cen-cdx-snap-")
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home)
        self.session = "thr_snap"
        self.codex_home = os.path.join(self.home, ".codex")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.base, ignore_errors=True)

    def _account(self):
        return {"account": {
            "type": "chatgpt",
            "email": "demo@example.invalid",
            "planType": "plus",
        }}

    def _limits(self):
        return {"rateLimitsByLimitId": {"codex": {
            "primary": {
                "usedPercent": 21,
                "windowDurationMins": 300,
                "resetsAt": self.NOW + 3000,
            },
            "secondary": {
                "usedPercent": 12,
                "windowDurationMins": 10080,
                "resetsAt": self.NOW + 6 * 86400,
            },
        }}}

    def test_snapshot_has_short_alias_and_weekly_truth(self):
        snap = telemetry.build_snapshot(
            self._account(), self._limits(), self.NOW)
        self.assertEqual(snap["account_alias"], "demo")
        self.assertNotIn("@", json.dumps(snap))
        self.assertEqual(snap["primary"]["remaining_percent"], 79)
        self.assertEqual(snap["weekly"]["remaining_percent"], 88)
        self.assertEqual(
            telemetry.format_weekly(snap, self.NOW), "7D 88% · ↻6d")

    def test_working_refresh_rate_is_sixty_seconds(self):
        snap = {"schema": 1, "last_attempt_at": self.NOW}
        self.assertFalse(
            telemetry.refresh_due(snap, self.NOW + 59.9))
        self.assertTrue(
            telemetry.refresh_due(snap, self.NOW + 60.0))

    def test_transient_fetch_failure_retains_last_valid_snapshot(self):
        valid = telemetry.build_snapshot(
            self._account(), self._limits(), self.NOW)
        telemetry._atomic_json(
            telemetry.snapshot_path(self.home, self.session), valid)
        with mock.patch.object(
                telemetry.status, "query_app_server",
                return_value=(None, None)):
            got = telemetry.refresh_snapshot(
                self.home, self.session, self.codex_home,
                self.NOW + 61)
        self.assertTrue(got["stale"])
        self.assertEqual(got["account_alias"], "demo")
        self.assertEqual(got["weekly"]["remaining_percent"], 88)

    def test_invalid_weekly_never_becomes_fake_zero(self):
        snap = telemetry.build_snapshot(
            self._account(),
            {"rateLimits": {
                "primary": {"usedPercent": 10,
                            "windowDurationMins": 300},
                "secondary": {"usedPercent": float("nan"),
                              "windowDurationMins": 10080},
            }},
            self.NOW,
        )
        self.assertIsNone(snap["weekly"])
        self.assertEqual(telemetry.format_weekly(snap, self.NOW), "—")


class PublisherLifecycleTests(unittest.TestCase):
    def test_session_start_and_clear_dedup_inside_small_window(self):
        home = "/fakehome"
        session = "thr_dedup"
        codex_home = "/fakehome/.codex"
        fresh = {"schema": 1, "last_attempt_at": 100.0}
        with mock.patch.object(publisher.telemetry, "load_snapshot",
                               return_value=fresh), \
             mock.patch.object(publisher.telemetry, "refresh_snapshot") as ref:
            got = publisher._refresh_if_due(
                home, session, codex_home, "clear", 103.0)
        self.assertEqual(got, fresh)
        ref.assert_not_called()

    def test_working_event_does_not_fetch_twice_inside_interval(self):
        snap = {"schema": 1, "last_attempt_at": 100.0}
        with mock.patch.object(publisher.telemetry, "load_snapshot",
                               return_value=snap), \
             mock.patch.object(publisher.telemetry, "refresh_snapshot") as ref:
            publisher._refresh_if_due(
                "/h", "thr", "/h/.codex", "working", 159.0)
        ref.assert_not_called()

    def test_idle_event_never_fetches_quota(self):
        snap = {"schema": 1, "last_attempt_at": 1.0}
        with mock.patch.object(publisher.telemetry, "load_snapshot",
                               return_value=snap), \
             mock.patch.object(publisher.telemetry, "refresh_snapshot") as ref:
            publisher._refresh_if_due(
                "/h", "thr", "/h/.codex", "state_changed", 999.0)
        ref.assert_not_called()

    def test_publisher_works_without_launcher_profile_token(self):
        pane = {
            "agent": "codex",
            "agent_status": "idle",
            "agent_session": {
                "source": "herdr:codex",
                "agent": "codex",
                "kind": "id",
                "value": "thr_generic",
            },
            "tokens": {},
        }
        snapshot = {
            "schema": 1,
            "account_alias": "demo",
            "plan": "PLUS",
            "weekly": {
                "remaining_percent": 80,
                "duration_minutes": 10080,
                "reset_at": 2_000_000_000,
            },
            "last_attempt_at": 1_000_000_000,
        }
        env = {
            "HOME": "/fakehome",
            "HERDR_PANE_ID": "pane-1",
            "HERDR_SOCKET_PATH": "/fakesock",
        }
        reported = []
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(publisher, "pane_get", return_value=pane), \
             mock.patch.object(publisher, "resolve_launcher_home",
                               return_value=None), \
             mock.patch.object(
                 publisher.telemetry, "resolve_codex_home",
                 return_value="/fakehome/.codex"), \
             mock.patch.object(
                 publisher.telemetry, "load_snapshot",
                 return_value=snapshot), \
             mock.patch.object(
                 publisher, "report_metadata",
                 side_effect=lambda *a: reported.append(a)):
            ok = publisher.publish_once(
                reason="state_changed", start_worker=False,
                now_epoch=1_000_000_010)
        self.assertTrue(ok)
        self.assertEqual(reported[-1][4]["cen_codex_identity"],
                         "demo · PLUS")
        self.assertNotIn("cen_codex_profile", reported[-1][4])
        self.assertIsNone(reported[-1][4]["cen_codex_window_1"])

    def test_stale_session_cannot_publish_after_pane_switch(self):
        pane = {"agent": "codex", "agent_status": "idle",
                "agent_session": {"source": "herdr:codex",
                                  "agent": "codex", "kind": "id",
                                  "value": "thr_new"}, "tokens": {}}
        with mock.patch.dict(os.environ, {
                "HOME": "/h", "HERDR_PANE_ID": "p",
                "HERDR_SOCKET_PATH": "/s",
                "CEN_CODEX_SESSION_ID": "thr_old"}, clear=False), \
             mock.patch.object(publisher, "pane_get", return_value=pane), \
             mock.patch.object(publisher.telemetry, "resolve_codex_home",
                               return_value=None), \
             mock.patch.object(publisher, "report_metadata") as report:
            publisher.publish_once(start_worker=False)
        # Hook races are fail-closed unless a profile can prove ownership.
        self.assertEqual(report.call_args.args[4]["cen_codex_identity"], "—")


class SessionHookTests(unittest.TestCase):
    def test_clear_writes_actual_profile_and_triggers_once(self):
        with tempfile.TemporaryDirectory() as home:
            payload = json.dumps({
                "hook_event_name": "SessionStart",
                "session_id": "thr_clear",
                "source": "clear",
            })
            fake_stdin = mock.Mock()
            fake_stdin.read.return_value = payload
            proc_calls = []
            env = {
                "HOME": home,
                "CODEX_HOME": os.path.join(home, "alt-codex"),
                "HERDR_PANE_ID": "pane-1",
            }
            with mock.patch.dict(os.environ, env, clear=True), \
                 mock.patch.object(session_hook.sys, "stdin", fake_stdin), \
                 mock.patch.object(
                     session_hook.os.path, "isfile", return_value=True), \
                 mock.patch.object(
                     session_hook.subprocess, "Popen",
                     side_effect=lambda *a, **kw: proc_calls.append((a, kw))):
                self.assertEqual(session_hook.main(), 0)
            self.assertEqual(len(proc_calls), 1)
            child_env = proc_calls[0][1]["env"]
            self.assertEqual(child_env["CEN_CODEX_REFRESH_REASON"], "clear")
            self.assertEqual(child_env["CEN_CODEX_SESSION_ID"], "thr_clear")
            self.assertEqual(
                telemetry.read_session_mapping(home, "thr_clear"),
                os.path.realpath(env["CODEX_HOME"]),
            )


class StatusIdentityPrivacyTests(unittest.TestCase):
    """Full email must never render at any terminal width."""

    def acc(self, email="demo@example.invalid", plan="plus",
            acc_type="chatgpt"):
        return {"account": {"type": acc_type, "email": email,
                            "planType": plan}}

    def test_narrow_terminal_renders_alias_only(self):
        self.assertEqual(
            status.format_identity(self.acc(), term_width=40), "demo · PLUS")

    def test_wide_terminal_renders_alias_only(self):
        self.assertEqual(
            status.format_identity(self.acc(), term_width=120), "demo · PLUS")

    def test_extremely_wide_terminal_renders_alias_only(self):
        out = status.format_identity(self.acc(), term_width=500)
        self.assertEqual(out, "demo · PLUS")
        self.assertNotIn("@", out)

    def test_plan_suffix_preserved(self):
        out = status.format_identity(self.acc(), term_width=200)
        self.assertIn("PLUS", out)
        self.assertTrue(out.startswith("demo"))

    def test_control_characters_sanitized(self):
        out = status.format_identity(
            self.acc(email="de\x1b[31mmo@example.invalid"), term_width=200)
        self.assertNotIn("\x1b", out)
        self.assertNotIn("@", out)

    def test_malformed_missing_email_fails_closed(self):
        for bad in (None, "", "   ", {"x": 1}, 123):
            with self.subTest(bad=bad):
                email = bad if isinstance(bad, str) or bad is None else None
                acc = {"account": {"type": "chatgpt", "email": email,
                                  "planType": "plus"}}
                if bad is not None and not isinstance(bad, str):
                    acc = {"account": {"type": "chatgpt",
                                      "email": bad, "planType": "plus"}}
                out = status.format_identity(acc, term_width=200)
                self.assertNotIn("@", out)

    def test_apikey_label_retained(self):
        out = status.format_identity(
            self.acc(email="anything@example.invalid",
                     acc_type="apikey"), term_width=200)
        self.assertEqual(out, "API KEY · PLUS")
        self.assertNotIn("@", out)

    def test_telemetry_snapshot_contains_no_at(self):
        snap = telemetry.build_snapshot(
            self.acc(), {"rateLimitsByLimitId": {"codex": {
                "primary": {"usedPercent": 10, "windowDurationMins": 300},
                "secondary": {"usedPercent": 20, "windowDurationMins": 10080,
                              "resetsAt": 2000000000}}}},
            now_epoch=1000000000)
        self.assertNotIn("@", json.dumps(snap))

    def test_publisher_identity_contains_no_at(self):
        snap = telemetry.build_snapshot(
            self.acc(), {"rateLimitsByLimitId": {"codex": {
                "secondary": {"usedPercent": 20, "windowDurationMins": 10080,
                              "resetsAt": 2000000000}}}},
            now_epoch=1000000000)
        tok = publisher.display_tokens(snap, now_epoch=1000000010)
        self.assertNotIn("@", tok["cen_codex_identity"])

    def test_status_line_contains_no_at(self):
        line = status.format_status(
            self.acc(), {"rateLimitsByLimitId": {"codex": {
                "secondary": {"usedPercent": 20, "windowDurationMins": 10080,
                              "resetsAt": 2000000000}}}},
            term_width=200, now_epoch=1000000000)
        self.assertNotIn("@", line)


class PublisherPostFetchRaceTests(unittest.TestCase):
    """Post-fetch native-session revalidation: an old session snapshot must
    never be published after the pane moves on. Synthetic only."""

    def pane(self, session, agent="codex"):
        return {
            "agent": agent,
            "agent_status": "idle",
            "agent_session": {
                "source": "herdr:codex",
                "agent": "codex",
                "kind": "id",
                "value": session,
            },
            "tokens": {},
        }

    def snapshot(self, alias="demo"):
        return {
            "schema": 1,
            "account_alias": alias,
            "plan": "PLUS",
            "weekly": {"remaining_percent": 80, "duration_minutes": 10080,
                       "reset_at": 2000000000},
            "last_attempt_at": 1000000000,
        }

    def run_publish(self, first, second, env_extra=None):
        env = {"HOME": "/fakehome", "HERDR_PANE_ID": "pane-1",
               "HERDR_SOCKET_PATH": "/fakesock"}
        env.update(env_extra or {})
        reported = []
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(publisher, "pane_get",
                               side_effect=[first, second]), \
             mock.patch.object(publisher.telemetry, "resolve_codex_home",
                               return_value="/fakehome/.codex"), \
             mock.patch.object(publisher.telemetry, "refresh_snapshot",
                               return_value=self.snapshot("old")), \
             mock.patch.object(publisher.telemetry, "load_snapshot",
                               return_value=None), \
             mock.patch.object(publisher, "_try_lock", return_value=True), \
             mock.patch.object(publisher, "_unlock", return_value=None), \
             mock.patch.object(publisher, "report_metadata",
                               side_effect=lambda *a: reported.append(a)):
            ok = publisher.publish_once(reason="working", start_worker=False)
        return ok, reported

    def test_stable_session_reports(self):
        ok, reported = self.run_publish(self.pane("thr_a"), self.pane("thr_a"))
        self.assertTrue(ok)
        self.assertEqual(reported[-1][4]["cen_codex_identity"], "old · PLUS")

    def test_session_switch_suppresses_old_snapshot(self):
        ok, reported = self.run_publish(
            self.pane("thr_old"), self.pane("thr_new"))
        self.assertFalse(ok)
        self.assertEqual(reported, [])

    def test_agent_switch_suppresses_snapshot(self):
        ok, reported = self.run_publish(
            self.pane("thr_old"), self.pane("thr_old", agent="agy"))
        self.assertFalse(ok)
        self.assertEqual(reported, [])

    def test_vanished_pane_suppresses_snapshot(self):
        ok, reported = self.run_publish(self.pane("thr_old"), None)
        self.assertFalse(ok)
        self.assertEqual(reported, [])

    def test_hook_env_pin_resolves_initial_session(self):
        pane = self.pane("thr_hook")
        with mock.patch.dict(os.environ, {"CEN_CODEX_SESSION_ID": "thr_hook"},
                             clear=False):
            self.assertEqual(publisher._session_id(pane), "thr_hook")
            self.assertEqual(publisher._native_pane_session_id(pane),
                             "thr_hook")

    def test_native_helper_ignores_env_pin(self):
        pane = self.pane("thr_native")
        with mock.patch.dict(os.environ, {"CEN_CODEX_SESSION_ID": "thr_stale"},
                             clear=False):
            self.assertEqual(publisher._native_pane_session_id(pane),
                             "thr_native")

    def test_worker_child_env_unpinned(self):
        seen = {}

        def fake_popen(*a, **kw):
            seen.update(kw.get("env", {}))
            raise OSError("stop here")

        env = {"CEN_CODEX_SESSION_ID": "thr_old",
               "CEN_CODEX_REFRESH_REASON": "clear", "HOME": "/h",
               "HERDR_PANE_ID": "p"}
        with mock.patch.dict(os.environ, env, clear=False), \
             mock.patch.object(publisher.subprocess, "Popen",
                               side_effect=fake_popen):
            publisher._spawn_worker()
        self.assertNotIn("CEN_CODEX_SESSION_ID", seen)
        self.assertNotIn("CEN_CODEX_REFRESH_REASON", seen)

    def test_launcher_tag_fallback_compatible(self):
        import launcher as lch
        tag = lch.compute_tag("/fakehome/.codex")
        pane_tokens = {"cen_codex_profile": f"{tag}@{os.getpid()}"}
        with mock.patch.object(
                publisher, "_profile_mapping_path",
                return_value="/fakemapping.json"), \
             mock.patch("builtins.open",
                        mock.mock_open(read_data='{"tag": "%s", '
                                                 '"codex_home": "/fakehome/.codex"}'
                                                 % tag)):
            got = publisher.resolve_launcher_home("/fakehome", pane_tokens)
        self.assertEqual(got, os.path.realpath("/fakehome/.codex"))

    def test_retired_tokens_still_cleared(self):
        tok = publisher.display_tokens(self.snapshot())
        self.assertIsNone(tok["cen_codex_window_1"])
        self.assertIsNone(tok["cen_codex_window_2"])
        self.assertIsNone(tok["cen_codex_summary"])


if __name__ == "__main__":
    unittest.main()
