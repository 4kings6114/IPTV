import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from iptv_builder import budget
from iptv_builder.builder import BuildError, run_build
from iptv_builder.config import BudgetOptions, Source, load_settings
from iptv_builder.m3u import render_m3u
from iptv_builder.privacy import exportable_channel, public_data, public_url
from iptv_builder.site import stage_site
from iptv_builder.sources import fetch_source
from iptv_builder.storage import json_text, read_json
from tests.helpers import Response, channel, settings
from tests.test_integration import fixture_server


class BudgetTests(unittest.TestCase):
    def test_shared_concurrent_byte_reservations_never_allow_unbounded_reads(self):
        with budget.build_budget(BudgetOptions(max_seconds=10, max_http_bytes=100)) as meter:

            def read(_):
                try:
                    return len(budget.read_response(Response(b"x" * 1000), 1000))
                except budget.BudgetExceeded:
                    return 0

            with ThreadPoolExecutor(max_workers=5) as pool:
                list(pool.map(read, range(5)))
            self.assertEqual(meter.received, 100)
            self.assertLessEqual(meter.used, 100)
            self.assertIn("external ffmpeg", meter.summary()["scope"])

    def test_wall_budget_limits_timeouts_and_retry_delays_without_sleeping_past_deadline(self):
        with budget.build_budget(BudgetOptions(max_seconds=1, max_http_bytes=100)):
            self.assertLessEqual(budget.timeout(30), 1)
            with self.assertRaises(budget.BudgetExceeded):
                budget.pause(2)
        self.assertEqual(budget.timeout(30), 30)

    def test_expired_deadline_is_checked_before_and_after_a_body_chunk(self):
        meter = budget.BuildBudget(1, 100)
        meter.started -= 2
        with self.assertRaises(budget.BudgetExceeded):
            meter.read(Response(b"hello"), 5)
        self.assertEqual(meter.received, 0)

    def test_http_budget_abort_preserves_playlist_and_does_not_advance_health(self):
        with tempfile.TemporaryDirectory() as tmp, fixture_server() as (base, _):
            root = Path(tmp)
            previous = render_m3u([channel()])
            (root / "playlist.m3u").write_text(previous)
            config = settings(
                extra_sources=(Source(base + "/source.m3u", country="us"),),
                budget=BudgetOptions(max_seconds=10, max_http_bytes=50),
            )
            with redirect_stdout(io.StringIO()), self.assertRaisesRegex(BuildError, "receive-byte"):
                run_build(config, root)
            self.assertEqual((root / "playlist.m3u").read_text(), previous)
            self.assertFalse((root / ".cache/iptv/link-health.json").exists())
            runtime = read_json(root / ".cache/iptv/last_run.json")
            self.assertEqual(runtime["status"], "blocked")
            self.assertEqual(runtime["budget"]["http_bytes_received"], 50)

    def test_budget_exhaustion_is_not_hidden_by_source_cache_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, patch("iptv_builder.sources.urlopen") as opened:
            opened.return_value = Response(render_m3u([channel()]).encode())
            source = Source("https://source.test/playlist")
            root = Path(tmp)
            fetch_source(source, settings(), root)
            opened.return_value = Response(b"x" * 1000)
            with (
                budget.build_budget(BudgetOptions(max_seconds=5, max_http_bytes=20)),
                self.assertRaises(budget.BudgetExceeded),
            ):
                fetch_source(source, settings(), root)

    def test_invalid_global_budgets_are_rejected_at_config_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            for options in (
                {"max_http_bytes": True},
                {"max_seconds": 0},
                {"max_seconds": 86401},
                {"max_http_bytes": 4294967297},
            ):
                path.write_text(json.dumps({"budget": options}))
                with self.assertRaises(ValueError):
                    load_settings(path)


class PrivacyTests(unittest.TestCase):
    def test_public_diagnostics_remove_url_credentials_queries_fragments_and_pipe_headers(self):
        raw = "https://user:pass@stream.test/live.m3u8?token=secret#fragment|Cookie=session"
        safe = public_url(raw)
        for private in ("user", "pass", "token=secret", "fragment", "Cookie", "session"):
            self.assertNotIn(private, safe)
        nested = public_data({"changes": [{"before": raw}], "error": "Failure at " + raw})
        self.assertNotIn("secret", json.dumps(nested))
        self.assertNotIn("user:pass", json.dumps(nested))

    def test_secrets_in_paths_and_headers_are_not_eligible_for_public_personal_exports(self):
        for item in (
            channel(url="https://stream.test/token/secret/live.m3u8"),
            channel(url="https://stream.test/live|Cookie=secret"),
            channel(directives=("#EXTVLCOPT:http-password=secret",)),
        ):
            self.assertFalse(exportable_channel(item))
        self.assertTrue(
            exportable_channel(channel(directives=("#EXTVLCOPT:http-user-agent=Agent",)))
        )
        self.assertEqual(
            public_url("https://example.test:443/a?b=c"), "https://example.test:443/a?[redacted]"
        )


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.output = self.root / "site"
        self.item = channel()
        self.snapshot = {
            "generated_at": "2026-11-09T12:00:00+00:00",
            "channels": 1,
            "channel_catalog": [
                {"identity": self.item.identity, "name": self.item.name, "checked_at": "old"}
            ],
            "personalization": {"managed_files": []},
        }
        self.runtime = {
            **self.snapshot,
            "generated_at": "2026-11-10T12:00:00+00:00",
            "status": "unchanged",
            "checked_links": 0,
            "cached_links": 1,
            "channel_catalog": [
                {
                    "identity": self.item.identity,
                    "name": self.item.name,
                    "checked_at": "recent",
                    "cached": True,
                }
            ],
        }
        (self.source / "playlist.m3u").write_text(render_m3u([self.item]))
        self.write_reports()

    def write_reports(self):
        (self.source / "build_report.json").write_text(json_text(self.snapshot))
        (self.source / "runtime.json").write_text(json_text(self.runtime))

    def stage(self, **options):
        return stage_site(self.source, self.source / "runtime.json", self.output, **options)

    def test_latest_run_is_separate_from_last_static_publication_without_mutating_inputs(self):
        before = (self.source / "build_report.json").read_bytes()
        result = self.stage()
        self.assertEqual(result["last_meaningful_publication"], self.snapshot["generated_at"])
        self.assertEqual(result["latest_run"], self.runtime["generated_at"])
        report = read_json(self.output / "build_report.json")
        self.assertEqual(report["generated_at"], self.snapshot["generated_at"])
        self.assertEqual(report["last_run"]["generated_at"], self.runtime["generated_at"])
        self.assertEqual(report["channel_catalog"][0]["checked_at"], "recent")
        self.assertEqual((self.source / "build_report.json").read_bytes(), before)
        self.assertTrue((self.output / "index.html").is_file())
        self.assertTrue((self.output / ".nojekyll").exists())

    def test_pages_only_copies_explicit_public_outputs_not_inputs_caches_or_logs(self):
        for name in ("manual.m3u", "config.json", "private.json", "build.log", "link-health.json"):
            (self.source / name).write_text("private data")
        self.stage()
        for name in (
            "manual.m3u",
            "config.json",
            "private.json",
            "build.log",
            "link-health.json",
            "runtime.json",
        ):
            self.assertFalse((self.output / name).exists())
        self.assertTrue((self.output / "last_run.json").exists())

    def test_dry_failed_blocked_or_mismatched_runtime_cannot_be_deployed(self):
        for status in ("failed", "blocked", "dry_run", "running"):
            self.runtime["status"] = status
            self.write_reports()
            with self.assertRaises(ValueError):
                self.stage()
            self.assertFalse(self.output.exists())
        self.runtime.update(status="published", channels=2)
        self.write_reports()
        with self.assertRaises(ValueError):
            self.stage()

    def test_sensitive_stream_is_not_mirrored_and_preview_is_honestly_labelled(self):
        (self.source / "playlist.m3u").write_text(
            render_m3u([channel(url="https://stream.test/live?token=private")])
        )
        result = self.stage(preview=True)
        self.assertTrue(result["preview"])
        self.assertEqual(result["omitted_sensitive_streams"], 1)
        self.assertNotIn("private", (self.output / "playlist.m3u").read_text())
        self.assertIn("Disallow", (self.output / "robots.txt").read_text())
        self.assertTrue(read_json(self.output / "build_report.json")["site"]["preview"])

    def test_unsafe_profile_names_symlinks_and_repository_root_are_rejected(self):
        with self.assertRaises(ValueError):
            stage_site(self.source, self.source / "runtime.json", self.source)
        self.snapshot["personalization"]["managed_files"] = ["../secret.m3u"]
        self.write_reports()
        with self.assertRaises(ValueError):
            self.stage()
        self.snapshot["personalization"]["managed_files"] = []
        self.write_reports()
        (self.source / "news.m3u").symlink_to(self.source / "playlist.m3u")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                stage_site(self.source, self.source / "runtime.json", Path(tmp) / "site")
