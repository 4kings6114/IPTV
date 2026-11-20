"""Non-destructive smoke tests against the repository's actual M3U data."""

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from iptv_builder.builder import run_build
from iptv_builder.config import EPGOptions, MetadataOptions, Source, load_settings
from iptv_builder.m3u import parse, render_m3u, validate_output
from iptv_builder.site import stage_site
from iptv_builder.sources import SourceResult
from iptv_builder.storage import read_json
from tests.helpers import ROOT


class RepositoryDataTests(unittest.TestCase):
    def test_all_existing_playlists_round_trip_without_losing_entries(self):
        for path in sorted(ROOT.glob("*.m3u")):
            with self.subTest(file=path.name):
                text = path.read_text(encoding="utf-8")
                entries = parse(text)
                self.assertEqual(
                    len(entries), sum(line.startswith("#EXTINF:") for line in text.splitlines())
                )
                rendered = render_m3u(entries)
                validate_output(rendered, len(entries))
                self.assertEqual(
                    [(item.ext, item.url) for item in parse(rendered)],
                    [(item.ext, item.url) for item in entries],
                )

    def test_full_size_offline_build_uses_temporary_output_only(self):
        source = Source("https://fixtures.test/existing-playlist", allow_unknown_country=True)
        text = (ROOT / "playlist.m3u").read_text(encoding="utf-8")
        before = {path: path.read_bytes() for path in ROOT.glob("*.m3u")}
        config = replace(
            load_settings(ROOT / "config.json"),
            stream_template="",
            extra_sources=(source,),
            check_links=False,
            metadata=MetadataOptions(),
            epg=EPGOptions(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / "playlist.m3u").write_text(text, encoding="utf-8")
            with patch(
                "iptv_builder.builder.fetch_sources",
                return_value=[SourceResult(source, text, "fresh")],
            ):
                with redirect_stdout(io.StringIO()):
                    result = run_build(config, output, ROOT)
            final = parse((output / "playlist.m3u").read_text(encoding="utf-8"))
            self.assertGreater(len(final), 0)
            self.assertEqual(result["channels"], len(final))
            self.assertEqual(result["checked_links"], 0)
            self.assertGreaterEqual(result["health"].get("unchecked", 0), len(final))
            self.assertNotIn("healthy", result["health"])
            self.assertTrue((output / "playlist-backup.m3u").exists())
            self.assertEqual((output / "playlist-backup.m3u").read_text(encoding="utf-8"), text)
            # Exercise the publisher's next step as well: the playlist-only
            # checkout must stage Pages without any removed app/tooling files.
            site = output / "pages"
            staged = stage_site(output, output / ".cache/iptv/last_run.json", site)
            self.assertEqual(staged["status"], "staged")
            self.assertEqual(staged["channels"], len(final))
            self.assertTrue((site / "index.html").is_file())
            self.assertTrue((site / "playlist.m3u").is_file())
            self.assertTrue((site / "build_stats.txt").is_file())
            self.assertEqual(read_json(site / "build_report.json")["channels"], len(final))
            self.assertEqual(read_json(site / "last_run.json")["status"], "published")
        self.assertTrue(all(path.read_bytes() == old for path, old in before.items()))
