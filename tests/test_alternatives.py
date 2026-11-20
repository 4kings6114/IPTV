"""Manual alternates must not change canonical identity/count/publication guards."""

import io
import tempfile
import unittest
from collections import Counter
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from iptv_builder.alternatives import ALTERNATIVES_FILE, manual_alternatives
from iptv_builder.builder import BuildError, run_build, select_channels
from iptv_builder.config import Source
from iptv_builder.health import ProbeResult
from iptv_builder.m3u import parse, render_m3u, validate_output
from iptv_builder.site import stage_site
from iptv_builder.sources import SourceResult
from tests.helpers import channel, settings

OK = ProbeResult("healthy", "fixture")
BAD = ProbeResult("http_error", "HTTP 404")


class ManualAlternativeTests(unittest.TestCase):
    def choose(self, items, results=None, config=None, primary=None):
        config = config or settings()
        if results is None:
            results = {item.probe_key: OK for item in items}
        if primary is None:
            primary = select_channels(items, results, {}, [], config).channels
        return manual_alternatives(items, primary, results, {}, config, select_channels)

    def test_two_best_distinct_urls_are_adjacent_and_third_is_omitted(self):
        high = channel(name="Example (1080p)", url="https://a.test/high")
        low = channel(name="Example (720p)", url="https://b.test/low")
        third = channel(name="Example (480p)", url="https://c.test/third")
        single = channel(name="Single", tvg_id="Single.us", url="https://s.test/only")
        items, report = self.choose([third, low, single, high, low])
        self.assertEqual([item.url for item in items], [high.url, low.url, single.url])
        self.assertEqual(items[0], high)
        self.assertEqual(items[1].name, "Example (720p) [جایگزین]")
        self.assertEqual(items[1].attrs["tvg-name"], items[1].name)
        self.assertEqual(items[0].identity, items[1].identity)
        self.assertEqual(report["channels_with_alternative"], 1)
        self.assertEqual(report["entries"], 3)
        validate_output(render_m3u(items), 3)

    def test_failed_unchecked_and_grace_links_never_become_alternates(self):
        first = channel(url="https://s.test/a")
        second = channel(url="https://s.test/b")
        for result in (BAD, ProbeResult("unchecked", "disabled"), ProbeResult("timeout", "late")):
            with self.subTest(status=result.status):
                entries, report = self.choose(
                    [first, second],
                    {first.probe_key: OK, second.probe_key: result},
                    config=settings(drop_dead=False),
                    primary=[first],
                )
                self.assertEqual(entries, [first])
                self.assertEqual(report["channels_with_alternative"], 0)
        entries, _ = self.choose(
            [first, second], {first.probe_key: BAD, second.probe_key: OK}, primary=[first]
        )
        self.assertEqual(entries, [first])

    def test_cache_healthy_is_eligible_but_not_a_fresh_playback_claim(self):
        first = channel(url="https://s.test/a")
        second = channel(url="https://s.test/b")
        entries, _ = self.choose(
            [first, second],
            {first.probe_key: OK, second.probe_key: replace(OK, cached=True)},
        )
        self.assertEqual(len(entries), 2)

    def test_headers_do_not_make_same_endpoint_a_second_link(self):
        first = channel(url="https://s.test/a|User-Agent=first")
        second = channel(url="https://s.test/a|User-Agent=second")
        entries, report = self.choose([first, second])
        self.assertEqual(len(entries), 1)
        self.assertEqual(report["channels_with_alternative"], 0)

    def test_primary_urls_are_reserved_and_other_alternative_is_tried(self):
        first = channel(tvg_id="A.us", url="https://s.test/a")
        second = channel(tvg_id="B.us", url="https://s.test/b")
        collision = channel(tvg_id="A.us", url=second.url + "|User-Agent=another")
        fallback = channel(tvg_id="A.us", url="https://s.test/c")
        entries, _ = self.choose([first, second, collision, fallback], primary=[first, second])
        self.assertEqual(
            [item.request_url for item in entries], [first.url, fallback.url, second.url]
        )

    def test_alternate_collisions_try_another_endpoint(self):
        first = channel(tvg_id="A.us", url="https://s.test/a")
        second = channel(tvg_id="B.us", url="https://s.test/b")
        a = channel(tvg_id="A.us", url="https://s.test/c")
        b = channel(tvg_id="B.us", url=a.url + "|User-Agent=another")
        spare = channel(tvg_id="B.us", url="https://s.test/d")
        entries, report = self.choose([first, second, a, b, spare], primary=[first, second])
        self.assertEqual(report["channels_with_alternative"], 2)
        self.assertEqual(len({item.request_url for item in entries}), 4)

    def test_group_epg_and_alternate_specific_playback_directives_survive(self):
        first = channel(url="https://s.test/a", group="curated").with_attribute(
            "tvg-id", "guide.one"
        )
        first = first.with_attribute("x-original-tvg-id", "Example.us")
        second = (
            channel(
                name="Example (480p)",
                url="https://s.test/b",
                group="other",
                directives=("#EXTVLCOPT:http-referrer=https://player.test/",),
            )
            .with_attribute("tvg-id", "guide.one")
            .with_attribute("x-original-tvg-id", "Example.us")
        )
        entries, _ = self.choose([first, second], primary=[first])
        self.assertEqual(entries[1].group, "curated")
        self.assertEqual(entries[1].attrs["tvg-id"], "guide.one")
        self.assertEqual(entries[1].identity, first.identity)
        self.assertEqual(entries[1].headers, second.headers)
        self.assertEqual(parse(render_m3u(entries))[1].ext, entries[1].ext)

    def test_regional_and_name_only_identities_stay_separate(self):
        items = [
            channel(tvg_id="Local.us@East", url="https://s.test/east"),
            channel(tvg_id="Local.us@West", url="https://s.test/west"),
            channel(name="Local (720p)", tvg_id="", url="https://s.test/one"),
            channel(name="Local (480p)", tvg_id="", url="https://s.test/two"),
        ]
        entries, report = self.choose(items)
        self.assertEqual(report["channels_with_alternative"], 1)
        self.assertEqual(sorted(Counter(item.identity for item in entries).values()), [1, 1, 2])

    def test_source_preference_is_reused_for_second_choice(self):
        items = [channel(url=f"https://{host}.test/live") for host in ("a", "b", "c")]
        entries, _ = self.choose(items, config=settings(preferred_sources=("c.test", "b.test")))
        self.assertEqual([item.url for item in entries], [items[2].url, items[1].url])


class AlternativeBuildTests(unittest.TestCase):
    def test_publish_pages_no_churn_dry_run_and_guard_with_real_build(self):
        first = channel(name="Example (1080p)", url="https://s.test/a")
        second = channel(name="Example (720p)", url="https://s.test/b")
        source = Source("https://source.test/list", allow_unknown_country=True)
        config = settings(extra_sources=(source,))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = [first, second]

            def fetch(*args):
                return [SourceResult(source, render_m3u(candidates), "fresh")]

            def check(items, *args):
                return {item.probe_key: OK for item in items}

            with (
                patch("iptv_builder.builder.fetch_sources", side_effect=fetch),
                patch("iptv_builder.builder.check_candidates", side_effect=check),
                redirect_stdout(io.StringIO()),
            ):
                result = run_build(config, root)
                self.assertEqual(result["channels"], 1)
                self.assertEqual(result["alternatives"]["entries"], 2)
                self.assertEqual(len(parse((root / "playlist.m3u").read_text())), 1)
                self.assertEqual(len(parse((root / ALTERNATIVES_FILE).read_text())), 2)
                before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
                result = run_build(config, root)
                self.assertEqual(result["status"], "unchanged")
                self.assertTrue(
                    all((root / name).read_bytes() == data for name, data in before.items())
                )
                stage_site(root, root / ".cache/iptv/last_run.json", root / "site")
                self.assertEqual(len(parse((root / "site" / ALTERNATIVES_FILE).read_text())), 2)
                self.assertIn(ALTERNATIVES_FILE, (root / "site/index.html").read_text())
                candidates.append(channel(url="https://s.test/0"))
                dry = run_build(config, root, dry_run=True)
                self.assertEqual(dry["status"], "dry_run")
                with self.assertRaises(BuildError):
                    # Two URLs for one network cannot satisfy a two-network minimum.
                    run_build(replace(config, min_channels=2), root)
                self.assertTrue(
                    all((root / name).read_bytes() == data for name, data in before.items())
                )
