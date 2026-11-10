import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from iptv_bot.config import ENV_KEYS
from tests.bot.helpers import ENV, ROOT, TOKEN
from tools.package_telegram_bot import package


class PackageTests(unittest.TestCase):
    def test_archive_is_deterministic_and_uses_an_explicit_safe_allowlist(self):
        with tempfile.TemporaryDirectory() as root:
            first = package(Path(root) / "first.zip")
            second = package(Path(root) / "second.zip")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                names = archive.namelist()
                self.assertIn("iptv-telegram-bot/.env.example", names)
                self.assertIn("iptv-telegram-bot/iptv_bot/cli.py", names)
                self.assertIn("iptv-telegram-bot/run_bot.bat", names)
                self.assertTrue(all(name.startswith("iptv-telegram-bot/") for name in names))
                self.assertFalse(
                    any("/.env" in name and not name.endswith("/.env.example") for name in names)
                )
                self.assertFalse(
                    any(
                        ".cache/" in name or ".tg_offset" in name or "__pycache__" in name
                        for name in names
                    )
                )
                sample = archive.read("iptv-telegram-bot/.env.example").decode()
                self.assertIn("TELEGRAM_BOT_TOKEN=\n", sample)
                self.assertNotIn(TOKEN, sample)

    def test_extracted_bot_runs_without_builder_dependencies(self):
        with tempfile.TemporaryDirectory(prefix="bot package ") as root:
            archive = package(Path(root) / "bot.zip")
            with zipfile.ZipFile(archive) as source:
                source.extractall(root)
            extracted = Path(root) / "iptv-telegram-bot"
            self.assertFalse((extracted / "iptv_builder").exists())
            env = {key: value for key, value in os.environ.items() if key not in ENV_KEYS}
            env.update(ENV)
            result = subprocess.run(
                [sys.executable, str(extracted / "telegram_bot.py"), "--check-config"],
                env=env,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('"configuration": "valid"', result.stdout)
            self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_archive_cannot_overwrite_a_source_file(self):
        with self.assertRaises(ValueError):
            package(ROOT / "telegram_bot.py")
