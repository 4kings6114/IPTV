import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from iptv_builder.telegram import main, message_text, send_message
from tests.helpers import Response


class TelegramTests(unittest.TestCase):
    def test_report_has_real_newlines_and_current_stats(self):
        text = message_text(
            {
                "channels": 20,
                "added": 2,
                "removed": 1,
                "retained_unverified": 3,
                "unavailable_links": [{"status": "timeout"}],
                "sources": [{"status": "cached"}],
            },
            "owner/repo",
            "123",
            changed=True,
        )
        self.assertIn("\n", text)
        self.assertNotIn("%0A", text)
        self.assertIn("کانال‌ها: 20", text)
        self.assertIn("https://raw.githubusercontent.com/owner/repo/main/playlist.m3u", text)
        self.assertIn("منابع بازیابی‌شده از کش: 1", text)

    def test_blocked_build_reports_preserved_output_and_run_link(self):
        text = message_text(
            {"status": "blocked", "error": "source unavailable"}, "owner/repo", "123", failed=True
        )
        self.assertIn("خروجی قبلی حفظ شد", text)
        self.assertIn("source unavailable", text)
        self.assertIn("https://github.com/owner/repo/actions/runs/123", text)

    def test_main_skips_missing_secrets_without_requesting_network(self):
        import io
        from contextlib import redirect_stdout

        from iptv_builder.telegram import main

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("iptv_builder.telegram.send_message") as send,
        ):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(), 0)
            send.assert_not_called()

    def test_notification_failure_does_not_print_secret_or_exception_url(self):
        import io
        from contextlib import redirect_stderr

        from iptv_builder.telegram import main

        env = {
            "TG_TOKEN": "private-test-token",
            "TG_CHAT": "chat",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_RUN_ID": "1",
            "BUILD_JOB_STATUS": "failure",
        }
        with (
            patch.dict("os.environ", env, clear=True),
            patch("iptv_builder.telegram.read_json", return_value={}),
        ):
            with (
                patch(
                    "iptv_builder.telegram.send_message", side_effect=RuntimeError(env["TG_TOKEN"])
                ),
                redirect_stderr(io.StringIO()) as output,
            ):
                self.assertEqual(main(), 1)
                self.assertNotIn(env["TG_TOKEN"], output.getvalue())
        with (
            patch.dict("os.environ", env, clear=True),
            patch("iptv_builder.telegram.send_message"),
            patch("iptv_builder.telegram.read_json", return_value={}),
            patch("iptv_builder.telegram.atomic_write"),
        ):
            self.assertEqual(main(), 0)

    @patch("iptv_builder.telegram.urlopen")
    def test_send_uses_json_and_checks_ok_field(self, opened):
        opened.return_value = Response(b'{"ok": true}', "application/json")
        send_message("test-token", "test-chat", "line one\nline two")
        request = opened.call_args.args[0]
        self.assertEqual(json.loads(request.data)["text"], "line one\nline two")
        opened.return_value = Response(b'{"ok": false}', "application/json")
        with self.assertRaises(RuntimeError):
            send_message("test-token", "test-chat", "message")


class PublicationSummaryTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "TG_TOKEN": "synthetic-token",
            "TG_CHAT": "synthetic-chat",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            "BUILD_JOB_STATUS": "success",
            "PLAYLIST_CHANGED": "true",
        }
        self.report = {
            "status": "published",
            "channels": 100,
            "previous_channels": 99,
            "added": 2,
            "removed": 1,
            "changed_urls": 3,
            "alternatives": {"channels_with_alternative": 42},
        }

    def invoke(self, previous=None, error=None):
        with (
            patch.dict("os.environ", self.env, clear=True),
            patch("iptv_builder.telegram.read_json", side_effect=[self.report, previous or {}]),
            patch("iptv_builder.telegram.send_message", side_effect=error) as sent,
            patch("iptv_builder.telegram.atomic_write") as stored,
            redirect_stdout(io.StringIO()) as output,
            redirect_stderr(io.StringIO()) as errors,
        ):
            result = main()
        state = json.loads(stored.call_args.args[1]) if stored.called else None
        return result, sent, state, output.getvalue(), errors.getvalue()

    def test_successful_changed_publication_sends_stats_alternatives_and_run_url(self):
        result, sent, state, output, _ = self.invoke()
        self.assertEqual(result, 0)
        sent.assert_called_once()
        text = sent.call_args.args[2]
        for expected in (
            "✅ پلی‌لیست به‌روزرسانی شد",
            "اضافه: 2 | حذف: 1",
            "تغییر آدرس: 3",
            "شبکه‌های دارای لینک جایگزین: 42",
            "https://raw.githubusercontent.com/owner/repo/main/playlist.m3u",
            "https://raw.githubusercontent.com/owner/repo/main/playlist-alternatives.m3u",
            "https://github.com/owner/repo/actions/runs/123",
        ):
            self.assertIn(expected, text)
        self.assertEqual(state["last_summary_run"], "owner/repo/123/1")
        self.assertIn("accepted", output)
        self.assertLess(len(text), 4096)

    def test_no_change_or_absent_push_confirmation_stays_silent(self):
        for changed in ("false", "", "True"):
            self.env["PLAYLIST_CHANGED"] = changed
            result, sent, _, _, _ = self.invoke()
            self.assertEqual(result, 0)
            sent.assert_not_called()
        del self.env["PLAYLIST_CHANGED"]
        self.invoke()[1].assert_not_called()

    def test_failed_publication_never_announces_local_build_as_published(self):
        self.env["BUILD_JOB_STATUS"] = "failure"
        result, sent, state, _, _ = self.invoke()
        self.assertEqual(result, 0)
        self.assertIn("هشدار هدفمند", sent.call_args.args[2])
        self.assertNotIn("✅", sent.call_args.args[2])
        self.assertNotIn("last_summary_run", state)

    def test_dry_blocked_failed_and_missing_report_do_not_send_success_summary(self):
        for status in ("dry_run", "blocked", "failed", "running", None):
            self.report["status"] = status
            with self.subTest(status=status):
                _, sent, state, _, _ = self.invoke()
                if sent.called:
                    self.assertNotIn("✅", sent.call_args.args[2])
                self.assertNotIn("last_summary_run", state)

    def test_sending_failure_does_not_mark_delivery_and_can_retry(self):
        result, _, state, _, errors = self.invoke(error=RuntimeError(self.env["TG_TOKEN"]))
        self.assertEqual(result, 1)
        self.assertIsNone(state)
        self.assertNotIn(self.env["TG_TOKEN"], errors)
        self.assertEqual(self.invoke()[0], 0)

    def test_same_run_summary_is_deduplicated_but_new_run_can_notify(self):
        _, _, state, _, _ = self.invoke()
        result, sent, next_state, _, _ = self.invoke(state)
        self.assertEqual(result, 0)
        sent.assert_not_called()
        self.assertEqual(next_state["last_summary_run"], state["last_summary_run"])
        self.env["GITHUB_RUN_ID"] = "124"
        self.invoke(next_state)[1].assert_called_once()

    def test_unchanged_run_preserves_last_delivered_summary(self):
        _, _, state, _, _ = self.invoke()
        self.env["PLAYLIST_CHANGED"] = "false"
        self.env["GITHUB_RUN_ID"] = "124"
        _, sent, next_state, _, _ = self.invoke(state)
        sent.assert_not_called()
        self.assertEqual(next_state["last_summary_run"], state["last_summary_run"])

    def test_incident_and_summary_are_combined_without_duplicate_alerts(self):
        self.report.update(channels=70, previous_channels=100, removed=30)
        _, sent, state, _, _ = self.invoke()
        text = sent.call_args.args[2]
        self.assertTrue(text.startswith("⚠️"))
        self.assertIn("✅", text)
        self.assertIn("افت مهم", text)
        self.assertEqual(len(state["active"]), 1)
        self.env["GITHUB_RUN_ID"] = "124"
        _, sent, state, _, _ = self.invoke(state)
        self.assertNotIn("هشدار هدفمند", sent.call_args.args[2])
        self.assertIn("✅", sent.call_args.args[2])
        self.assertEqual(len(state["active"]), 1)

    def test_unchanged_build_can_still_send_new_targeted_incident(self):
        self.env["PLAYLIST_CHANGED"] = "false"
        self.report["watched_failures"] = [{"identity": "id:x.us", "name": "Example"}]
        _, sent, state, _, _ = self.invoke()
        self.assertIn("کانال مهم", sent.call_args.args[2])
        self.assertNotIn("✅", sent.call_args.args[2])
        self.assertEqual(len(state["active"]), 1)
