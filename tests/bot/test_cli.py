import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from iptv_bot.cli import main
from iptv_bot.state import StateStore
from iptv_bot.transport import NetworkError, TelegramError
from tests.bot.helpers import ROOT, TOKEN, process_env, settings


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.envfile = Path(self.temp.name) / "bot.env"
        self.envfile.write_text(
            f"TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_CHAT_IDS=123\nBOT_STATE_DIR={self.temp.name}/state\n",
            encoding="utf-8",
        )
        self.args = ["--env-file", str(self.envfile)]

    def test_check_config_is_offline_and_redacts_credentials(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("iptv_bot.cli.HTTPTransport") as transport,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(main([*self.args, "--check-config"]), 0)
        transport.assert_not_called()
        self.assertNotIn(TOKEN, output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["allowed_chats"], 1)
        self.assertFalse((Path(self.temp.name) / "state").exists())

    def test_missing_configuration_never_contacts_telegram(self):
        self.envfile.write_text("TELEGRAM_BOT_TOKEN=\n", encoding="utf-8")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("iptv_bot.cli.HTTPTransport") as transport,
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(main(self.args), 2)
        transport.assert_not_called()

    def test_bad_config_does_not_echo_secret_value(self):
        self.envfile.write_text(
            f"TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_CHAT_IDS={TOKEN}\n", encoding="utf-8"
        )
        with patch.dict(os.environ, {}, clear=True), redirect_stderr(io.StringIO()) as output:
            self.assertEqual(main(self.args), 2)
        self.assertNotIn(TOKEN, output.getvalue())

    def test_import_offset_is_local_only(self):
        old = Path(self.temp.name) / ".tg_offset"
        old.write_text("72\n")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("iptv_bot.cli.HTTPTransport") as transport,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main([*self.args, "--import-offset", str(old)]), 0)
        transport.assert_not_called()
        config = settings(self.temp.name)
        store = StateStore(Path(self.temp.name) / "state", config.scope)
        self.assertEqual(store.load(), 72)

    def client(self, **options):
        client = Mock()
        client.call.side_effect = lambda method, *a, **k: {
            "getMe": {"id": 123456, "is_bot": True, "username": "ExampleBot"},
            "getWebhookInfo": {"url": ""},
            "setMyCommands": True,
            "deleteWebhook": True,
            **options,
        }[method]
        return client

    def test_webhook_is_not_deleted_implicitly(self):
        client = self.client(getWebhookInfo={"url": "https://secret.invalid/private-webhook"})
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("iptv_bot.cli.TelegramClient", return_value=client),
            patch("iptv_bot.cli.Bot") as bot,
            redirect_stderr(io.StringIO()) as output,
        ):
            self.assertEqual(main(self.args), 2)
        self.assertEqual(
            [call.args[0] for call in client.call.call_args_list], ["getMe", "getWebhookInfo"]
        )
        self.assertNotIn("secret.invalid", output.getvalue())
        bot.assert_not_called()

    def test_explicit_delete_webhook_preserves_pending_updates(self):
        client = self.client()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("iptv_bot.cli.TelegramClient", return_value=client),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main([*self.args, "--delete-webhook"]), 0)
        self.assertEqual(
            client.call.call_args_list[-1].args, ("deleteWebhook", {"drop_pending_updates": False})
        )

    def test_chat_discovery_does_not_reply_acknowledge_or_start_polling(self):
        self.envfile.write_text(
            f"TELEGRAM_BOT_TOKEN={TOKEN}\nBOT_STATE_DIR={self.temp.name}/state\n", encoding="utf-8"
        )
        client = self.client(
            getUpdates=[
                {"message": {"chat": {"id": 123, "type": "private"}}},
                {"message": {"chat": {"id": -100123, "type": "supergroup"}}},
            ]
        )
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("iptv_bot.cli.TelegramClient", return_value=client),
            patch("iptv_bot.cli.Bot") as bot,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(main([*self.args, "--discover-chats"]), 0)
        self.assertEqual(
            [call.args[0] for call in client.call.call_args_list], ["getMe", "getUpdates"]
        )
        payload = client.call.call_args.args[1]
        self.assertNotIn("offset", payload)
        self.assertNotIn("allowed_updates", payload)
        self.assertIn('"chat_id": 123', output.getvalue())
        self.assertNotIn(TOKEN, output.getvalue())
        self.assertEqual(list((Path(self.temp.name) / "state").glob("*/state.json")), [])
        bot.assert_not_called()
        client.send.assert_not_called()

    def test_discovery_does_not_make_missing_allowlist_valid_for_normal_start(self):
        self.envfile.write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\n", encoding="utf-8")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("iptv_bot.cli.TelegramClient") as client,
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(main(self.args), 2)
        client.assert_not_called()

    def test_register_commands_is_explicit_and_does_not_start_polling(self):
        client = self.client()
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("iptv_bot.cli.TelegramClient", return_value=client),
            patch("iptv_bot.cli.Bot") as bot,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main([*self.args, "--register-commands"]), 0)
        self.assertEqual(client.call.call_args_list[-1].args[0], "setMyCommands")
        commands = client.call.call_args_list[-1].args[1]["commands"]
        self.assertIn("search", [value["command"] for value in commands])
        bot.assert_not_called()

    def test_normal_start_does_not_change_webhook_or_commands(self):
        client = self.client()
        bot = Mock()
        bot.run.return_value = 0
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("iptv_bot.cli.TelegramClient", return_value=client),
            patch("iptv_bot.cli.Bot", return_value=bot),
        ):
            self.assertEqual(main(self.args), 0)
        self.assertEqual(
            [call.args[0] for call in client.call.call_args_list], ["getMe", "getWebhookInfo"]
        )
        bot.run.assert_called_once()

    def test_network_or_startup_errors_are_safe(self):
        for failure, expected in (
            (NetworkError(TOKEN), 1),
            (TelegramError(401), 2),
            (RuntimeError(TOKEN), 2),
        ):
            client = Mock()
            client.call.side_effect = failure
            with (
                self.subTest(kind=type(failure)),
                patch.dict(os.environ, {}, clear=True),
                patch("iptv_bot.cli.TelegramClient", return_value=client),
                redirect_stderr(io.StringIO()) as output,
            ):
                self.assertEqual(main(self.args), expected)
            self.assertNotIn(TOKEN, output.getvalue())

    def test_bad_identity_does_not_start_bot(self):
        for identity in (
            {},
            {"id": 999, "is_bot": True, "username": "ExampleBot"},
            {"id": 123456, "is_bot": False, "username": "ExampleBot"},
        ):
            client = self.client(getMe=identity)
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("iptv_bot.cli.TelegramClient", return_value=client),
                patch("iptv_bot.cli.Bot") as bot,
                redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(main(self.args), 1)
            bot.assert_not_called()

    def test_state_directory_argument_overrides_env_file_for_service_safety(self):
        old = Path(self.temp.name) / "old-offset"
        old.write_text("42")
        chosen = Path(self.temp.name) / "explicit-state"
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(io.StringIO()):
            self.assertEqual(
                main([*self.args, "--state-dir", str(chosen), "--import-offset", str(old)]), 0
            )
        self.assertFalse((Path(self.temp.name) / "state").exists())
        self.assertEqual(StateStore(chosen, settings(self.temp.name).scope).load(), 42)

    def test_startup_rate_limit_requires_owner_wait_without_restart_storm(self):
        client = Mock()
        client.call.side_effect = TelegramError(429, retry_after=17)
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("iptv_bot.cli.TelegramClient", return_value=client),
            redirect_stderr(io.StringIO()) as output,
        ):
            self.assertEqual(main(self.args), 2)
        self.assertIn("17", output.getvalue())
        self.assertNotIn(TOKEN, output.getvalue())

    def test_persian_errors_work_even_when_inherited_output_encoding_is_ascii(self):
        env = process_env(PYTHONIOENCODING="ascii", TELEGRAM_CHAT_IDS="")
        result = subprocess.run(
            [sys.executable, str(ROOT / "telegram_bot.py"), *self.args, "--check-config"],
            cwd=self.temp.name,
            env=env,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("خطای تنظیمات", result.stderr.decode("utf-8"))
        self.assertNotIn(TOKEN.encode(), result.stderr)

    def test_importing_entrypoint_does_not_load_config_or_start_network(self):
        with patch("iptv_bot.cli.main") as start:
            import telegram_bot  # noqa: F401
        start.assert_not_called()

    def test_python_entrypoint_supports_offline_preflight(self):
        env = process_env()
        result = subprocess.run(
            [sys.executable, str(ROOT / "telegram_bot.py"), *self.args, "--check-config"],
            cwd=self.temp.name,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX launcher")
    def test_shell_launcher_forwards_args_and_exit_code(self):
        env = process_env(PYTHON=sys.executable)
        result = subprocess.run(
            ["bash", str(ROOT / "run_bot.sh"), *self.args, "--check-config"],
            cwd=self.temp.name,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["configuration"], "valid")
        result = subprocess.run(
            [
                "bash",
                str(ROOT / "run_bot.sh"),
                "--env-file",
                str(self.envfile) + "missing",
                "--check-config",
            ],
            env=env,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 2)

    @unittest.skipUnless(os.name == "nt", "Windows launcher")
    def test_windows_launcher_forwards_args_and_exit_code(self):
        env = process_env(PYTHON=sys.executable)
        command = f'"{ROOT / "run_bot.bat"}" --env-file "{self.envfile}" --check-config'
        result = subprocess.run(
            command,
            shell=True,
            cwd=self.temp.name,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"configuration": "valid"', result.stdout)
