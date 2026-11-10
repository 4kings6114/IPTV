import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from iptv_builder.diagnostics import main, validate
from tests.helpers import ROOT
from tests.test_integration import fixture_server


class DiagnosticTests(unittest.TestCase):
    def test_actual_local_http_diagnostics_cache_dry_run_and_guard_in_isolated_directory(self):
        with tempfile.TemporaryDirectory() as tmp, fixture_server() as (base, state):
            root = Path(tmp)
            config = root / "config.json"
            config.write_text(
                json.dumps(
                    {
                        "stream_template": "",
                        "extra_sources": [{"url": base + "/source.m3u", "country": "us"}],
                        "health_cache": {"hours": 6},
                        "retry_delay": 0,
                        "timeout": 2,
                        "source_timeout": 2,
                        "cache_dir": "../unsafe-cache",
                    }
                )
            )
            with redirect_stdout(io.StringIO()):
                result = validate(config, root / "scratch", limit=3, playback_samples=0)
            self.assertEqual(result["status"], "passed")
            self.assertFalse(result["production_modified"])
            self.assertEqual(result["available_sources"], 1)
            self.assertGreater(result["channels"], 0)
            self.assertGreater(result["second_run_cached_links"], 0)
            self.assertTrue(result["dry_run_preserved_outputs_and_history"])
            self.assertTrue(result["synthetic_minimum_guard_preserved_outputs"])
            self.assertTrue((root / "scratch/output/index.html").is_file())
            self.assertFalse((root / "playlist.m3u").exists())
            self.assertFalse((root / "scratch/unsafe-cache").exists())
            self.assertTrue(state["requests"])
            with self.assertRaises(ValueError):
                validate(config, root / "scratch", limit=3, playback_samples=0)

    def test_rejects_repository_root_and_invalid_cli_budgets_before_network(self):
        with patch("iptv_builder.diagnostics.fetch_sources") as fetched:
            with self.assertRaises(ValueError):
                validate(ROOT / "config.json", ROOT)
            fetched.assert_not_called()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--limit", "51"])
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--playback-samples", "6"])

    def test_actions_summary_is_machine_readable_and_escapes_workflow_commands(self):
        result = {
            "status": "passed",
            "source_count": 1,
            "available_sources": 1,
            "channels": 1,
            "second_run_cached_links": 1,
            "note": "100%\n::warning::not-a-command",
        }
        with (
            patch("iptv_builder.diagnostics.validate", return_value=result),
            patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}),
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(main([]), 0)
        notice = next(
            line for line in output.getvalue().splitlines() if line.startswith("::notice")
        )
        self.assertIn("100%25", notice)
        self.assertNotIn("\n::warning", notice)

    def test_large_source_lists_do_not_truncate_or_invalidate_actions_summary_json(self):
        from iptv_builder.diagnostics import _actions_notices

        result = {
            "status": "passed",
            "source_count": 60,
            "sources": [{"url": "https://source.test/" + "x" * 100, "status": "fresh"}] * 60,
            "metadata": {"matched": 12, "missing_examples": ["x"] * 50},
            "epg": {"covered": 0, "missing": 12, "missing_channels": ["x"] * 12},
            "playback": {"samples": [{"reason": "x" * 4000}]},
        }
        with redirect_stdout(io.StringIO()) as output:
            _actions_notices(result)
        notices = output.getvalue().splitlines()
        self.assertEqual(len(notices), 5)
        for notice in notices:
            self.assertLess(len(notice.encode()), 3500)
            json.loads(notice.split("::", 2)[2])
        self.assertIn('"source_count":60', notices[0])
        self.assertIn('"fresh":60', notices[1])

    def test_cli_reports_failure_without_publishing(self):
        with (
            patch("iptv_builder.diagnostics.validate", side_effect=ValueError("bad config")),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main([]), 1)
        result = {
            "status": "passed",
            "source_count": 1,
            "available_sources": 1,
            "channels": 1,
            "second_run_cached_links": 1,
        }
        with (
            patch("iptv_builder.diagnostics.validate", return_value=result),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main([]), 0)


class DiagnosticSampleTests(unittest.TestCase):
    def test_verified_alias_identities_are_included_without_conflating_regional_feeds(self):
        from iptv_builder.diagnostics import _sample
        from tests.helpers import channel

        items = [
            channel(tvg_id="Example.us@East", url="https://regional.test/live"),
            channel(tvg_id="Example.us@HD", url="https://national.test/live"),
            channel(tvg_id="Other.de", url="https://other.test/live"),
        ]
        chosen = _sample(items, 2, ["example.us"])
        self.assertEqual(len(chosen), 2)
        self.assertEqual(chosen[0].channel_id, "Example.us@HD")
        self.assertEqual(len({item.identity for item in chosen}), 2)
        self.assertEqual(len(_sample(items, 1, ["example.us", "other.de"])), 1)
