#!/usr/bin/env python3
"""Focused tests for the standalone Claude StatusLine adapter."""

import io
import json
import os
import unittest
from unittest.mock import patch

from integrations.claude import status
from installer.components.herdr import CLAUDE_ROWS


NOW = 1_700_000_000.0
SESSION = "synthetic-claude-session"


def payload(**overrides):
    value = {
        "session_id": SESSION,
        "model": {"display_name": "Sonnet", "id": "model-id"},
        "context_window": {"remaining_percentage": 71},
        "rate_limits": {
            "five_hour": {"used_percentage": 25, "resets_at": NOW + 42 * 60},
            "seven_day": {
                "used_percentage": 40,
                "resets_at": NOW + 3 * 3600 + 12 * 60,
            },
        },
    }
    value.update(overrides)
    return value


def pane(session=SESSION, agent="claude", source="herdr:claude"):
    return {
        "agent": agent,
        "agent_session": {
            "agent": agent,
            "source": source,
            "value": session,
        },
    }


class NormalizationTests(unittest.TestCase):
    def test_normal_payload_uses_left_percentages_and_countdowns(self):
        native, tokens, published = status.process_payload(
            payload(), now_epoch=NOW, env={})
        self.assertFalse(published)
        self.assertEqual(tokens[status.TOKEN_MODEL_CONTEXT], "Sonnet · CTX 71%")
        self.assertEqual(
            tokens[status.TOKEN_QUOTA],
            "5H 75% ↻42m · 7D 60% ↻3h12",
        )
        self.assertEqual(
            native,
            "CLAUDE │ Sonnet · CTX 71% │ 5H 75% ↻42m · 7D 60% ↻3h12",
        )

    def test_model_display_name_preferred_and_id_fallback(self):
        self.assertEqual(
            status.normalize_model({"model": {
                "display_name": "  Son\nnet  ", "id": "fallback"}}),
            "Son net",
        )
        self.assertEqual(
            status.normalize_model({"model": {
                "display_name": " ", "id": "fallback"}}),
            "fallback",
        )
        self.assertIsNone(status.normalize_model({"model": {}}))

    def test_context_remaining_preferred_and_used_fallback(self):
        self.assertEqual(
            status.normalize_context({"context_window": {
                "remaining_percentage": 12.6, "used_percentage": 99}}),
            13,
        )
        self.assertEqual(
            status.normalize_context({"context_window": {
                "used_percentage": 25}}), 75)
        self.assertIsNone(status.normalize_context({
            "context_window": {"remaining_percentage": "bad",
                                "used_percentage": 25}}))

    def test_percentage_boundaries_and_invalid_values(self):
        for used, expected in ((0, 100), (100, 0), (-10, 100), (150, 0)):
            limits = {"five_hour": {"used_percentage": used}}
            normalized = status.normalize_rate_limits(
                {"rate_limits": limits}, now_epoch=NOW)
            self.assertEqual(normalized["five_hour"]["left"], expected)
        for invalid in (float("nan"), float("inf"), "garbage", None, True):
            normalized = status.normalize_rate_limits(
                {"rate_limits": {
                    "five_hour": {"used_percentage": invalid}}},
                now_epoch=NOW,
            )
            self.assertNotIn("five_hour", normalized)

    def test_spend_limit_is_ignored(self):
        normalized = status.normalize_rate_limits({
            "rate_limits": {
                "spend_limit": {"used_percentage": 5,
                                 "resets_at": NOW + 60},
            },
        }, now_epoch=NOW)
        self.assertEqual(normalized, {})
        native, tokens, _ = status.process_payload(
            {"rate_limits": {
                "spend_limit": {"used_percentage": 5,
                                 "resets_at": NOW + 60},
            }}, now_epoch=NOW, env={})
        self.assertEqual(native, "CLAUDE")
        self.assertIsNone(tokens[status.TOKEN_QUOTA])

    def test_expired_invalid_and_compact_countdowns(self):
        self.assertIsNone(status.format_countdown(NOW, NOW))
        self.assertIsNone(status.format_countdown(NOW - 1, NOW))
        self.assertIsNone(status.format_countdown("bad", NOW))
        self.assertEqual(
            status.format_countdown(NOW + 2 * 86400 + 4 * 3600, NOW),
            "↻2d4h",
        )
        normalized = status.normalize_rate_limits({
            "rate_limits": {
                "five_hour": {"used_percentage": 25},
                "seven_day": {"used_percentage": 40,
                               "resets_at": NOW - 1},
            },
        }, now_epoch=NOW)
        self.assertEqual(status.format_quota(normalized), "5H 75% · 7D 60%")

    def test_custom_provider_suppresses_subscription_quota(self):
        env = {status.CUSTOM_PROVIDER_ROUTE_ENV: "present"}
        native, tokens, _ = status.process_payload(
            payload(), now_epoch=NOW, env=env)
        self.assertEqual(tokens[status.TOKEN_MODEL_CONTEXT],
                         "Sonnet · CTX 71%")
        self.assertIsNone(tokens[status.TOKEN_QUOTA])
        self.assertEqual(native, "CLAUDE │ Sonnet · CTX 71%")

    def test_herdr_rows_reference_only_the_two_statusline_tokens(self):
        row_tokens = {
            entry["token"]
            for row in CLAUDE_ROWS[2:]
            for entry in row
        }
        self.assertEqual(row_tokens, {
            "$" + status.TOKEN_MODEL_CONTEXT,
            "$" + status.TOKEN_QUOTA,
        })


class InputSafetyTests(unittest.TestCase):
    def run_main(self, raw):
        stdin = io.StringIO(raw)
        stdout = io.StringIO()
        with patch.object(status.sys, "stdin", stdin), \
                patch.object(status.sys, "stdout", stdout):
            rc = status.main()
        return rc, stdout.getvalue()

    def test_valid_cli_output_is_compact_and_file_free(self):
        rc, output = self.run_main(json.dumps(payload()))
        self.assertEqual(rc, 0)
        self.assertIn("CLAUDE │ Sonnet · CTX 71%", output)
        self.assertIn("5H 75%", output)

    def test_malformed_non_object_and_oversized_input_fail_open(self):
        for raw in ("{bad", "[]", "null", "1", "x" * (status.MAX_STDIN_BYTES + 1)):
            rc, output = self.run_main(raw)
            self.assertEqual(rc, 0)
            self.assertEqual(output, "")

    def test_missing_and_partial_fields_render_truthful_partial_output(self):
        rc, output = self.run_main(json.dumps({
            "model": {"id": "OnlyModel"},
            "context_window": {},
            "rate_limits": {"five_hour": {"used_percentage": 20}},
        }))
        self.assertEqual(rc, 0)
        self.assertEqual(output, "CLAUDE │ OnlyModel │ 5H 80%\n")
        rc, output = self.run_main(json.dumps({}))
        self.assertEqual(rc, 0)
        self.assertEqual(output, "CLAUDE\n")

    def test_malformed_nested_objects_fail_open_without_traceback(self):
        native, tokens, published = status.process_payload({
            "model": [],
            "context_window": "bad",
            "rate_limits": [],
            "session_id": SESSION,
        }, now_epoch=NOW, env={})
        self.assertEqual(native, "CLAUDE")
        self.assertEqual(tokens, {
            status.TOKEN_MODEL_CONTEXT: None,
            status.TOKEN_QUOTA: None,
        })
        self.assertFalse(published)

    def test_no_persistence_is_created(self):
        before = set(os.listdir(os.getcwd()))
        rc, _ = self.run_main(json.dumps(payload()))
        self.assertEqual(rc, 0)
        self.assertEqual(set(os.listdir(os.getcwd())), before)


class PaneSafetyTests(unittest.TestCase):
    ENV = {
        "HERDR_PANE_ID": "pane-synthetic",
        "HERDR_SOCKET_PATH": "/tmp/herdr-synthetic.sock",
    }

    def run_publish(self, before, after=None, env=None):
        if after is None:
            after = before
        calls = [before, after]
        with patch.object(status, "resolve_socket_path",
                          return_value="/tmp/herdr-synthetic.sock"), \
                patch.object(status, "pane_get", side_effect=calls), \
                patch.object(status, "report_metadata",
                             return_value=True) as report:
            result = status.process_payload(
                payload(), now_epoch=NOW, env=self.ENV if env is None else env)
        return result, report

    def test_matching_claude_pane_and_session_reports(self):
        (_, tokens, published), report = self.run_publish(pane())
        self.assertTrue(published)
        report.assert_called_once()
        self.assertEqual(report.call_args.args[2], status.REPORT_SOURCE)
        self.assertEqual(report.call_args.args[4], tokens)

    def test_wrong_agent_does_not_report(self):
        (_, _, published), report = self.run_publish(pane(agent="codex"))
        self.assertFalse(published)
        report.assert_not_called()

    def test_wrong_session_source_does_not_report(self):
        (_, _, published), report = self.run_publish(
            pane(source="other:claude"))
        self.assertFalse(published)
        report.assert_not_called()

    def test_wrong_native_session_agent_does_not_report(self):
        bad = pane()
        bad["agent_session"]["agent"] = "codex"
        (_, _, published), report = self.run_publish(bad)
        self.assertFalse(published)
        report.assert_not_called()

    def test_session_mismatch_does_not_report(self):
        (_, _, published), report = self.run_publish(pane(session="other"))
        self.assertFalse(published)
        report.assert_not_called()

    def test_pane_switch_before_final_report_is_suppressed(self):
        (_, _, published), report = self.run_publish(
            pane(), after=pane(session="new-session"))
        self.assertFalse(published)
        report.assert_not_called()

    def test_socket_absent_keeps_native_output(self):
        env = dict(self.ENV)
        with patch.object(status, "resolve_socket_path", return_value=None), \
                patch.object(status, "report_metadata") as report:
            native, tokens, published = status.process_payload(
                payload(), now_epoch=NOW, env=env)
        self.assertTrue(native.startswith("CLAUDE │ Sonnet"))
        self.assertIsNotNone(tokens[status.TOKEN_QUOTA])
        self.assertFalse(published)
        report.assert_not_called()

    def test_missing_pane_id_keeps_native_output(self):
        env = {"HERDR_SOCKET_PATH": "/tmp/herdr-synthetic.sock"}
        with patch.object(status, "pane_get") as pane_call, \
                patch.object(status, "report_metadata") as report:
            native, _, published = status.process_payload(
                payload(), now_epoch=NOW, env=env)
        self.assertIn("5H 75%", native)
        self.assertFalse(published)
        pane_call.assert_not_called()
        report.assert_not_called()

    def test_allowed_publication_clears_missing_tokens(self):
        (_, tokens, published), report = self.run_publish(
            pane())
        self.assertTrue(published)
        self.assertIsNotNone(tokens[status.TOKEN_MODEL_CONTEXT])
        self.assertIsNotNone(tokens[status.TOKEN_QUOTA])
        missing = {"session_id": SESSION}
        with patch.object(status, "resolve_socket_path",
                          return_value="/tmp/herdr-synthetic.sock"), \
                patch.object(status, "pane_get", side_effect=[pane(), pane()]), \
                patch.object(status, "report_metadata",
                             return_value=True) as clear_report:
            _, clear_tokens, cleared = status.process_payload(
                missing, now_epoch=NOW, env=self.ENV)
        self.assertTrue(cleared)
        self.assertEqual(clear_tokens, {
            status.TOKEN_MODEL_CONTEXT: None,
            status.TOKEN_QUOTA: None,
        })
        self.assertIsNone(clear_report.call_args.args[4][status.TOKEN_QUOTA])

    def test_custom_provider_can_report_model_context_but_clears_quota(self):
        env = dict(self.ENV)
        env[status.CUSTOM_PROVIDER_ROUTE_ENV] = "present"
        with patch.object(status, "resolve_socket_path",
                          return_value="/tmp/herdr-synthetic.sock"), \
                patch.object(status, "pane_get", side_effect=[pane(), pane()]), \
                patch.object(status, "report_metadata",
                             return_value=True) as report:
            native, tokens, published = status.process_payload(
                payload(), now_epoch=NOW, env=env)
        self.assertTrue(published)
        self.assertEqual(native, "CLAUDE │ Sonnet · CTX 71%")
        self.assertIsNone(tokens[status.TOKEN_QUOTA])
        self.assertIsNone(report.call_args.args[4][status.TOKEN_QUOTA])


if __name__ == "__main__":
    unittest.main()
