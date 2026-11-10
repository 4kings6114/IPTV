import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iptv_bot.config import ConfigError, load_environment
from iptv_bot.state import StateError, StateStore
from tests.bot.helpers import ENV, ROOT, TOKEN, settings


class ConfigTests(unittest.TestCase):
    def test_default_is_private_and_does_not_echo_token(self):
        value = settings(".")
        self.assertFalse(value.public)
        self.assertEqual(value.chat_ids, {123})
        self.assertNotIn(TOKEN, repr(value))
        self.assertEqual(value.dashboard_url, "https://4kings6114.github.io/IPTV/")

    def test_missing_allowlist_fails_closed(self):
        with self.assertRaises(ConfigError):
            settings(".", TELEGRAM_CHAT_IDS="")
        value = settings(".", TELEGRAM_CHAT_IDS="", BOT_PUBLIC="true")
        self.assertTrue(value.public)

    def test_public_with_private_allowlist_is_rejected(self):
        with self.assertRaises(ConfigError):
            settings(".", BOT_PUBLIC="true")

    def test_legacy_ids_and_group_users(self):
        value = settings(
            ".",
            TELEGRAM_CHAT_IDS="",
            TELEGRAM_CHAT_ID="123,-100123",
            BOT_ALLOWED_USER_IDS="456,789",
        )
        self.assertEqual(value.chat_ids, {123, -100123})
        self.assertEqual(value.user_ids, {456, 789})
        with self.assertRaises(ConfigError):
            settings(".", TELEGRAM_CHAT_ID="456")
        self.assertEqual(settings(".", TELEGRAM_CHAT_ID="123").chat_ids, {123})

    def test_bad_values_are_rejected_without_disclosing_them(self):
        bad = {
            "TELEGRAM_BOT_TOKEN": ["", "PUT_YOUR_BOT_TOKEN_HERE", "secret\nvalue"],
            "TELEGRAM_CHAT_IDS": ["0", "abc", "123,", "9" * 30, "-9223372036854775808"],
            "BOT_ALLOWED_USER_IDS": ["-1", "True"],
            "BOT_PUBLIC": ["yes", "maybe"],
            "IPTV_REPO": ["../bad", "owner/repo/path", "owner/..", "https://x.invalid"],
            "IPTV_BRANCH": ["../main", "foo//bar", "x.lock", "main?token=bad"],
            "IPTV_DASHBOARD_URL": [
                "http://host.invalid",
                "https://user:pass@host.invalid",
                "https://host.invalid/?token=secret",
                "https://host.invalid/#secret",
                "https://host.invalid:bad/",
            ],
            "BOT_POLL_TIMEOUT": ["0", "51", "true", "9" * 100],
            "BOT_HTTP_TIMEOUT": ["0", "61"],
            "BOT_RATE_LIMIT": ["0", "121"],
            "BOT_RATE_WINDOW": ["0", "3601"],
            "BOT_GLOBAL_RATE_LIMIT": ["0", "601"],
            "BOT_STATE_DIR": ["", "bad\0path"],
            "BOT_LOG_LEVEL": ["SECRET_LEVEL"],
        }
        for key, values in bad.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ConfigError):
                    settings(".", **{key: value})

    def test_scope_distinguishes_bots_not_token_rotations(self):
        base = settings(".")
        rotated = settings(".", TELEGRAM_BOT_TOKEN="123456:" + "y" * 35)
        other = settings(".", TELEGRAM_BOT_TOKEN="987654:" + "y" * 35)
        self.assertEqual(base.scope, rotated.scope)
        self.assertNotEqual(base.scope, other.scope)
        self.assertNotIn(TOKEN, base.scope)

    def test_custom_repo_branch_and_relative_state_directory(self):
        value = settings("/tmp/root", IPTV_REPO="owner/repo", IPTV_BRANCH="feature/test")
        self.assertEqual(
            value.raw_base, "https://raw.githubusercontent.com/owner/repo/feature%2Ftest"
        )
        self.assertEqual(value.dashboard_url, "")
        self.assertEqual(value.state_dir, Path("/tmp/root/.cache/telegram-bot"))

    def test_dotenv_is_literal_and_environment_wins(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / ".env"
            path.write_text(
                "\ufeff# comment\nexport TELEGRAM_BOT_TOKEN=\"file-secret\"\nBOT_STATE_DIR='$(touch SHOULD_NOT_EXIST)' # literal\nBOT_PUBLIC=false # comment\n",
                encoding="utf-8",
            )
            value = load_environment(path, {"TELEGRAM_BOT_TOKEN": TOKEN})
            self.assertEqual(value["TELEGRAM_BOT_TOKEN"], TOKEN)
            self.assertEqual(value["BOT_STATE_DIR"], "$(touch SHOULD_NOT_EXIST)")
            self.assertEqual(value["BOT_PUBLIC"], "false")
            self.assertFalse((Path(root) / "SHOULD_NOT_EXIST").exists())

    def test_dotenv_rejects_invalid_duplicate_oversized_and_non_utf8(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / ".env"
            for content in (
                b"UNKNOWN=secret",
                b"BOT_PUBLIC=false\nBOT_PUBLIC=true",
                b"BOT_PUBLIC='unterminated",
                b"#" * 16385,
                b"\xff",
            ):
                path.write_bytes(content)
                with self.subTest(content=content[:25]), self.assertRaises(ConfigError):
                    load_environment(path, {})
            path.unlink()
            self.assertEqual(load_environment(path, ENV), ENV)
            with self.assertRaises(ConfigError):
                load_environment(path, {}, required=True)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = StateStore(Path(self.temp.name), "scope")

    def test_atomic_round_trip_and_namespace(self):
        self.assertEqual(self.store.load(), 0)
        self.store.save(42)
        self.assertEqual(self.store.load(), 42)
        self.assertEqual(StateStore(Path(self.temp.name), "other").load(), 0)
        self.assertEqual(list(self.store.directory.glob(".cursor-*")), [])

    def test_bad_or_corrupt_state_never_silently_resets(self):
        self.store.prepare()
        values = (
            b"",
            b"broken",
            b"[]",
            b"{}",
            b"x" * 4097,
            json.dumps({"schema": True, "next_offset": 0}).encode(),
            json.dumps({"schema": 1, "next_offset": True}).encode(),
            json.dumps({"schema": 1, "next_offset": -1}).encode(),
            json.dumps({"schema": 1, "next_offset": 2**64}).encode(),
        )
        for raw in values:
            self.store.path.write_bytes(raw)
            with self.subTest(raw=raw[:30]), self.assertRaises(StateError):
                self.store.load()

    def test_write_failure_preserves_previous_file_and_cleans_temp(self):
        self.store.save(9)
        with patch("iptv_bot.state.os.replace", side_effect=OSError("failure")):
            with self.assertRaises(StateError):
                self.store.save(10)
        self.assertEqual(self.store.load(), 9)
        self.assertEqual(list(self.store.directory.glob(".cursor-*")), [])
        for value in (-1, True, 2**64):
            with self.assertRaises(StateError):
                self.store.save(value)

    def test_legacy_import_is_explicit_and_cannot_overwrite_new_state(self):
        path = Path(self.temp.name) / ".tg_offset"
        path.write_text("123\n")
        self.store.import_legacy(path)
        self.assertEqual(self.store.load(), 123)
        self.assertTrue(path.exists())
        with self.assertRaises(StateError):
            self.store.import_legacy(path)

    def test_legacy_import_rejects_bad_values(self):
        path = Path(self.temp.name) / "old"
        for content in ("-1", "", "x", "9" * 65, "۱۲۳"):
            path.write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaises(StateError):
                self.store.import_legacy(path)

    def test_lock_blocks_second_process_and_is_released(self):
        code = (
            "import sys; from pathlib import Path; from iptv_bot.state import StateStore, AlreadyRunning\n"
            "try:\n with StateStore(Path(sys.argv[1]), 'scope').lock(): pass\n"
            "except AlreadyRunning: sys.exit(3)\n"
        )
        with self.store.lock():
            child = subprocess.run(
                [sys.executable, "-c", code, self.temp.name],
                cwd=ROOT,
                capture_output=True,
                timeout=15,
            )
            self.assertEqual(child.returncode, 3, child.stderr.decode())
        child = subprocess.run(
            [sys.executable, "-c", code, self.temp.name], cwd=ROOT, capture_output=True, timeout=15
        )
        self.assertEqual(child.returncode, 0, child.stderr.decode())
        self.assertTrue(self.store.lock_path.exists())

    @unittest.skipIf(os.name == "nt", "POSIX file permissions")
    def test_state_and_lock_are_owner_only(self):
        with self.store.lock():
            self.store.save(2)
            self.assertEqual(stat.S_IMODE(self.store.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.store.lock_path.stat().st_mode), 0o600)

    def test_symlink_state_is_rejected(self):
        self.store.prepare()
        target = Path(self.temp.name) / "target"
        target.write_text("private")
        try:
            self.store.path.symlink_to(target)
        except OSError:
            self.skipTest("Symlink creation is unavailable")
        with self.assertRaises(StateError):
            self.store.load()
        with self.assertRaises(StateError):
            self.store.save(1)
        self.assertEqual(target.read_text(), "private")
