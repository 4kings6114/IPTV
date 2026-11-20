"""Offline tests for the independently packaged local bot. Never contact Telegram."""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from local_bot.iptv_bot.app import Bot
from local_bot.iptv_bot.config import (
    Config,
    ConfigurationError,
    atomic_json,
    instance_lock,
    load_config,
    read_json,
)
from local_bot.iptv_bot.network import GitHub, ServiceError, Telegram, request_json, text_chunks
from local_bot.iptv_bot.views import MENU, compact_report, links_text, search, summary
from tests.helpers import ROOT, Response
from tools.package_local_bot import FILES, package

TOKEN = "123456789:" + "synthetic_test_token_not_for_telegram"
SHA_A, SHA_B, SHA_C = "a" * 40, "b" * 40, "c" * 40


def published(name="BBC فارسی"):
    return {
        "channels": 1,
        "generated_at": "2026-11-20T11:00:00Z",
        "added": 1,
        "removed": 0,
        "changed_urls": 2,
        "alternatives": {"channels_with_alternative": 1},
        "channel_catalog": [
            {
                "name": name,
                "identity": "id:bbc.uk",
                "group": "فارسی",
                "status": "healthy",
                "url": "https://s.test/?token=never_cache",
            }
        ],
        "groups": {"فارسی": 1},
        "changes": {"added_channels": [{"name": name, "url": "https://s.test/?token=never_cache"}]},
        "editor": {"private": "never_cache"},
        "personalization": {
            "profiles": {
                "slow": {"file": "profile-slow.m3u", "channels": 1},
                "unsafe": {"file": "../../secret", "channels": 1},
            }
        },
    }


def update(text, chat_id=42, kind="private", update_id=1, user_id=None):
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id, "type": kind},
            "from": {"id": chat_id if user_id is None else user_id},
            "text": text,
        },
    }


class LocalConfigTests(unittest.TestCase):
    def test_token_hidden_from_repr_and_config_round_trip(self):
        config = Config(TOKEN)
        self.assertNotIn(TOKEN, repr(config))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            atomic_json(path, config.data())
            self.assertEqual(load_config(path), config)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_settings_never_accept_arbitrary_urls_or_group_owner(self):
        for change in (
            {"token": "bad"},
            {"repository": "https://evil.test/owner/repo"},
            {"repository": "owner/.."},
            {"branch": "../main"},
            {"branch": "main?token=x"},
            {"owner_chat_id": -100},
            {"owner_chat_id": True},
            {"poll_seconds": 10},
            {"poll_seconds": 300.0},
        ):
            with self.subTest(change=change), self.assertRaises(ConfigurationError):
                Config(**{**Config(TOKEN).data(), **change})

    def test_invalid_or_oversize_local_data_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            self.assertEqual(read_json(path), {})
            for value in ("not-json", "[]", "{" + " " * 20):
                path.write_text(value)
                with self.assertRaises(ConfigurationError):
                    read_json(path, maximum=10)

    def test_local_lock_is_exclusive_and_reusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bot.lock"
            with instance_lock(path):
                with self.assertRaises(ConfigurationError):
                    with instance_lock(path):
                        pass
            with instance_lock(path):
                pass


class LocalNetworkTests(unittest.TestCase):
    def test_request_is_post_json_bounded_and_without_parse_mode(self):
        opened = Mock()
        opened.open.return_value = Response(b'{"ok":true,"result":{}}')
        with patch("local_bot.iptv_bot.network.build_opener", return_value=opened):
            Telegram(TOKEN).send(42, "سلام <script> &", MENU)
        request = opened.open.call_args.args[0]
        payload = json.loads(request.data)
        self.assertNotIn("parse_mode", payload)
        self.assertEqual(payload["chat_id"], 42)
        self.assertIn("reply_markup", payload)
        self.assertEqual(request.get_method(), "POST")

    def test_errors_never_include_token_or_original_url(self):
        for error in (
            URLError("https://api.telegram.org/bot" + TOKEN),
            HTTPError("https://api.telegram.org/bot" + TOKEN, 409, TOKEN, {}, None),
        ):
            with patch("local_bot.iptv_bot.network.build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(ServiceError) as caught:
                    Telegram(TOKEN).updates(0)
            self.assertNotIn(TOKEN, str(caught.exception))

    def test_response_size_and_invalid_json_are_rejected(self):
        for body in (b"123456789", b"bad-json"):
            with patch("local_bot.iptv_bot.network.build_opener") as opener:
                opener.return_value.open.return_value = Response(body)
                with self.assertRaises(ServiceError):
                    request_json("GitHub", "https://api.github.com/", maximum=8)

    def test_api_rejection_and_retry_after_are_checked(self):
        with patch(
            "local_bot.iptv_bot.network.request_json",
            return_value={
                "ok": False,
                "error_code": 429,
                "parameters": {"retry_after": 120},
            },
        ):
            with self.assertRaises(ServiceError) as caught:
                Telegram(TOKEN).call("getMe", {})
            self.assertEqual(caught.exception.retry_after, 120)

    def test_http_429_honors_telegram_json_retry_after(self):
        error = HTTPError(
            "https://api.telegram.org/bot" + TOKEN,
            429,
            "limited",
            {},
            io.BytesIO(b'{"ok":false,"parameters":{"retry_after":120}}'),
        )
        with patch("local_bot.iptv_bot.network.build_opener") as opener:
            opener.return_value.open.side_effect = error
            with self.assertRaises(ServiceError) as caught:
                Telegram(TOKEN).updates(0)
        self.assertEqual(caught.exception.retry_after, 120)
        self.assertNotIn(TOKEN, str(caught.exception))

    def test_chunking_respects_utf16_telegram_limit(self):
        chunks = list(text_chunks("😀" * 5000))
        self.assertEqual("".join(chunks), "😀" * 5000)
        self.assertTrue(all(len(chunk.encode("utf-16-le")) // 2 < 4096 for chunk in chunks))

    def test_malformed_github_runs_and_commits_are_rejected(self):
        client = GitHub(Config(TOKEN))
        for row in ({"sha": None}, {"sha": []}):
            with patch("local_bot.iptv_bot.network.request_json", return_value=[row]):
                with self.assertRaises(ServiceError):
                    client.commits()
        for row in ({"id": 1, "status": []}, {"id": 1, "status": "completed", "conclusion": []}):
            with patch(
                "local_bot.iptv_bot.network.request_json", return_value={"workflow_runs": [row]}
            ):
                with self.assertRaises(ServiceError):
                    client.latest_run()

    def test_github_pins_report_to_commit_and_uses_raw_media_type(self):
        with patch("local_bot.iptv_bot.network.request_json", return_value=published()) as request:
            GitHub(Config(TOKEN)).report(SHA_A)
        self.assertIn(SHA_A, request.call_args.args[1])
        self.assertEqual(request.call_args.kwargs["maximum"], 32 * 1024 * 1024)
        with self.assertRaises(ServiceError):
            GitHub(Config(TOKEN)).report("main?anything")


class LocalViewTests(unittest.TestCase):
    def test_report_cache_omits_raw_streams_editor_and_unsafe_profile(self):
        report = compact_report(published())
        self.assertNotIn("never_cache", json.dumps(report))
        self.assertEqual(set(report["profiles"]), {"slow"})
        self.assertEqual(report["alternatives"], 1)
        self.assertIn("جایگزین", summary(Config(TOKEN), report, SHA_A))

    def test_persian_search_and_missing_empty_long_queries(self):
        report = compact_report(published("شبکه يک"))
        self.assertIn("1 نتیجه", search(Config(TOKEN), report, "شبکه یک"))
        self.assertIn("پیدا نشد", search(Config(TOKEN), report, "unknown"))
        for query in ("", "x" * 129):
            self.assertIn("۱۲۸", search(Config(TOKEN), report, query))

    def test_bad_report_never_becomes_valid_snapshot(self):
        for data in ({}, [], {"channels": 0}, {**published(), "groups": None}):
            with self.subTest(data=str(data)[:100]), self.assertRaises(ConfigurationError):
                compact_report(data)

    def test_all_playlist_links_are_published_urls_not_localhost(self):
        text = links_text(Config(TOKEN))
        self.assertIn("/main/playlist.m3u", text)
        self.assertIn("/main/playlist-alternatives.m3u", text)
        self.assertIn("/main/playlist-backup.m3u", text)
        self.assertNotIn("localhost", text)


class LocalBotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.telegram = Mock()
        self.github = Mock()
        self.github.latest_run.return_value = {
            "id": 12,
            "status": "completed",
            "conclusion": "success",
            "run_attempt": 1,
        }
        self.github.commits.return_value = [SHA_A]
        self.github.report.return_value = published()
        self.bot = Bot(
            Config(TOKEN, owner_chat_id=42),
            self.root,
            self.telegram,
            self.github,
            pairing_code="local-code",
        )

    def test_strangers_groups_and_sender_mismatch_are_ignored(self):
        for message in (
            update("/start", 99),
            update("/links", 42, "group"),
            update("/links", 42, user_id=99),
        ):
            self.bot.handle_update(message)
        self.telegram.send.assert_not_called()

    def test_pairing_requires_secret_console_code_and_is_persisted(self):
        self.bot.config = replace(self.bot.config, owner_chat_id=None)
        for message in (
            update("/start"),
            update("/start wrong"),
            update("/start 💥"),
            update("/start local-code", kind="group"),
        ):
            self.bot.handle_update(message)
        self.telegram.send.assert_not_called()
        self.bot.handle_update(update("/start local-code"))
        self.assertEqual(load_config(self.root / "config.json").owner_chat_id, 42)
        self.assertEqual(self.bot.pin, "")
        self.bot.handle_update(update("/start local-code", chat_id=99))
        self.assertEqual(self.telegram.send.call_count, 1)

    def test_commands_and_plain_search_use_cached_report(self):
        self.bot.report = compact_report(published())
        self.bot.state["report_sha"] = SHA_A
        for text in (
            "/start",
            "/links",
            "/stats",
            "/search bbc",
            "bbc",
            "/changes",
            "/profiles",
            "/dashboard",
            "/help",
            "/unknown",
        ):
            with self.subTest(text=text):
                self.bot.handle_update(update(text))
                self.assertTrue(self.telegram.send.called)
        self.github.report.assert_not_called()
        self.github.commits.assert_not_called()

    def test_status_failure_does_not_break_telegram_polling(self):
        self.github.latest_run.side_effect = ServiceError("GitHub", 403, 900)
        self.bot.handle_update(update("/status"))
        self.assertIn("دسترس نیست", self.telegram.send.call_args.args[1])

    def test_failed_send_does_not_discard_telegram_update(self):
        self.telegram.updates.return_value = [update("/links", update_id=5)]
        self.telegram.send.side_effect = ServiceError("Telegram", 500)
        with self.assertRaises(ServiceError):
            self.bot.process_updates()
        self.assertEqual(self.bot.state.get("offset", 0), 0)
        self.telegram.send.side_effect = None
        self.bot.process_updates()
        self.assertEqual(read_json(self.root / "state.json")["offset"], 6)

    def test_initial_snapshot_then_no_duplicate_after_restart(self):
        self.bot.check_publications(0)
        self.assertIn("شروع پایش", self.telegram.send.call_args.args[1])
        self.assertEqual(read_json(self.root / "state.json")["last_seen"], SHA_A)
        self.telegram.send.reset_mock()
        bot = Bot(self.bot.config, self.root, self.telegram, self.github)
        bot.check_publications(1000)
        self.telegram.send.assert_not_called()

    def test_corrupt_report_cache_reports_how_to_recover(self):
        atomic_json(self.root / "report.json", {"channels": 1})
        with self.assertRaisesRegex(ConfigurationError, "remove data/report.json"):
            Bot(self.bot.config, self.root, self.telegram, self.github)
        self.assertTrue((self.root / "report.json").exists())

    def test_deleted_search_cache_is_restored_without_duplicate_notification(self):
        self.bot.check_publications(0)
        (self.root / "report.json").unlink()
        self.telegram.send.reset_mock()
        bot = Bot(self.bot.config, self.root, self.telegram, self.github)
        bot.check_publications(300)
        self.telegram.send.assert_not_called()
        self.assertEqual(bot.report["publication_sha"], SHA_A)
        self.assertTrue((self.root / "report.json").is_file())

    def test_pairing_delivery_failure_does_not_claim_owner(self):
        self.bot.config = replace(self.bot.config, owner_chat_id=None)
        self.telegram.send.side_effect = ServiceError("Telegram", 500)
        with self.assertRaises(ServiceError):
            self.bot.handle_update(update("/start local-code"))
        self.assertIsNone(self.bot.config.owner_chat_id)
        self.assertFalse((self.root / "config.json").exists())

    def test_polling_conflict_stops_instead_of_fighting_other_bot(self):
        self.telegram.call.side_effect = [{"id": 1}, {"url": ""}, True]
        self.telegram.updates.side_effect = ServiceError("Telegram", 409)
        self.github.commits.return_value = []
        with self.assertRaisesRegex(ConfigurationError, "stop other instances"):
            self.bot.run()

    def test_new_publications_are_delivered_oldest_first(self):
        self.bot.state["last_seen"] = SHA_A
        self.github.commits.return_value = [SHA_C, SHA_B, SHA_A]
        self.bot.check_publications(0)
        self.assertEqual(self.bot.state["last_seen"], SHA_B)
        self.bot.check_publications(10)
        self.assertEqual(self.bot.state["last_seen"], SHA_C)
        self.assertEqual(
            [call.args[0] for call in self.github.report.call_args_list], [SHA_B, SHA_C]
        )

    def test_send_failure_retains_queue_and_last_publication(self):
        self.bot.state["last_seen"] = SHA_A
        self.github.commits.return_value = [SHA_B, SHA_A]
        self.telegram.send.side_effect = ServiceError("Telegram", 500)
        with self.assertRaises(ServiceError):
            self.bot.check_publications(0)
        stored = read_json(self.root / "state.json")
        self.assertEqual(stored["last_seen"], SHA_A)
        self.assertEqual(stored["pending"], [SHA_B])
        self.assertFalse((self.root / "report.json").exists())

    def test_invalid_download_does_not_replace_cached_report(self):
        self.bot.check_publications(0)
        old = (self.root / "report.json").read_bytes()
        self.github.commits.return_value = [SHA_B, SHA_A]
        self.github.report.return_value = {}
        with self.assertRaises(ConfigurationError):
            self.bot.check_publications(300)
        self.assertEqual((self.root / "report.json").read_bytes(), old)
        self.assertEqual(self.bot.state["last_seen"], SHA_A)

    def test_failed_workflow_alert_is_deduplicated(self):
        self.github.commits.return_value = []
        self.github.latest_run.return_value["conclusion"] = "failure"
        self.bot.check_publications(0)
        self.bot.check_publications(300)
        self.assertEqual(self.telegram.send.call_count, 1)

    def test_missing_history_has_explicit_gap_notice(self):
        self.bot.state["last_seen"] = SHA_A
        self.github.commits.return_value = [SHA_C, SHA_B]
        self.bot.check_publications(0)
        self.assertIn("۲۰", self.telegram.send.call_args_list[0].args[1])
        self.assertEqual(self.bot.state["last_seen"], SHA_B)

    def test_unpaired_bot_makes_no_github_requests(self):
        self.bot.config = replace(self.bot.config, owner_chat_id=None)
        self.bot.check_publications(0)
        self.github.latest_run.assert_not_called()
        self.github.commits.assert_not_called()

    def test_active_webhook_is_not_deleted(self):
        self.telegram.call.side_effect = [{"id": 1}, {"url": "https://hosted.example.test"}]
        with self.assertRaises(ConfigurationError):
            self.bot.run()
        self.assertNotIn(
            "deleteWebhook", [call.args[0] for call in self.telegram.call.call_args_list]
        )


class LocalPackageTests(unittest.TestCase):
    def test_zip_is_reproducible_and_excludes_private_runtime_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            for name in FILES:
                destination = source / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes((ROOT / "local_bot" / name).read_bytes())
            (source / "data/config.json").write_text(TOKEN)
            (source / "data/state.json").write_text("private-state")
            first = root / "one.zip"
            second = root / "two.zip"
            digest = package(first, source)
            self.assertEqual(package(second, source), digest)
            with zipfile.ZipFile(first) as archive:
                self.assertEqual(len(archive.namelist()), len(FILES) + 1)
                self.assertNotIn("IPTV-Local-Bot/data/config.json", archive.namelist())
                manifest = archive.read("IPTV-Local-Bot/MANIFEST.sha256").decode()
                for line in manifest.splitlines():
                    expected, name = line.split("  ", 1)
                    self.assertEqual(
                        hashlib.sha256(archive.read("IPTV-Local-Bot/" + name)).hexdigest(), expected
                    )
                archive.extractall(root / "extracted")
            result = subprocess.run(
                [sys.executable, str(root / "extracted/IPTV-Local-Bot/bot_run.py"), "--help"],
                cwd=tmp,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--setup", result.stdout)
            self.assertNotIn(TOKEN, first.read_bytes().decode("latin1"))

    def test_checked_in_report_can_be_compacted_without_runtime_dependencies(self):
        report = compact_report(json.loads((ROOT / "build_report.json").read_text()))
        self.assertEqual(report["channels"], len(report["channel_catalog"]))
        self.assertLess(len(json.dumps(report)), 8 * 1024 * 1024)
