import gzip
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from iptv_builder import catalog as g
from iptv_builder.classification import prepare_channels
from iptv_builder.config import EPGOptions, MetadataOptions, Source
from iptv_builder.epg import load_aliases, parse_xmltv, validate_epg, xmltv_time
from iptv_builder.m3u import parse, render_m3u
from iptv_builder.metadata import ReferenceCatalog, load_metadata, parse_metadata
from iptv_builder.resources import Resource, fetch_resource, resource_path
from iptv_builder.storage import atomic_write, json_text
from tests.helpers import Response, channel, settings

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def xmltv(identifier="guide.one", start=NOW - timedelta(hours=1), stop=NOW + timedelta(hours=2)):
    return (
        f'<tv><channel id="{identifier}"><display-name>Example</display-name></channel>'
        f'<programme channel="{identifier}" start="{start:%Y%m%d%H%M%S %z}" stop="{stop:%Y%m%d%H%M%S %z}"><title>Show</title></programme></tv>'
    ).encode()


class ResourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = settings(source_attempts=2)
        self.url = "https://resource.test/catalog.json"

    def fetch(self, **options):
        return fetch_resource(
            self.url,
            "test",
            self.config,
            self.root,
            lambda body: {"schema_version": 1, "items": json.loads(body)},
            options.get("max_bytes", 512),
            options.get("cache_hours", 24),
            options.get("refresh_hours", 0),
        )

    @patch("iptv_builder.resources.urlopen")
    def test_gzip_expansion_and_valid_cache_fallback(self, opened):
        opened.return_value = Response(gzip.compress(b'["hello"]'))
        first = self.fetch()
        self.assertEqual(first.status, "fresh")
        self.assertEqual(first.value["items"], ["hello"])
        before = resource_path(self.root, "test", self.url).read_bytes()
        opened.side_effect = URLError("offline")
        second = self.fetch()
        self.assertEqual(second.status, "cached")
        self.assertTrue(second.error)
        self.assertEqual(resource_path(self.root, "test", self.url).read_bytes(), before)

    @patch("iptv_builder.resources.urlopen")
    def test_warm_cache_can_skip_network_but_expired_and_future_caches_cannot(self, opened):
        opened.return_value = Response(b"[]")
        self.fetch()
        opened.reset_mock()
        self.assertEqual(self.fetch(refresh_hours=12).status, "cached")
        opened.assert_not_called()
        path = resource_path(self.root, "test", self.url)
        for fetched in (NOW - timedelta(days=999), datetime.now(timezone.utc) + timedelta(days=1)):
            atomic_write(
                path,
                json_text(
                    {
                        "url": self.url,
                        "value": {"schema_version": 1},
                        "fetched_at": fetched.isoformat(),
                    }
                ),
            )
            opened.side_effect = URLError("offline")
            self.assertEqual(self.fetch().status, "failed")

    @patch("iptv_builder.resources.urlopen")
    def test_malformed_truncated_gzip_and_oversized_data_never_replace_cache(self, opened):
        for body in (b"not json", b"x" * 513, gzip.compress(b" " * 1024), b"\x1f\x8bbroken"):
            with self.subTest(body=body[:15]):
                opened.side_effect = None
                opened.return_value = Response(body)
                result = self.fetch()
                self.assertEqual(result.status, "failed")
                self.assertFalse(resource_path(self.root, "test", self.url).exists())

    @patch("iptv_builder.resources.urlopen")
    def test_non_http_resources_are_rejected_without_network(self, opened):
        result = fetch_resource(
            "file:///etc/passwd", "test", self.config, self.root, parse_xmltv, 512, 24
        )
        self.assertEqual(result.status, "failed")
        opened.assert_not_called()


class MetadataTests(unittest.TestCase):
    def catalog(self):
        catalog = ReferenceCatalog()
        for records in (
            [
                {
                    "id": "Example.us",
                    "name": "Canonical News",
                    "country": "US",
                    "categories": ["news"],
                }
            ],
            [{"channel": "Example.us", "id": "HD", "languages": ["eng"], "is_main": True}],
            [
                {
                    "channel": "Example.us",
                    "feed": None,
                    "in_use": True,
                    "url": "https://logos.test/example.png",
                }
            ],
        ):
            catalog.add(Resource("fixture", parse_metadata(json.dumps(records).encode()), "fresh"))
        return catalog

    def test_reference_enriches_name_logo_language_category_and_preserves_quality_identity(self):
        item = channel(name="Wrong Music (720p)")
        result = prepare_channels(
            render_m3u([item]),
            Source("source"),
            settings(),
            {"channels": {}, "names": {}},
            (set(), set()),
            reference=self.catalog(),
        )[0]
        self.assertEqual(result.identity, item.identity)
        self.assertEqual(result.name, "Canonical News (720p)")
        self.assertEqual(result.attrs["tvg-logo"], "https://logos.test/example.png")
        self.assertEqual(result.attrs["tvg-language"], "en")
        self.assertEqual(result.group, g.EN_NEWS)

    def test_manual_names_groups_and_explicit_corrections_win_over_reference(self):
        item = channel(name="My Name", group="My Group")
        result = prepare_channels(
            render_m3u([item]),
            Source("manual"),
            settings(),
            {"channels": {}, "names": {}},
            (set(), set()),
            manual=True,
            reference=self.catalog(),
        )[0]
        self.assertEqual((result.name, result.group), ("My Name", "My Group"))
        overrides = {
            "channels": {
                "example.us": {
                    "name": "Override",
                    "language": "fa",
                    "group": "Custom",
                    "logo": "https://mine.test/logo.png",
                }
            },
            "names": {},
        }
        result = prepare_channels(
            render_m3u([item]),
            Source("source"),
            settings(),
            overrides,
            (set(), set()),
            reference=self.catalog(),
        )[0]
        self.assertEqual(
            (result.name, result.group, result.attrs["tvg-language"]), ("Override", "Custom", "fa")
        )
        self.assertEqual(result.attrs["tvg-logo"], "https://mine.test/logo.png")

    def test_names_are_not_guessed_and_ambiguous_matches_are_reported(self):
        catalog = self.catalog()
        catalog.add(
            Resource(
                "fixture",
                parse_metadata(
                    json.dumps(
                        [{"id": "Other.us", "name": "Canonical News", "country": "US"}]
                    ).encode()
                ),
                "fresh",
            )
        )
        unknown = channel(name="Canonical News", tvg_id="").with_attribute("tvg-country", "us")
        self.assertEqual(catalog.lookup(unknown), {})
        self.assertEqual(catalog.summary([unknown])["ambiguous"], 1)
        self.assertEqual(catalog.summary([unknown])["without_id"], 1)

    def test_regional_feed_does_not_inherit_ambiguous_languages(self):
        catalog = self.catalog()
        catalog.add(
            Resource(
                "fixture",
                parse_metadata(
                    json.dumps(
                        [{"channel": "Example.us", "id": "West", "languages": ["spa"]}]
                    ).encode()
                ),
                "fresh",
            )
        )
        self.assertEqual(catalog.lookup(channel(tvg_id="Example.us@West"))["language"], "spa")
        self.assertEqual(catalog.lookup(channel(tvg_id="Example.us@Unknown"))["language"], "")

    def test_quality_alias_can_use_the_single_main_feed_logo_without_guessing_other_regions(self):
        catalog = self.catalog()
        catalog.feeds["example.us"] = [{"id": "Main", "languages": ["eng"], "is_main": True}]
        catalog.logos["example.us"] = [
            {"feed": "Main", "url": "https://logos.test/main.png", "in_use": True}
        ]
        self.assertEqual(catalog.lookup(channel())["logo"], "https://logos.test/main.png")
        self.assertEqual(catalog.lookup(channel(tvg_id="Example.us@East"))["logo"], "")

    def test_local_reference_validation_and_offline_metadata_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = settings(metadata=MetadataOptions(file="reference.json"))
            with self.assertRaises(ValueError):
                load_metadata(config, root, root)
            (root / "reference.json").write_text(
                '[{"id":"Example.us","name":"Example","country":"US"}]'
            )
            self.assertEqual(len(load_metadata(config, root, root).channels), 1)
        for body in (b"{}", b"[]", b"[{}]", b"not json"):
            with self.assertRaises(ValueError):
                parse_metadata(body)


class EPGTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = settings(
            epg_urls=("https://guide.test/tv.xml.gz",),
            epg=EPGOptions(enabled=True),
            source_attempts=1,
        )

    @patch("iptv_builder.resources.urlopen")
    def test_gzip_alias_rewrite_preserves_identity_over_round_trip_and_overrides(self, opened):
        opened.return_value = Response(gzip.compress(xmltv()))
        (self.root / "epg_aliases.json").write_text('{"channels":{"Example.us":"guide.one"}}')
        item = channel()
        final, urls, report, coverage = validate_epg(self.config, [item], self.root, self.root, NOW)
        self.assertEqual(final[0].attrs["tvg-id"], "guide.one")
        self.assertEqual(final[0].channel_id, item.channel_id)
        self.assertEqual(parse(render_m3u(final))[0].identity, item.identity)
        self.assertEqual(urls, self.config.epg_urls)
        self.assertEqual(report["covered"], 1)
        self.assertEqual(coverage[item.identity], "covered")
        overrides = {"channels": {"example.us": {"group": "Override"}}, "names": {}}
        reparsed = prepare_channels(
            render_m3u(final), Source("previous"), self.config, overrides, (set(), set())
        )
        self.assertEqual(reparsed[0].group, "Override")
        # Neither the numeric guide ID nor the missing country suffix loses country policy.
        self.assertEqual(reparsed[0].attrs["tvg-country"], "us")

    @patch("iptv_builder.resources.urlopen")
    def test_stale_guide_is_reported_and_verified_only_headers_exclude_it(self, opened):
        opened.return_value = Response(
            xmltv(stop=NOW - timedelta(days=1), start=NOW - timedelta(days=2))
        )
        config = replace(self.config, epg=replace(self.config.epg, keep_unverified=False))
        item = channel(tvg_id="guide.one")
        final, urls, report, coverage = validate_epg(config, [item], self.root, self.root, NOW)
        self.assertFalse(urls)
        self.assertEqual(report["missing"], 1)
        self.assertEqual(coverage[item.identity], "stale")
        self.assertEqual(final[0].url, item.url)

    @patch("iptv_builder.resources.urlopen")
    def test_unavailable_guide_and_bad_alias_never_guess_by_name_or_break_streams(self, opened):
        opened.side_effect = URLError("offline")
        item = channel()
        final, urls, report, _ = validate_epg(self.config, [item], self.root, self.root, NOW)
        self.assertEqual(final, [item])
        self.assertEqual(urls, self.config.epg_urls)
        self.assertEqual(report["guides"][0]["status"], "failed")
        self.assertEqual(report["missing_channels"][0]["status"], "missing_id")

    @patch("iptv_builder.resources.urlopen")
    def test_regional_feed_never_silently_uses_base_channel_guide(self, opened):
        opened.return_value = Response(xmltv(identifier="Example.us"))
        item = channel(tvg_id="Example.us@East")
        final, _, report, _ = validate_epg(self.config, [item], self.root, self.root, NOW)
        self.assertEqual(final[0].attrs["tvg-id"], "Example.us@East")
        self.assertEqual(report["missing"], 1)

    @patch("iptv_builder.resources.urlopen")
    def test_quality_specific_alias_normalizes_gb_uk_without_merging_regional_ids(self, opened):
        opened.return_value = Response(xmltv())
        (self.root / "epg_aliases.json").write_text('{"channels":{"Example.gb@HD":"guide.one"}}')
        item = channel(tvg_id="Example.uk@HD")
        final, _, report, _ = validate_epg(self.config, [item], self.root, self.root, NOW)
        self.assertEqual(final[0].attrs["tvg-id"], "guide.one")
        self.assertEqual(report["covered"], 1)
        self.assertEqual(final[0].identity, item.identity)

    @patch("iptv_builder.resources.urlopen")
    def test_past_and_far_future_programmes_do_not_fake_current_guide_coverage(self, opened):
        past = xmltv(start=NOW - timedelta(days=10), stop=NOW - timedelta(days=9))
        future = xmltv(start=NOW + timedelta(days=10), stop=NOW + timedelta(days=11))
        body = past.replace(b"</tv>", future.split(b"<programme", 1)[1].join([b"<programme", b""]))
        opened.return_value = Response(body)
        _, _, report, coverage = validate_epg(
            self.config, [channel(tvg_id="guide.one")], self.root, self.root, NOW
        )
        self.assertEqual(report["covered"], 0)
        self.assertEqual(coverage[channel(tvg_id="guide.one").identity], "future")

    def test_adjacent_programme_windows_are_compacted_but_gaps_are_preserved(self):
        from iptv_builder.epg import _coverage

        summary = parse_xmltv(
            b'<tv><channel id="one"/><programme channel="one" start="20261110110000 +0000" stop="20261110120000 +0000"/><programme channel="one" start="20261110120000 +0000" stop="20261110130000 +0000"/></tv>'
        )
        item = summary["channels"]["one"]
        self.assertEqual(len(item["windows"]), 1)
        self.assertEqual(item["count"], 2)
        self.assertEqual(_coverage(item, NOW, 24), "covered")

    def test_structural_validation_rejects_dtd_entity_and_non_xmltv(self):
        for body in (
            b"<html/>",
            b"<tv/>",
            b"<tv>",
            b'<!DOCTYPE tv [<!ENTITY x "xx">]><tv><channel id="x"/></tv>',
            "<!DOCTYPE tv><tv/>".encode("utf-16"),
        ):
            with self.subTest(body=body[:20]), self.assertRaises(ValueError):
                parse_xmltv(body)
        summary = parse_xmltv(
            b'<tv><channel id="one"/><programme channel="two" start="20261110120000 +0000" stop="20261110130000 +0000"/><programme channel="one" start="bad" stop="bad"/></tv>'
        )
        self.assertEqual(summary["orphan_programmes"], 1)
        self.assertEqual(summary["invalid_programmes"], 1)

    def test_timestamp_timezone_and_alias_validation(self):
        self.assertEqual(xmltv_time("20261110130000 +0100"), NOW)
        self.assertEqual(xmltv_time("202611101200Z"), NOW)
        for value in ("bad", "20261310120000 +0000", "20261110120000 +9900"):
            self.assertIsNone(xmltv_time(value))
        path = self.root / "aliases.json"
        self.assertEqual(load_aliases(path), {})
        for data in (
            [],
            {"channels": []},
            {"unknown": {}},
            {"channels": {"x": 'bad"id'}},
            {"channels": {"X": "one", "x": "two"}},
        ):
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_aliases(path)

    @patch("iptv_builder.resources.urlopen")
    def test_disabled_epg_makes_no_network_requests(self, opened):
        result = validate_epg(settings(), [channel()], self.root, self.root)
        self.assertFalse(result[2]["enabled"])
        opened.assert_not_called()
