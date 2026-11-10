import unittest

from iptv_builder.m3u import (
    Channel,
    attributes,
    base_name,
    country_from_tvg,
    parse,
    render_m3u,
    res_of,
    stable_id,
    valid_source,
    validate_output,
)
from tests.helpers import channel


class M3UTests(unittest.TestCase):
    def test_bom_crlf_blank_lines_and_directives(self):
        text = '\ufeff#EXTM3U\r\n #EXTINF:-1 tvg-id="X.us",Channel\r\n\r\n#EXTVLCOPT:http-referrer=https://site.test\r\n https://s.test/a.m3u8\r\n'
        parsed = parse(text)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].name, "Channel")
        self.assertEqual(parsed[0].headers, {"referer": "https://site.test"})

    def test_missing_url_does_not_swallow_next_channel(self):
        parsed = parse("#EXTM3U\n#EXTINF:-1,Broken\n#EXTINF:-1,Good\nhttps://s.test/good\n")
        self.assertEqual([item.name for item in parsed], ["Good"])

    def test_non_http_and_empty_names_are_ignored(self):
        self.assertFalse(
            parse("#EXTINF:-1,FTP\nftp://example.test/file\n#EXTINF:-1,\nhttps://s.test/live\n")
        )

    def test_commas_in_attributes_and_names(self):
        item = parse(
            '#EXTINF:-1 tvg-name="Name, other" tvg-id="A.us",Channel, East\nhttps://s.test/live\n'
        )[0]
        self.assertEqual(item.name, "Channel, East")
        self.assertEqual(item.renamed("New, Name").name, "New, Name")
        self.assertEqual(attributes("#EXTINF:-1 tvg-id='A.us',X")["tvg-id"], "A.us")

    def test_display_name_cannot_be_misread_as_attributes(self):
        item = Channel(('#EXTINF:-1,Name group-title="not metadata"',), "https://s.test/live")
        self.assertEqual(item.attrs, {})
        grouped = item.with_group("real group")
        self.assertEqual(grouped.group, "real group")
        self.assertEqual(grouped.name, item.name)

    def test_setting_group_does_not_replace_similarly_named_attribute(self):
        item = Channel(('#EXTINF:-1 x-group-title="keep",Name',), "https://s.test/live")
        updated = item.with_group("real group")
        self.assertEqual(updated.group, "real group")
        self.assertEqual(updated.attrs["x-group-title"], "keep")

    def test_quality_variants_share_identity_but_regional_feeds_do_not(self):
        self.assertEqual(stable_id("Example.us@SD"), stable_id("EXAMPLE.us@1080p"))
        self.assertNotEqual(stable_id("Example.us@East"), stable_id("Example.us@West"))
        self.assertEqual(stable_id("Example.gb@HD"), "example.uk")
        self.assertEqual(country_from_tvg("CNN.gb@SD"), "uk")

    def test_no_id_identity_uses_country_not_group(self):
        first = channel(tvg_id="", group="A").with_attribute("tvg-country", "us")
        second = channel(tvg_id="", group="B").with_attribute("tvg-country", "us")
        third = second.with_attribute("tvg-country", "ca")
        self.assertEqual(first.identity, second.identity)
        self.assertNotEqual(first.identity, third.identity)

    def test_quality_normalization_does_not_damage_words(self):
        self.assertEqual(base_name("Example FHD (1080p) [Geo-blocked]"), "example")
        self.assertEqual(base_name("Rashd TV"), "rashd tv")
        self.assertEqual(res_of("Example FHD"), 1080)
        self.assertEqual(res_of("Example UHD"), 2160)

    def test_probe_headers_include_vlc_kodi_and_url_options(self):
        item = channel(
            url="https://s.test/live|Origin=https%3A%2F%2Forigin.test",
            directives=(
                "#EXTVLCOPT:http-user-agent=Custom Agent",
                "#KODIPROP:inputstream.adaptive.manifest_headers=Referer=https%3A%2F%2Fsite.test",
            ),
        )
        self.assertEqual(item.request_url, "https://s.test/live")
        self.assertEqual(
            item.headers,
            {
                "user-agent": "Custom Agent",
                "referer": "https://site.test",
                "origin": "https://origin.test",
            },
        )
        other = channel(url="https://s.test/live", directives=("#EXTVLCOPT:http-user-agent=Other",))
        self.assertNotEqual(item.probe_key, other.probe_key)

    def test_group_update_replaces_extgrp_and_preserves_options(self):
        item = channel(directives=("#EXTGRP:old", "#EXTVLCOPT:http-user-agent=Agent")).with_group(
            "new"
        )
        self.assertEqual(item.group, "new")
        self.assertIn("#EXTGRP:new", item.ext)
        self.assertIn("#EXTVLCOPT:http-user-agent=Agent", item.ext)

    def test_epg_uses_one_attribute_and_round_trips(self):
        text = render_m3u([channel()], ("https://epg.test/ir.xml", "https://epg.test/us.xml"))
        self.assertEqual(text.splitlines()[0].count("url-tvg="), 1)
        self.assertIn('url-tvg="https://epg.test/ir.xml,https://epg.test/us.xml"', text)
        validate_output(text, 1)
        self.assertTrue(valid_source(text))
        validate_output(render_m3u([]), 0)
        self.assertFalse(valid_source("#EXTM3U\n"))

    def test_duplicate_or_malformed_output_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_output(render_m3u([channel(), channel()]), 2)
        with self.assertRaises(ValueError):
            validate_output("#EXTM3U\n#EXTINF:-1,Missing\n", 1)
        with self.assertRaises(ValueError):
            channel().with_group('invalid"group')

    def test_inline_credentials_are_not_copied_into_probe_key(self):
        item = Channel(("#EXTINF:-1,Channel",), "https://s.test/live|Authorization=Bearer+secret")
        self.assertNotIn("secret", item.probe_key)
