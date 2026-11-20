"""Isolated Indian publication: dedup, guards, privacy and bot links."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from build_indian import build
from iptv_builder.health import ProbeResult
from iptv_builder.m3u import parse, valid_source
from iptv_builder.privacy import exportable_channel
from local_bot.iptv_bot.config import Config
from local_bot.iptv_bot.views import LINKS, MENU, links_text
from tests.helpers import ROOT


class IndianBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.input = self.path / "input"
        self.output = self.path / "output"
        self.input.mkdir()
        self.config = self.input / "config.json"
        self.config.write_text((ROOT / "indian/config.json").read_text(), encoding="utf-8")
        self.source = self.input / "channels.m3u"
        self.source.write_text(
            "#EXTM3U\n"
            '#EXTINF:-1 tvg-id="Example.in" group-title="فیلم هندی",Example (720p)\nhttps://one.test/live\n'
            '#EXTINF:-1 tvg-id="Example.in" group-title="فیلم هندی",Example (480p)\nhttps://two.test/live\n'
            '#EXTINF:-1 tvg-id="Music.in" group-title="موزیک هندی",Music\nhttps://three.test/live\n',
            encoding="utf-8",
        )

    def fake_results(self, channels, settings, history):
        return {c.probe_key: ProbeResult("healthy", "offline synthetic") for c in channels}

    def run_build(self, **kwargs):
        with patch("iptv_builder.builder.check_candidates", side_effect=self.fake_results):
            with redirect_stdout(io.StringIO()):
                return build(self.config, self.output, **kwargs)

    def test_india_only_build_deduplicates_and_preserves_manual_alternatives(self):
        result = self.run_build()
        primary = parse((self.output / "playlist.m3u").read_text())
        alternatives = parse((self.output / "playlist-alternatives.m3u").read_text())
        self.assertEqual(result["status"], "published")
        self.assertEqual(len(primary), 2)
        self.assertEqual(len({c.identity for c in primary}), len(primary))
        self.assertEqual(len({c.request_url for c in primary}), len(primary))
        self.assertEqual(len(alternatives), 3)
        self.assertTrue(any("[جایگزین]" in c.name for c in alternatives))
        self.assertEqual({c.group for c in primary}, {"فیلم هندی", "موزیک هندی"})
        self.assertFalse((self.path / "playlist.m3u").exists())
        self.assertTrue((self.output / ".cache/iptv/link-health.json").exists())

    def test_sensitive_or_malformed_input_never_replaces_output(self):
        self.run_build()
        before = (self.output / "playlist.m3u").read_bytes()
        for invalid in (
            '#EXTM3U\n#EXTINF:-1 tvg-id="X.in",X\nhttps://s.test/live?token=private\n',
            "#EXTM3U\n#EXTINF:-1,missing\n",
            "bad html",
        ):
            with self.subTest(invalid=invalid[:20]):
                self.source.write_text(invalid, encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.run_build()
                self.assertEqual((self.output / "playlist.m3u").read_bytes(), before)

    def test_empty_result_is_guarded_and_previous_output_kept(self):
        self.run_build()
        before = (self.output / "playlist.m3u").read_bytes()

        def unhealthy(channels, settings, history):
            return {c.probe_key: ProbeResult("http_error", "offline synthetic") for c in channels}

        # Existing healthy links have a one-failure grace period. Use two failed builds.
        with (
            patch("iptv_builder.builder.check_candidates", side_effect=unhealthy),
            redirect_stdout(io.StringIO()),
        ):
            self.run_without_patched_health()
            with self.assertRaises(RuntimeError):
                self.run_without_patched_health()
        # First failure may retain the list, second cannot publish an empty result.
        self.assertEqual((self.output / "playlist.m3u").read_bytes(), before)

    def run_without_patched_health(self):
        return build(self.config, self.output)

    def test_dry_run_does_not_publish(self):
        self.run_build(dry_run=True)
        self.assertFalse((self.output / "playlist.m3u").exists())

    def test_repository_input_excludes_known_secret_patterns(self):
        text = (ROOT / "indian/channels.m3u").read_text(encoding="utf-8")
        self.assertTrue(valid_source(text))
        self.assertEqual(len(parse(text)), 92)
        self.assertTrue(all(exportable_channel(c) for c in parse(text)))
        self.assertEqual(
            json.loads((ROOT / "config.json").read_text())["blocked_countries"].count("in"), 1
        )

    def test_bot_link_and_menu_point_to_separate_playlist(self):
        token = "123456789:" + "synthetic_test_token_not_for_telegram"
        text = links_text(Config(token))
        self.assertIn("/main/indian/playlist.m3u", text)
        self.assertIn("/main/indian/playlist-alternatives.m3u", text)
        self.assertIn(("🇮🇳 هندی", "indian/playlist.m3u"), LINKS)
        self.assertIn("🇮🇳 هندی", [button for row in MENU["keyboard"] for button in row])
