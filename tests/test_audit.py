import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from iptv_builder.audit import full_audit, main
from iptv_builder.m3u import render_m3u
from tests.helpers import ROOT, channel
from tests.test_integration import fixture_server


class FullAuditTests(unittest.TestCase):
    def test_complete_local_http_audit_keeps_production_files_and_history_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp, fixture_server() as (base, _):
            root = Path(tmp)
            path = root / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "stream_template": "",
                        "extra_sources": [{"url": base + "/source.m3u", "country": "us"}],
                        "source_attempts": 1,
                        "link_attempts": 1,
                        "timeout": 2,
                        "retry_delay": 0,
                    }
                )
            )
            before = path.read_bytes()
            with redirect_stdout(io.StringIO()):
                result = full_audit(path, root / "audit")
            self.assertTrue(result["analysis_completed"])
            self.assertTrue(result["publication_allowed"])
            self.assertFalse(result["production_modified"])
            self.assertTrue(result["history_preserved"])
            self.assertGreater(result["checked_links"], 0)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((root / "playlist.m3u").exists())
            self.assertTrue((root / "audit/audit_summary.json").exists())

    def test_unsafe_drop_is_reported_without_bypassing_a_production_guard(self):
        with tempfile.TemporaryDirectory() as tmp, fixture_server() as (base, _):
            root = Path(tmp)
            path = root / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "stream_template": "",
                        "extra_sources": [{"url": base + "/source.m3u", "country": "us"}],
                        "source_attempts": 1,
                        "link_attempts": 1,
                        "timeout": 2,
                        "retry_delay": 0,
                    }
                )
            )
            previous = render_m3u(
                [channel(tvg_id=f"Old{i}.us", url=f"https://old.test/{i}") for i in range(20)]
            )
            (root / "playlist.m3u").write_text(previous)
            with redirect_stdout(io.StringIO()):
                result = full_audit(path, root / "audit")
            self.assertTrue(result["analysis_completed"])
            self.assertEqual(result["status"], "blocked")
            self.assertFalse(result["publication_allowed"])
            self.assertIn("drop limit", result["guard_error"])
            self.assertEqual((root / "playlist.m3u").read_text(), previous)
            self.assertTrue(result["history_preserved"])
            with self.assertRaises(ValueError):
                full_audit(path, root / "audit")

    def test_production_root_is_rejected(self):
        with self.assertRaises(ValueError):
            full_audit(ROOT / "config.json", ROOT)

    def test_cli_error_returns_nonzero(self):
        with (
            patch("iptv_builder.audit.full_audit", side_effect=ValueError("bad output")),
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main([]), 1)
