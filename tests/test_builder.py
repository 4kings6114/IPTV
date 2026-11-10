import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from iptv_builder.builder import (
    BuildError,
    guard_output,
    run_build,
    select_channels,
    update_history,
)
from iptv_builder.config import Source
from iptv_builder.health import ProbeResult
from iptv_builder.m3u import parse, render_m3u
from iptv_builder.sources import SourceResult
from iptv_builder.storage import atomic_write, read_json
from tests.helpers import channel, settings

OK = ProbeResult("healthy", "valid HLS")
BAD = ProbeResult("http_error", "HTTP 404")


class SelectionTests(unittest.TestCase):
    def test_broken_high_quality_falls_back_to_healthy_low_quality(self):
        high = channel(name="Example (1080p)", url="https://s.test/high", tvg_id="Example.us@HD")
        low = channel(
            name="Different Display Name (480p)", url="https://s.test/low", tvg_id="Example.us@SD"
        )
        result = select_channels(
            [high, low], {high.probe_key: BAD, low.probe_key: OK}, {}, [], settings()
        )
        self.assertEqual([item.url for item in result.channels], [low.url])

    def test_manifest_resolution_beats_unreliable_name_resolution(self):
        high_name = channel(name="Example (1080p)", url="https://s.test/a")
        master = channel(name="Example", url="https://s.test/b")
        results = {
            high_name.probe_key: OK,
            master.probe_key: ProbeResult("healthy", "master", 2160),
        }
        chosen = select_channels([high_name, master], results, {}, [], settings()).channels
        self.assertEqual(chosen[0].url, master.url)

    def test_one_failed_build_has_grace_but_two_do_not(self):
        item = channel()
        results = {item.probe_key: BAD}
        first_history = update_history(results, {}, "2026-01-01T00:00:00+00:00")
        first = select_channels([item], results, first_history, [item], settings())
        self.assertEqual(first.retained_unverified, 1)
        self.assertEqual(len(first.channels), 1)
        second_history = update_history(results, first_history, "2026-01-02T00:00:00+00:00")
        second = select_channels([item], results, second_history, [item], settings())
        self.assertFalse(second.channels)
        # Failed new links never enter the playlist under the grace policy.
        self.assertFalse(select_channels([item], results, first_history, [], settings()).channels)

    def test_healthy_low_quality_beats_unverified_old_high_quality(self):
        high = channel(name="Example (1080p)", url="https://s.test/high")
        low = channel(name="Example (480p)", url="https://s.test/low")
        results = {high.probe_key: BAD, low.probe_key: OK}
        history = update_history(results, {}, "now")
        self.assertEqual(
            select_channels([high, low], results, history, [high], settings()).channels[0].url,
            low.url,
        )

    def test_manual_group_wins_while_healthy_quality_is_still_selected(self):
        automatic = channel(name="Example (1080p)", url="https://s.test/high", group="auto")
        manual = channel(
            name="Example (480p)", url="https://s.test/low", group="custom", manual=True
        )
        results = {automatic.probe_key: OK, manual.probe_key: OK}
        final = select_channels([automatic, manual], results, {}, [], settings()).channels
        self.assertEqual((final[0].url, final[0].group), (automatic.url, "custom"))

    def test_drop_dead_false_is_explicit_unverified_opt_out(self):
        item = channel()
        result = select_channels([item], {item.probe_key: BAD}, {}, [], settings(drop_dead=False))
        self.assertEqual(len(result.channels), 1)
        self.assertEqual(result.retained_unverified, 1)

    def test_regional_feeds_are_not_merged_and_url_collisions_are_deduplicated(self):
        east = channel(tvg_id="Example.us@East", url="https://s.test/east")
        west = channel(tvg_id="Example.us@West", url="https://s.test/west")
        duplicate = channel(tvg_id="Another.us", url=east.url)
        results = {east.probe_key: OK, west.probe_key: OK}
        final = select_channels([east, west, duplicate], results, {}, [], settings()).channels
        self.assertEqual(len(final), 2)
        self.assertEqual(len({item.url for item in final}), 2)

    def test_explicit_language_metadata_wins_over_country_or_source_hints(self):
        from iptv_builder import catalog as g
        from iptv_builder.classification import prepare_channels

        config = settings()
        overrides = {"channels": {}, "names": {}}
        item = channel(name="Abadan TV", tvg_id="AbadanTV.ir@SD")
        country = prepare_channels(
            render_m3u([item]), Source("country", country="ir"), config, overrides, (set(), set())
        )
        persian = prepare_channels(
            render_m3u([item]),
            Source("persian", country="ir", language="fa"),
            config,
            overrides,
            (set(), set()),
        )
        selected = select_channels(
            country + persian, {item.probe_key: OK}, {}, [], config
        ).channels[0]
        self.assertEqual((selected.group, selected.attrs["tvg-language"]), (g.FA_TV, "fa"))
        item = channel(name="Example News", tvg_id="Example.ir@SD")
        english = prepare_channels(
            render_m3u([item.with_attribute("tvg-language", "en")]),
            Source("country", country="ir"),
            config,
            overrides,
            (set(), set()),
        )
        hint = prepare_channels(
            render_m3u([item]),
            Source("persian", country="ir", language="fa"),
            config,
            overrides,
            (set(), set()),
        )
        selected = select_channels(hint + english, {item.probe_key: OK}, {}, [], config).channels[0]
        self.assertEqual((selected.group, selected.attrs["tvg-language"]), (g.EN_NEWS, "en"))

    def test_history_preserves_last_success_and_skipped_checks_do_not_advance_failures(self):
        item = channel()
        healthy = update_history({item.probe_key: OK}, {}, "first")
        failed = update_history({item.probe_key: BAD}, healthy, "second")
        self.assertEqual(failed[item.probe_key]["last_success"], "first")
        self.assertEqual(failed[item.probe_key]["consecutive_failures"], 1)
        unchecked = update_history(
            {item.probe_key: ProbeResult("unchecked", "disabled")}, failed, "third"
        )
        self.assertEqual(unchecked, failed)

    def test_guards_never_allow_empty_and_percentage_limit_is_configurable(self):
        with self.assertRaises(BuildError):
            guard_output(settings(max_drop_ratio=1), 100, 0)
        with self.assertRaises(BuildError):
            guard_output(settings(), 100, 69)
        guard_output(settings(), 100, 70)
        guard_output(settings(max_drop_ratio=1), 100, 1)


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.source = Source("https://source.test/playlist", country="us")
        self.config = settings(extra_sources=(self.source,), check_links=False)
        self.item = channel(group="ignored")

    def build(self, channels=None, config=None, dry_run=False, sources=None):
        if sources is None:
            sources = [SourceResult(self.source, render_m3u(channels or [self.item]), "fresh")]
        with (
            patch("iptv_builder.builder.fetch_sources", return_value=sources),
            redirect_stdout(io.StringIO()),
        ):
            return run_build(config or self.config, self.root, dry_run=dry_run)

    def test_first_build_and_unchanged_second_build(self):
        self.assertEqual(self.build()["status"], "published")
        files = {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}
        mtimes = {
            path.name: path.stat().st_mtime_ns for path in self.root.iterdir() if path.is_file()
        }
        self.assertEqual(self.build()["status"], "unchanged")
        self.assertEqual(
            files, {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}
        )
        self.assertEqual(
            mtimes,
            {path.name: path.stat().st_mtime_ns for path in self.root.iterdir() if path.is_file()},
        )

    def test_large_drop_does_not_touch_playlist_backup_or_health_history(self):
        old = [
            channel(
                name=f"Channel {index}", tvg_id=f"Channel{index}.us", url=f"https://s.test/{index}"
            )
            for index in range(10)
        ]
        atomic_write(self.root / "playlist.m3u", render_m3u(old))
        atomic_write(self.root / "playlist-backup.m3u", "existing backup")
        atomic_write(self.root / ".cache/iptv/link-health.json", '{"links": {}}\n')
        before = {
            path: path.read_bytes()
            for path in [
                self.root / "playlist.m3u",
                self.root / "playlist-backup.m3u",
                self.root / ".cache/iptv/link-health.json",
            ]
        }
        with self.assertRaises(BuildError):
            self.build(channels=[old[0]])
        self.assertTrue(all(path.read_bytes() == data for path, data in before.items()))
        self.assertEqual(read_json(self.root / ".cache/iptv/last_run.json")["status"], "blocked")
        self.assertFalse((self.root / ".cache/iptv/backups").exists())

    def test_unavailable_source_guard_runs_before_link_checks(self):
        failure = SourceResult(self.source, "", "failed", "HTTP 503")
        with patch("iptv_builder.builder.check_candidates") as checked:
            with self.assertRaises(BuildError):
                self.build(sources=[failure])
            checked.assert_not_called()
        self.assertFalse((self.root / "playlist.m3u").exists())

    def test_dry_run_never_changes_published_files_history_or_backups(self):
        self.build()
        before = {
            path: path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file() and path.name != "last_run.json"
        }
        new = channel(name="New Channel", tvg_id="New.us", url="https://s.test/new")
        result = self.build(channels=[self.item, new], dry_run=True)
        self.assertEqual(result["status"], "dry_run")
        self.assertTrue(all(path.read_bytes() == data for path, data in before.items()))
        self.assertFalse((self.root / "playlist-backup.m3u").exists())
        self.assertFalse((self.root / ".cache/iptv/backups").exists())

    def test_backup_is_previous_valid_playlist_and_not_rotated_for_report_only_changes(self):
        self.build()
        old = (self.root / "playlist.m3u").read_bytes()
        new = channel(name="New Channel", tvg_id="New.us", url="https://s.test/new")
        self.build(channels=[self.item, new])
        backup = self.root / "playlist-backup.m3u"
        self.assertEqual(backup.read_bytes(), old)
        mtime = backup.stat().st_mtime_ns
        cached = SourceResult(self.source, render_m3u([self.item, new]), "cached", "HTTP 503")
        self.build(sources=[cached])
        self.assertEqual(backup.stat().st_mtime_ns, mtime)
        self.assertEqual(len(list((self.root / ".cache/iptv/backups").glob("*.m3u"))), 1)

    def test_empty_splits_are_cleared_instead_of_left_stale(self):
        atomic_write(self.root / "kids.m3u", render_m3u([self.item]))
        self.build()
        self.assertFalse(parse((self.root / "kids.m3u").read_text()))

    def test_new_invalid_link_is_not_published(self):
        with patch(
            "iptv_builder.builder.check_candidates", return_value={self.item.probe_key: BAD}
        ):
            with self.assertRaises(BuildError):
                self.build(config=replace(self.config, check_links=True))
        self.assertFalse((self.root / "playlist.m3u").exists())

    def test_removed_and_excluded_channel_is_not_resurrected_from_previous_output(self):
        self.build()
        (self.root / "exclude.txt").write_text("Example\n", encoding="utf-8")
        new = channel(name="New Channel", tvg_id="New.us", url="https://s.test/new")
        self.build(channels=[self.item, new])
        final = parse((self.root / "playlist.m3u").read_text())
        self.assertEqual([item.name for item in final], ["New Channel"])
