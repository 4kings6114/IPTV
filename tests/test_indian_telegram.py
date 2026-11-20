"""India-only publication and failures must not invent a main playlist update."""

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from iptv_builder.telegram import main


class IndianTelegramTests(unittest.TestCase):
    def run_notification(self, env, report, prior, indian=None):
        values = [report, prior] + ([indian] if indian is not None else [])
        with (
            patch.dict("os.environ", env, clear=True),
            patch("iptv_builder.telegram.read_json", side_effect=values),
            patch("iptv_builder.telegram.send_message") as send,
            patch("iptv_builder.telegram.atomic_write") as write,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(), 0)
            return send, json.loads(write.call_args.args[1])

    def setUp(self):
        self.env = {
            "TG_TOKEN": "synthetic-token",
            "TG_CHAT": "synthetic-chat",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "1",
            "BUILD_JOB_STATUS": "success",
            "PLAYLIST_CHANGED": "false",
            "INDIAN_CHANGED": "true",
            "INDIAN_BUILD_OUTCOME": "success",
        }
        self.report = {"status": "unchanged", "channels": 100}
        self.indian = {"status": "published", "channels": 2, "added": 1, "removed": 0}

    def test_india_only_summary_has_correct_link_and_does_not_claim_main_changed(self):
        send, state = self.run_notification(self.env, self.report, {}, self.indian)
        text = send.call_args.args[2]
        self.assertIn("🇮🇳", text)
        self.assertIn("/main/indian/playlist.m3u", text)
        self.assertNotIn("✅ پلی‌لیست به‌روزرسانی شد", text)
        self.assertEqual(state["last_indian_summary_run"], "owner/repo/123/1")

    def test_failed_india_build_alert_is_deduplicated_and_no_success_link_sent(self):
        self.env.update(INDIAN_CHANGED="false", INDIAN_BUILD_OUTCOME="failure")
        send, state = self.run_notification(self.env, self.report, {})
        self.assertIn("ناموفق", send.call_args.args[2])
        self.assertNotIn("playlist.m3u", send.call_args.args[2])
        self.assertTrue(state["indian_failure_active"])
        send, state = self.run_notification(self.env, self.report, state)
        send.assert_not_called()
        self.assertTrue(state["indian_failure_active"])

    def test_failed_overall_publication_does_not_claim_india_success(self):
        self.env["BUILD_JOB_STATUS"] = "failure"
        send, state = self.run_notification(self.env, {"status": "failed"}, {})
        self.assertNotIn("🇮🇳 پلی‌لیست هندی به‌روزرسانی شد", send.call_args.args[2])
        self.assertNotIn("last_indian_summary_run", state)
