import json
import tempfile
import unittest
from pathlib import Path

from iptv_builder import catalog as g
from iptv_builder.classification import classify, load_excludes, load_overrides, prepare_channels
from iptv_builder.config import Source, load_settings
from iptv_builder.m3u import render_m3u
from tests.helpers import ROOT, channel, settings


class ConfigTests(unittest.TestCase):
    def load(self, value):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            return load_settings(path)

    def test_repository_config_is_valid(self):
        config = load_settings(ROOT / "config.json")
        self.assertEqual(len(config.allowed_countries), 35)
        self.assertEqual(len(config.sources()), 37)
        self.assertEqual(config.failure_threshold, 2)
        self.assertEqual(config.max_drop_ratio, 0.3)

    def test_country_alias_and_source_deduplication(self):
        config = self.load(
            {
                "allowed_countries": ["GB", "uk"],
                "extra_sources": [
                    {"url": "https://source.test/uk", "country": "gb"},
                    "https://source.test/uk",
                ],
            }
        )
        self.assertEqual(config.allowed_countries, ("uk",))
        self.assertEqual(sum(item.url == "https://source.test/uk" for item in config.sources()), 1)

    def test_invalid_config_values_fail_fast(self):
        cases = [
            {"unknown": 1},
            {"check_links": "false"},
            {"max_workers": True},
            {"timeout": -1},
            {"failure_threshold": 0},
            {"max_drop_ratio": 1.1},
            {"source_cache_max_age_hours": float("inf")},
            {"retry_delay": float("nan")},
            {"extra_sources": [{"url": "file:///tmp/source"}]},
            {"extra_sources": [{"url": "https://s.test/a", "typo": 1}]},
            {"extra_sources": [{"url": "https://s.test/a", "allow_unknown_country": 1}]},
            {"allowed_countries": ["USA"]},
            {"epg_urls": ["https://epg.test/a,b"]},
            {"stream_template": "https://s.test/no-placeholder"},
            {"cache_dir": ""},
            {"extra_sources": [{"url": "https://s.test:bad/a"}]},
            {"extra_sources": [{"url": "https://s.test/invalid path"}]},
            {"epg_urls": ["https://epg.test/invalid\tpath"]},
            {"stream_template": "https://s.test/{country}/{unknown}"},
            {"stream_template": "https://s.test/{country}/{country.missing}"},
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.load(value)

    def test_manual_only_config(self):
        self.assertFalse(self.load({"stream_template": "", "extra_sources": []}).sources())


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.overrides = load_overrides(ROOT / "overrides.json")
        self.source = Source("https://source.test/us", country="us")

    def prepare(self, item, source=None, manual=False, excludes=(set(), set())):
        return prepare_channels(
            render_m3u([item]), source or self.source, settings(), self.overrides, excludes, manual
        )

    def test_express_is_never_persian_press(self):
        self.assertNotEqual(classify("Super Express TV", "pl"), g.FA_NEWS)
        self.assertEqual(classify("Super Express TV", "pl", "pl", "News"), g.WORLD_NEWS)
        result = self.prepare(
            channel(name="Super Express TV", tvg_id="SuperExpressTV.pl@HD", group=g.FA_NEWS),
            manual=True,
        )
        self.assertEqual(result[0].group, g.WORLD_NEWS)

    def test_movie_name_precedes_generic_hits_keyword(self):
        self.assertEqual(classify("Cinemax Hits (1080p)", "us"), g.MOV_EN)
        self.assertEqual(classify("Example Hits", "us", category="Movies"), g.MOV_EN)
        self.assertEqual(classify("Classic Rock", "us"), g.MUS_ROCK)

    def test_language_is_not_inferred_from_iranian_country_alone(self):
        self.assertEqual(classify("Press TV", "ir"), g.EN_NEWS)
        self.assertNotEqual(classify("Unspecified Channel", "ir"), g.FA_TV)
        self.assertEqual(classify("Iran International", "uk"), g.FA_NEWS)
        self.assertEqual(classify("Example News", "us", "Spanish"), g.WORLD_NEWS)
        self.assertEqual(classify("IRIB 1", "ir", "English"), g.ENT_ME)

    def test_real_tvg_country_beats_allowed_source_country(self):
        self.assertFalse(self.prepare(channel(tvg_id="Example.in@HD")))
        result = self.prepare(channel(tvg_id="Example.gb@HD"))
        self.assertEqual(result[0].attrs["tvg-country"], "uk")

    def test_unknown_country_is_opt_in(self):
        item = channel(tvg_id="")
        self.assertFalse(self.prepare(item, Source("https://s.test/mixed")))
        self.assertEqual(
            len(self.prepare(item, Source("https://s.test/mixed", allow_unknown_country=True))), 1
        )

    def test_manual_groups_preserved_unless_overridden(self):
        item = channel(group="گروه دلخواه")
        self.assertEqual(self.prepare(item, manual=True)[0].group, "گروه دلخواه")
        item = channel(name="Wrong", tvg_id="CinemaxHits.us@East", group=g.MUS_EN)
        self.assertEqual(self.prepare(item, manual=True)[0].group, g.MOV_EN)

    def test_source_language_and_explicit_metadata(self):
        source = Source("https://s.test/farsi", language="fa", allow_unknown_country=True)
        self.assertEqual(
            self.prepare(channel(tvg_id="", name="Example News"), source)[0].group, g.FA_NEWS
        )
        english = channel(name="Example News").with_attribute("tvg-language", "English")
        self.assertEqual(self.prepare(english, source)[0].group, g.EN_NEWS)

    def test_excludes_match_partial_names_and_exact_urls(self):
        item = channel(name="My CHANNEL")
        self.assertFalse(self.prepare(item, excludes=({"channel"}, set())))
        self.assertFalse(self.prepare(item, excludes=(set(), {item.url})))
        self.assertFalse(self.prepare(channel(name="Bollywood TV")))

    def test_disabled_and_renamed_overrides(self):
        self.overrides["channels"]["example.us"] = {"disabled": True}
        self.assertFalse(self.prepare(channel()))
        self.overrides["channels"]["example.us"] = {
            "name": "New Name",
            "group": "Custom",
            "language": "en",
        }
        item = self.prepare(channel())[0]
        self.assertEqual((item.name, item.group), ("New Name", "Custom"))
        self.assertFalse(self.prepare(channel(), excludes=({"new name"}, set())))

    def test_specific_variant_override_beats_base_id_override(self):
        from iptv_builder.classification import override_for

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overrides.json"
            path.write_text(
                json.dumps(
                    {
                        "channels": {
                            "Example.gb": {"group": "Base"},
                            "Example.gb@HD": {"group": "HD only"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            overrides = load_overrides(path)
            self.assertEqual(
                override_for(channel(tvg_id="Example.uk@HD"), overrides)["group"], "HD only"
            )
            self.assertEqual(
                override_for(channel(tvg_id="Example.uk@SD"), overrides)["group"], "Base"
            )

    def test_non_english_music_is_not_labeled_english_from_country(self):
        self.assertEqual(classify("Example Music", "us", "es"), g.MUS_OTHER)

    def test_override_validation_and_exclude_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "override.json"
            for data in (
                {"bad": {}},
                {"channels": {"X": {"disabled": "yes"}}},
                {"channels": {"X": {"group": 'bad"group'}}},
                {"channels": []},
            ):
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_overrides(path)
            path.write_text("# comment\nMy Channel\nhttps://s.test/live\n", encoding="utf-8")
            self.assertEqual(load_excludes(path), ({"my channel"}, {"https://s.test/live"}))
