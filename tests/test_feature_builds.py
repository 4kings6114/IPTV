import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from iptv_builder.builder import BuildError, run_build
from iptv_builder.config import EPGOptions, PlaybackOptions, Source, load_settings
from iptv_builder.health import ProbeResult
from iptv_builder.m3u import parse, render_m3u
from iptv_builder.sources import SourceResult
from iptv_builder.storage import read_json
from tests.helpers import Response, channel, settings
from tests.test_resources_metadata_epg import xmltv


class FeatureBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = Source("https://source.test/playlist", country="us")
        self.config = settings(extra_sources=(self.source,), max_drop_ratio=1)

    def build(self, channels, config=None, dry_run=False, status="healthy"):
        def checks(candidates, settings, previous):
            return {item.probe_key: ProbeResult(status, "fixture") for item in candidates}

        with (
            patch(
                "iptv_builder.builder.fetch_sources",
                return_value=[SourceResult(self.source, render_m3u(channels), "fresh")],
            ),
            patch("iptv_builder.builder.check_candidates", side_effect=checks),
            redirect_stdout(io.StringIO()),
        ):
            return run_build(config or self.config, self.root, dry_run=dry_run)

    def test_public_reports_do_not_churn_when_only_observation_times_change(self):
        item = channel()
        self.build([item])
        paths = [
            self.root / name
            for name in (
                "playlist.m3u",
                "build_report.json",
                "build_stats.txt",
                "index.html",
                "favorites.m3u",
            )
        ]
        before = {path: path.read_bytes() for path in paths}
        result = self.build([item])
        self.assertEqual(result["status"], "unchanged")
        self.assertTrue(all(path.read_bytes() == content for path, content in before.items()))
        self.assertNotEqual(
            result["generated_at"], read_json(self.root / "build_report.json")["generated_at"]
        )
        self.assertEqual(result["checked_links"], 1)
        self.assertEqual(
            read_json(self.root / ".cache/iptv/link-health.json")["links"][item.probe_key][
                "consecutive_successes"
            ],
            2,
        )

    def test_build_reports_url_swap_without_fake_new_channel(self):
        old = channel(url="https://s.test/zold")
        new = channel(url="https://s.test/anew")
        self.build([old])
        result = self.build([new])
        self.assertEqual((result["added"], result["removed"], result["changed_urls"]), (0, 0, 1))
        self.assertEqual(parse((self.root / "playlist.m3u").read_text())[0].url, new.url)

    def test_removed_then_returning_channel_is_recovered_not_new(self):
        first = channel(tvg_id="One.us", url="https://s.test/one")
        second = channel(tvg_id="Two.us", url="https://s.test/two")
        self.build([first, second])
        self.assertEqual(self.build([second])["removed"], 1)
        result = self.build([first, second])
        self.assertEqual((result["added"], result["recovered"]), (0, 1))

    def test_retired_managed_profile_is_cleared_without_touching_unmanaged_files(self):
        profiles = self.root / "profiles.json"
        profiles.write_text('{"profiles":{"example":{}}}')
        self.build([channel()])
        self.assertTrue(parse((self.root / "profile-example.m3u").read_text()))
        unrelated = self.root / "profile-user-owned.m3u"
        unrelated.write_text("user-owned data")
        profiles.write_text('{"profiles":{}}')
        self.build([channel()])
        self.assertFalse(parse((self.root / "profile-example.m3u").read_text()))
        self.assertEqual(unrelated.read_text(), "user-owned data")
        self.assertEqual(self.build([channel()])["status"], "unchanged")

    def test_dry_and_blocked_builds_never_advance_playback_history(self):
        config = replace(self.config, playback=PlaybackOptions(enabled=True))
        with patch(
            "iptv_builder.playback.sample", return_value={"status": "decoded", "reason": "fixture"}
        ):
            self.build([channel()], config)
            path = self.root / ".cache/iptv/playback.json"
            original = path.read_bytes()
            self.build([channel()], config, dry_run=True)
            self.assertEqual(path.read_bytes(), original)
            with self.assertRaises(BuildError):
                self.build([channel()], replace(config, min_channels=999))
            self.assertEqual(path.read_bytes(), original)

    @patch("iptv_builder.resources.urlopen")
    def test_epg_aliases_apply_to_main_and_profiles_without_identity_churn(self, opened):
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        opened.side_effect = lambda *args, **kwargs: Response(
            xmltv(start=now - timedelta(hours=1), stop=now + timedelta(hours=1))
        )
        (self.root / "epg_aliases.json").write_text('{"channels":{"Example.us":"guide.one"}}')
        (self.root / "favorites.json").write_text('{"channels":["Example.us"]}')
        (self.root / "profiles.json").write_text('{"profiles":{"capped":{"max_resolution":720}}}')
        config = replace(
            self.config, epg=EPGOptions(enabled=True), epg_urls=("https://guide.test/tv.xml",)
        )
        self.build([channel()], config)
        for filename in ("playlist.m3u", "favorites.m3u", "profile-capped.m3u"):
            item = parse((self.root / filename).read_text())[0]
            self.assertEqual(item.identity, channel().identity)
            self.assertEqual(item.attrs["tvg-id"], "guide.one")
        result = self.build([channel()], config)
        self.assertEqual((result["added"], result["removed"], result["changed_urls"]), (0, 0, 0))
        self.assertEqual(result["status"], "unchanged")


class FeatureConfigTests(unittest.TestCase):
    def load(self, value):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(value))
            return load_settings(path)

    def test_nested_options_are_strict_and_bounded(self):
        cases = [
            {"metadata": []},
            {"metadata": {"url": "typo"}},
            {"metadata": {"urls": ["file:///tmp/x"]}},
            {"metadata": {"cache_hours": 1, "refresh_hours": 2}},
            {"epg": {"enabled": 1}},
            {"epg": {"max_bytes": 10}},
            {"epg": {"aliases_file": "bad\nfile"}},
            {"playback": {"seconds": 31}},
            {"playback": {"max_channels": 101}},
            {"playback": {"timeout": 121}},
            {"playback": {"ffmpeg": ""}},
            {"host_limit": {"concurrent": True}},
            {"host_limit": {"interval": float("nan")}},
            {"health_cache": {"hours": 25, "max_hours": 24}},
            {"health_cache": {"successes_per_step": 0}},
            {"preferred_sources": "host.test"},
            {"preferred_sources": ["bad host"]},
            {"personalization": {"profiles_file": 5}},
            {"dashboard": "true"},
        ]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ValueError):
                self.load(item)
        config = self.load(
            {"metadata": {"urls": ["https://api.test/channels.json"]}, "playback": {"ffprobe": ""}}
        )
        self.assertEqual(config.metadata.urls, ("https://api.test/channels.json",))
        self.assertEqual(config.playback.ffprobe, "")

    def test_cli_force_checks_and_playback_leave_guards_enabled(self):
        import build_playlist

        with patch("build_playlist.run_build") as run:
            self.assertEqual(build_playlist.main(["--force-check", "--playback-checks"]), 0)
            config = run.call_args.args[0]
            self.assertEqual(config.health_cache.hours, 0)
            self.assertTrue(config.playback.enabled)
            self.assertEqual(config.max_drop_ratio, 0.3)
