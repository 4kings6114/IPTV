import json
import unittest
from unittest.mock import patch

from iptv_builder.telegram import message_text, send_message
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
            "BUILD_JOB_STATUS": "success",
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
        with patch.dict("os.environ", env, clear=True), patch("iptv_builder.telegram.send_message"):
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
