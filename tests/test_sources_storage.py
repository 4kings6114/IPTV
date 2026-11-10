import io
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from iptv_builder.config import Source
from iptv_builder.m3u import render_m3u
from iptv_builder.sources import cache_path, fetch_source
from iptv_builder.storage import (
    archive_playlist,
    atomic_write,
    build_lock,
    json_text,
    publish_files,
    read_json,
)
from tests.helpers import Response, channel, settings


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name)
        self.source = Source("https://source.test/playlist.m3u", country="us")
        self.playlist = render_m3u([channel()])

    @patch("iptv_builder.sources.urlopen")
    def test_success_cached_then_network_failure_uses_last_good(self, opened):
        opened.return_value = Response(self.playlist.encode())
        self.assertEqual(fetch_source(self.source, settings(), self.cache).status, "fresh")
        opened.side_effect = URLError("offline")
        result = fetch_source(self.source, settings(), self.cache)
        self.assertEqual(result.status, "cached")
        self.assertEqual(result.text, self.playlist)

    @patch("iptv_builder.sources.urlopen")
    def test_html_does_not_overwrite_good_source_cache(self, opened):
        opened.return_value = Response(self.playlist.encode())
        fetch_source(self.source, settings(), self.cache)
        path = cache_path(self.cache, self.source.url)
        old = path.read_bytes()
        opened.side_effect = lambda *args, **kwargs: Response(
            b"<html>temporary error</html>", "text/html"
        )
        self.assertEqual(fetch_source(self.source, settings(), self.cache).status, "cached")
        self.assertEqual(path.read_bytes(), old)

    @patch("iptv_builder.sources.urlopen")
    def test_expired_or_corrupt_cache_does_not_count_as_available(self, opened):
        path = cache_path(self.cache, self.source.url)
        atomic_write(
            path,
            json_text(
                {
                    "url": self.source.url,
                    "text": self.playlist,
                    "fetched_at": (datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),
                }
            ),
        )
        opened.side_effect = URLError("offline")
        self.assertEqual(fetch_source(self.source, settings(), self.cache).status, "failed")
        atomic_write(path, "not json")
        self.assertEqual(fetch_source(self.source, settings(), self.cache).status, "failed")

    @patch("iptv_builder.sources.urlopen")
    def test_404_is_not_retried_and_failed_source_is_explicit(self, opened):
        opened.side_effect = HTTPError(self.source.url, 404, "fixture", {}, io.BytesIO())
        result = fetch_source(self.source, settings(), self.cache)
        self.assertEqual((result.status, result.error), ("failed", "HTTP 404"))
        self.assertEqual(opened.call_count, 1)

    @patch("iptv_builder.sources.urlopen")
    def test_incomplete_http_body_falls_back_to_cache(self, opened):
        from http.client import IncompleteRead

        opened.return_value = Response(self.playlist.encode())
        fetch_source(self.source, settings(), self.cache)
        opened.side_effect = IncompleteRead(b"partial")
        self.assertEqual(fetch_source(self.source, settings(), self.cache).status, "cached")

    @patch("iptv_builder.sources.urlopen")
    def test_success_after_transient_source_error(self, opened):
        opened.side_effect = [URLError("temporary"), Response(self.playlist.encode())]
        self.assertEqual(fetch_source(self.source, settings(), self.cache).status, "fresh")
        self.assertEqual(opened.call_count, 2)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_unchanged_files_are_not_rewritten(self):
        path = self.root / "state.json"
        self.assertTrue(atomic_write(path, "value\n"))
        before = path.stat().st_mtime_ns
        self.assertFalse(atomic_write(path, "value\n"))
        self.assertEqual(path.stat().st_mtime_ns, before)
        self.assertEqual(publish_files(self.root, {"state.json": "value\n"}), [])

    def test_atomic_publication_and_main_is_replaced_last(self):
        import os

        real_replace = os.replace
        order = []

        def replace(source, destination):
            order.append(Path(destination).name)
            return real_replace(source, destination)

        with patch("iptv_builder.storage.os.replace", side_effect=replace):
            changed = publish_files(self.root, {"playlist.m3u": "main", "kids.m3u": "kids"})
        self.assertEqual(changed, ["kids.m3u", "playlist.m3u"])
        self.assertEqual(order, changed)

    def test_failed_replacement_rolls_back_existing_and_new_files(self):
        import os

        real_replace = os.replace
        (self.root / "playlist.m3u").write_text("old-main")
        (self.root / "a.txt").write_text("old-a")
        calls = 0

        def replace(source, destination):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError("simulated disk failure")
            return real_replace(source, destination)

        with patch("iptv_builder.storage.os.replace", side_effect=replace):
            with self.assertRaises(OSError):
                publish_files(
                    self.root, {"a.txt": "new-a", "b.txt": "new-b", "playlist.m3u": "new-main"}
                )
        self.assertEqual((self.root / "playlist.m3u").read_text(), "old-main")
        self.assertEqual((self.root / "a.txt").read_text(), "old-a")
        self.assertFalse((self.root / "b.txt").exists())
        self.assertFalse(list(self.root.glob(".iptv-stage-*")))

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            publish_files(self.root, {"../outside.txt": "bad"})

    def test_snapshots_keep_only_valid_bounded_unique_versions(self):
        archive_playlist(self.root, "<html>bad</html>", 2)
        self.assertFalse(list(self.root.glob("*.m3u")))
        for index in range(4):
            archive_playlist(self.root, render_m3u([channel(name=f"Version {index}")]), 2)
        self.assertEqual(len(list(self.root.glob("*.m3u"))), 2)
        archive_playlist(self.root, render_m3u([channel(name="Version 3")]), 2)
        self.assertEqual(len(list(self.root.glob("*.m3u"))), 2)

    def test_local_lock_prevents_overlapping_builds_and_is_reusable(self):
        with build_lock(self.root):
            with self.assertRaises(RuntimeError):
                with build_lock(self.root):
                    self.fail("second build acquired the lock")
        with build_lock(self.root):
            pass

    def test_corrupt_state_is_safe(self):
        atomic_write(self.root / "state.json", "[1, 2]")
        self.assertEqual(read_json(self.root / "state.json"), {})
