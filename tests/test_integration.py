"""Real CLI + local HTTP tests; all files live in TemporaryDirectory."""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from iptv_builder import catalog as g
from iptv_builder.m3u import parse
from tests.helpers import HLS, ROOT, TS


@contextmanager
def fixture_server():
    state = {"source_down": False, "streams_down": False, "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["requests"].append((self.path, dict(self.headers)))
            if self.path == "/source.m3u":
                if state["source_down"]:
                    self.respond(503, b"maintenance", "text/plain")
                    return
                base = f"http://127.0.0.1:{self.server.server_port}"
                playlist = (
                    '#EXTM3U\n#EXTINF:-1 tvg-id="CNN.us@HD" group-title="News",CNN (1080p)\n'
                    f"{base}/bad\n"
                    '#EXTINF:-1 tvg-id="CNN.us@SD" group-title="News",CNN (480p)\n'
                    "#EXTVLCOPT:http-referrer=https://player.test/\n"
                    f"{base}/live.m3u8\n"
                    '#EXTINF:-1 tvg-id="Kids.us@SD" group-title="Kids",Example Kids\n'
                    f"{base}/kids.master\n"
                    '#EXTINF:-1 tvg-id="CinemaxHits.us@HD" group-title="Music",Cinemax Hits (1080p)\n'
                    f"{base}/movie.m3u8\n"
                )
                self.respond(200, playlist.encode(), "application/x-mpegurl")
            elif self.path == "/bad":
                self.respond(200, b"<html>Not a stream</html>", "text/html")
            elif state["streams_down"]:
                self.respond(503, b"temporary failure", "text/plain")
            elif (
                self.path == "/live.m3u8" and self.headers.get("Referer") != "https://player.test/"
            ):
                self.respond(403, b"missing referer", "text/plain")
            elif self.path == "/kids.master":
                self.respond(
                    200,
                    b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=1280x720\nkids.media\n",
                    "application/x-mpegurl",
                )
            elif self.path in {"/live.m3u8", "/kids.media", "/movie.m3u8", "/farsi.m3u8"}:
                self.respond(200, HLS, "application/x-mpegurl")
            elif self.path == "/segment.ts":
                self.respond(200, TS, "video/mp2t")
            else:
                self.respond(404, b"missing", "text/plain")

        def respond(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = self.root / "output"
        self.server = fixture_server()
        self.base, self.state = self.server.__enter__()
        self.addCleanup(self.server.__exit__, None, None, None)
        self.config_path = self.root / "config.json"
        self.config_path.write_text(
            json.dumps(
                {
                    "stream_template": "",
                    "extra_sources": [{"url": self.base + "/source.m3u", "country": "us"}],
                    "epg_urls": ["https://epg.test/ir.xml", "https://epg.test/us.xml"],
                    "max_workers": 4,
                    "timeout": 2,
                    "source_attempts": 1,
                    "retry_delay": 0,
                    "check_segments": True,
                }
            ),
            encoding="utf-8",
        )
        shutil.copyfile(ROOT / "overrides.json", self.root / "overrides.json")
        (self.root / "manual.m3u").write_text(
            f'#EXTM3U\n#EXTINF:-1 tvg-id="IRIB1.ir@SD" group-title="{g.FA_TV}",IRIB 1\n{self.base}/farsi.m3u8\n',
            encoding="utf-8",
        )

    def cli(self, *args):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "build_playlist.py"),
                "--config",
                str(self.config_path),
                "--output-dir",
                str(self.output),
                *args,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def runtime(self):
        return json.loads((self.output / ".cache/iptv/last_run.json").read_text())

    def published_hashes(self):
        return {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.output.iterdir()
            if path.is_file()
        }

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_full_cli_fallback_metadata_headers_splits_and_no_timestamp_churn(self):
        self.assert_success(self.cli())
        final = parse((self.output / "playlist.m3u").read_text())
        self.assertEqual(len(final), 4)
        self.assertIn(self.base + "/live.m3u8", [item.url for item in final])
        self.assertNotIn(self.base + "/bad", [item.url for item in final])
        movies = parse((self.output / "movies.m3u").read_text())
        self.assertEqual([item.name for item in movies], ["Cinemax Hits (1080p)"])
        self.assertEqual(len(parse((self.output / "persian.m3u").read_text())), 1)
        self.assertEqual(len(parse((self.output / "kids.m3u").read_text())), 1)
        self.assertEqual(len(parse((self.output / "news.m3u").read_text())), 1)
        self.assertEqual(
            (self.output / "playlist.m3u").read_text().splitlines()[0].count("url-tvg="), 1
        )
        hashes = self.published_hashes()
        self.assert_success(self.cli())
        self.assertEqual(self.runtime()["status"], "unchanged")
        self.assertEqual(self.published_hashes(), hashes)
        self.assertTrue(any(path == "/segment.ts" for path, _ in self.state["requests"]))
        self.assertFalse((self.output / "playlist-backup.m3u").exists())

    def test_source_outage_uses_cache_without_losing_channels(self):
        self.assert_success(self.cli())
        playlist = (self.output / "playlist.m3u").read_bytes()
        self.state["source_down"] = True
        self.assert_success(self.cli())
        self.assertEqual(self.runtime()["sources"][0]["status"], "cached")
        self.assertEqual((self.output / "playlist.m3u").read_bytes(), playlist)
        hashes = self.published_hashes()
        self.assert_success(self.cli())
        self.assertEqual(self.runtime()["status"], "unchanged")
        self.assertEqual(self.published_hashes(), hashes)

    def test_repeated_stream_outage_has_grace_then_is_blocked_without_data_loss(self):
        self.assert_success(self.cli())
        self.state["streams_down"] = True
        self.assert_success(self.cli())
        self.assertEqual(self.runtime()["retained_unverified"], 4)
        hashes = self.published_hashes()
        history = (self.output / ".cache/iptv/link-health.json").read_bytes()
        result = self.cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.runtime()["status"], "blocked")
        self.assertEqual(self.published_hashes(), hashes)
        self.assertEqual((self.output / ".cache/iptv/link-health.json").read_bytes(), history)
        self.assertIn("Previous output preserved", result.stderr)
        self.assertFalse((self.output / "playlist-backup.m3u").exists())

    def test_dry_run_checks_real_http_but_does_not_publish(self):
        self.assert_success(self.cli("--dry-run"))
        self.assertEqual(self.runtime()["status"], "dry_run")
        self.assertFalse((self.output / "playlist.m3u").exists())
        self.assertFalse((self.output / ".cache/iptv/link-health.json").exists())
        self.assertTrue(self.state["requests"])

    def test_first_build_with_no_sources_and_no_cache_is_rejected(self):
        self.state["source_down"] = True
        result = self.cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.runtime()["status"], "blocked")
        self.assertFalse((self.output / "playlist.m3u").exists())
