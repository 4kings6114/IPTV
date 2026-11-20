"""Source-specific corrections and deduplication across independent lists."""

import unittest
from collections import Counter
from pathlib import Path

from iptv_builder.alternatives import manual_alternatives
from iptv_builder.builder import select_channels
from iptv_builder.classification import load_excludes, load_overrides, prepare_channels
from iptv_builder.config import Source, load_settings
from iptv_builder.health import ProbeResult
from iptv_builder.m3u import parse, render_m3u, validate_output
from iptv_builder.source_identity import GERMAN_INTERNATIONAL, GERMAN_PUBLIC
from tests.helpers import ROOT, channel


class SourceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings(ROOT / "config.json")
        self.overrides = load_overrides(ROOT / "overrides.json")
        self.excludes = load_excludes(ROOT / "exclude.txt")

    def prepare(self, channels, url, country="", manual=False):
        return prepare_channels(
            render_m3u(channels),
            Source(url, country=country, allow_unknown_country=True),
            self.settings,
            self.overrides,
            self.excludes,
            manual=manual,
        )

    def test_german_list_deduplicates_id_and_keeps_regional_feeds(self):
        originals = [
            channel(name="Das Erste", tvg_id="Das.Erste.de", url="https://test.example/a"),
            channel(name="MDR Sachsen", tvg_id="MDR.de", url="https://test.example/b"),
            channel(name="MDR Sachsen-Anhalt", tvg_id="MDR.de", url="https://test.example/c"),
            channel(name="NDR Hamburg", tvg_id="NDR.de", url="https://test.example/d"),
            channel(name="NDR Niedersachsen", tvg_id="NDR.de", url="https://test.example/e"),
            channel(name="SWR Baden-Württemberg", tvg_id="SWR/SR.de", url="https://test.example/f"),
            channel(name="SR Fernsehen", tvg_id="SWR/SR.de", url="https://test.example/g"),
        ]
        items = self.prepare(originals, GERMAN_PUBLIC, country="de")
        self.assertEqual(
            [item.identity for item in items],
            [
                "id:daserste.de",
                "id:mdrfernsehen.de@sachsen",
                "id:mdrfernsehen.de@sachsenanhalt",
                "id:ndrfernsehen.de@hamburg",
                "id:ndrfernsehen.de@niedersachsen",
                "id:swrfernsehenbadenwurttemberg.de",
                "id:srfernsehen.de",
            ],
        )
        self.assertEqual(len({item.identity for item in items}), len(items))
        existing = channel(
            name="Das Erste HD", tvg_id="DasErste.de@HD", url="https://test.example/existing"
        )
        all_items = [*items, existing, items[0]]
        results = {item.probe_key: ProbeResult("healthy", "synthetic") for item in all_items}
        selected = select_channels(all_items, results, {}, [], self.settings).channels
        self.assertEqual(len(selected), len(items))
        self.assertEqual(len({item.identity for item in selected}), len(selected))
        validate_output(render_m3u(selected), len(selected))
        alternate, _ = manual_alternatives(
            all_items, selected, results, {}, self.settings, select_channels
        )
        self.assertEqual(Counter(item.identity for item in alternate)["id:daserste.de"], 2)
        self.assertEqual(len({item.request_url for item in alternate}), len(alternate))

    def test_international_list_preserves_language_feeds(self):
        items = self.prepare(
            [
                channel(
                    name="France 24 English", tvg_id="France24.fr", url="https://test.example/en"
                ),
                channel(
                    name="France 24 Français", tvg_id="France24.fr", url="https://test.example/fr"
                ),
                channel(name="DW English", tvg_id="DW.de", url="https://test.example/dw"),
            ],
            GERMAN_INTERNATIONAL,
        )
        self.assertEqual(
            {c.identity for c in items},
            {"id:france24.fr@english", "id:france24.fr@french", "id:dw.de@english"},
        )

    def test_alias_is_scoped_to_exact_source_id_and_name(self):
        example = channel(name="Das Erste", tvg_id="Das.Erste.de")
        for item, source, manual in (
            (example, "https://other.test/source", False),
            (channel(name="Someone else", tvg_id="Das.Erste.de"), GERMAN_PUBLIC, False),
            (channel(name="Das Erste", tvg_id="Different.de"), GERMAN_PUBLIC, False),
            (example, GERMAN_PUBLIC, True),
        ):
            with self.subTest(item=item, source=source, manual=manual):
                actual = self.prepare([item], source, country="de", manual=manual)
                self.assertEqual(actual[0].channel_id, item.channel_id)

    def test_existing_primary_has_no_duplicate_identities_or_urls(self):
        items = parse((Path(ROOT) / "playlist.m3u").read_text(encoding="utf-8"))
        self.assertEqual(len(items), len({item.identity for item in items}))
        self.assertEqual(len(items), len({item.request_url for item in items}))
