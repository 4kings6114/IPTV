import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from iptv_builder.builder import select_channels, update_history
from iptv_builder.config import HealthCacheOptions
from iptv_builder.health import MediaVariant, ProbeResult, check_candidates, probe
from iptv_builder.m3u import parse
from iptv_builder.personalization import (
    editor_manifest,
    expand_variants,
    load_personalization,
    personalized_outputs,
)
from tests.helpers import HLS, Response, channel, settings

MASTER = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=5000000,AVERAGE-BANDWIDTH=4000000,RESOLUTION=1920x1080\nhigh.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nlow.m3u8\n"


class BitrateTests(unittest.TestCase):
    @patch("iptv_builder.health.urlopen")
    def test_peak_master_rate_is_conservative_and_real_lower_variants_are_verified(self, opened):
        opened.side_effect = [
            Response(MASTER),
            Response(HLS, url="https://streams.test/high.m3u8"),
            Response(HLS, url="https://streams.test/low.m3u8"),
        ]
        result = probe(channel(), settings())
        self.assertEqual(result.bitrate, 5000000)
        self.assertEqual(result.bitrate_source, "hls_master_max")
        self.assertEqual({item.bitrate for item in result.variants}, {5000000, 800000})
        item = channel()
        expanded, results, history = expand_variants([item], {item.probe_key: result}, {})
        profiles = {
            "slow": {
                "languages": [],
                "countries": [],
                "max_resolution": 720,
                "max_bitrate": 1000000,
            }
        }
        outputs, report = personalized_outputs(
            settings(),
            [item],
            [item],
            {item.probe_key: result},
            {},
            [],
            set(),
            profiles,
            (),
            select_channels,
        )
        self.assertEqual(parse(outputs["profile-slow.m3u"])[0].url, "https://streams.test/low.m3u8")
        self.assertEqual(report["profiles"]["slow"]["channels"], 1)
        self.assertEqual(len(expanded), 3)
        self.assertTrue(all(value.healthy for value in results.values()))

    @patch("iptv_builder.health.urlopen")
    def test_advertised_but_failed_child_is_never_exported_as_healthy(self, opened):
        opened.side_effect = [
            Response(MASTER),
            Response(HLS),
            HTTPError("https://streams.test/low.m3u8", 403, "restricted", {}, io.BytesIO()),
        ]
        item = channel()
        result = probe(item, settings())
        self.assertEqual(len(result.variants), 1)
        self.assertNotIn("low.m3u8", str(result.variants))
        output, report = personalized_outputs(
            settings(),
            [item],
            [item],
            {item.probe_key: result},
            {},
            [],
            set(),
            {
                "slow": {
                    "languages": [],
                    "countries": [],
                    "max_resolution": 720,
                    "max_bitrate": 1000000,
                }
            },
            (),
            select_channels,
        )
        self.assertFalse(parse(output["profile-slow.m3u"]))
        self.assertEqual(report["profiles"]["slow"]["over_bitrate_omitted"], 1)

    @patch("iptv_builder.health.urlopen")
    def test_unknown_and_invalid_advertised_bitrates_are_not_guessed_from_resolution(self, opened):
        for value in ("0", "-1", "NaN", "999999999999"):
            master = MASTER.replace(
                b"BANDWIDTH=5000000,AVERAGE-BANDWIDTH=4000000", f"BANDWIDTH={value}".encode()
            )
            opened.side_effect = [Response(master), Response(HLS), Response(HLS)]
            result = probe(channel(), settings())
            self.assertEqual(result.bitrate, 0)
            self.assertEqual(result.variants[0].bitrate, 0)
        opened.side_effect = [Response(HLS)]
        self.assertEqual(probe(channel(name="Channel 1080p"), settings()).bitrate, 0)

    def test_unknown_bitrate_is_strictly_omitted_unless_explicitly_allowed(self):
        item = channel()
        profiles = {
            "slow": {
                "languages": [],
                "countries": [],
                "max_resolution": 720,
                "max_bitrate": 2000000,
            }
        }
        args = [
            settings(),
            [item],
            [item],
            {item.probe_key: ProbeResult("healthy", "media", 720)},
            {},
            [],
            set(),
            profiles,
            (),
            select_channels,
        ]
        output, report = personalized_outputs(*args)
        self.assertFalse(parse(output["profile-slow.m3u"]))
        self.assertEqual(report["profiles"]["slow"]["unknown_bitrate_omitted"], 1)
        profiles["slow"]["include_unknown_bitrate"] = True
        output, report = personalized_outputs(*args)
        self.assertEqual(len(parse(output["profile-slow.m3u"])), 1)
        self.assertEqual(report["profiles"]["slow"]["unknown_bitrate_included"], 1)

    def test_profile_bitrate_limits_are_validated_without_coercion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for value in (True, -1, "1000", 1000000001):
                (root / "profiles.json").write_text(
                    json.dumps({"profiles": {"slow": {"max_bitrate": value}}})
                )
                with self.assertRaises(ValueError):
                    load_personalization(settings(), root)
            (root / "profiles.json").write_text('{"profiles":{"slow":{"max_bitrate":2000000}}}')
            self.assertEqual(
                load_personalization(settings(), root)[1]["slow"]["max_bitrate"], 2000000
            )

    @patch("iptv_builder.health.probe")
    def test_cached_variant_bitrate_and_observation_times_survive_without_new_success(self, mocked):
        from datetime import datetime, timezone

        stamp = datetime.now(timezone.utc).isoformat()
        item = channel()
        result = ProbeResult(
            "healthy",
            "valid",
            1080,
            checked_at=stamp,
            bitrate=4000000,
            bitrate_source="hls_master_max",
            variants=(MediaVariant("https://streams.test/low.m3u8", 360, 800000, "hls_peak"),),
        )
        history = update_history({item.probe_key: result}, {}, stamp)
        with redirect_stdout(io.StringIO()):
            cached = check_candidates(
                [item], settings(health_cache=HealthCacheOptions(hours=6)), history
            )[item.probe_key]
        mocked.assert_not_called()
        self.assertTrue(cached.cached)
        self.assertEqual(cached.bitrate, 4000000)
        self.assertEqual(cached.variants, result.variants)
        self.assertEqual(update_history({item.probe_key: cached}, history, stamp), history)

    @patch("iptv_builder.health.urlopen")
    def test_cross_origin_hls_child_does_not_receive_authorization_or_cookie(self, opened):
        master = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\nhttps://other.test/low.m3u8\n"
        item = channel(
            url="https://streams.test/live.m3u8|Authorization=secret&Cookie=session&User-Agent=Agent"
        )
        opened.side_effect = [Response(master), Response(HLS, url="https://other.test/low.m3u8")]
        result = probe(item, settings())
        sent = dict((key.lower(), value) for key, value in opened.call_args.args[0].header_items())
        self.assertNotIn("authorization", sent)
        self.assertNotIn("cookie", sent)
        expanded, _, _ = expand_variants([item], {item.probe_key: result}, {})
        self.assertNotIn("authorization", expanded[-1].headers)
        self.assertEqual(expanded[-1].headers["user-agent"], "Agent")

    def test_editor_manifest_preserves_epg_identity_and_safe_request_headers_but_omits_private_data(
        self,
    ):
        good = channel(
            tvg_id="guide.numeric", directives=("#EXTVLCOPT:http-referrer=https://player.test/",)
        ).with_attribute("x-original-tvg-id", "Example.us@HD")
        private = channel(tvg_id="Private.us", url="https://stream.test/?token=do-not-publish")
        drm = channel(
            tvg_id="DRM.us",
            url="https://stream.test/drm",
            directives=(
                "#KODIPROP:inputstream.adaptive.license_key=https://license.test/?token=secret",
            ),
        )
        results = {
            item.probe_key: ProbeResult(
                "healthy", "valid", 720, bitrate=1000000, bitrate_source="hls_peak"
            )
            for item in (good, private, drm)
        }
        manifest = editor_manifest([good, private, drm], results, {}, (), {good.identity}, {})
        self.assertEqual(len(manifest["channels"]), 1)
        row = manifest["channels"][0]
        exported = parse("#EXTM3U\n" + row["m3u"])[0]
        self.assertEqual(exported.identity, good.identity)
        self.assertEqual(exported.headers["referer"], "https://player.test/")
        self.assertNotIn("secret", json.dumps(manifest))
        self.assertNotIn("do-not-publish", json.dumps(manifest))
        self.assertEqual(manifest["private_identities_omitted"], 2)
