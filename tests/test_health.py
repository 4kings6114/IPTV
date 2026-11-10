import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from iptv_builder.health import check_candidates, probe
from tests.helpers import HLS, TS, Response, channel, settings


def http_error(code):
    return HTTPError("https://streams.test/live.m3u8", code, "fixture", {}, io.BytesIO())


class HealthTests(unittest.TestCase):
    @patch("iptv_builder.health.urlopen")
    def test_html_200_is_not_accepted_and_only_get_is_used(self, opened):
        opened.return_value = Response(b"<!doctype html><html>Error</html>", "text/html")
        result = probe(channel(), settings())
        self.assertEqual(result.status, "invalid")
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(opened.call_args.args[0].method, "GET")

    @patch("iptv_builder.health.urlopen")
    def test_html_disguised_as_video_json_and_empty_are_rejected(self, opened):
        for body in (b"<html>Denied</html>", b'{"error": "denied"}', b""):
            with self.subTest(body=body):
                opened.return_value = Response(body, "video/mp2t")
                self.assertEqual(probe(channel(), settings()).status, "invalid")

    @patch("iptv_builder.health.urlopen")
    def test_valid_hls_requires_segments_not_just_a_header(self, opened):
        opened.return_value = Response(HLS, "text/plain")
        self.assertTrue(probe(channel(), settings()).healthy)
        opened.return_value = Response(b"#EXTM3U\n", "application/vnd.apple.mpegurl")
        self.assertFalse(probe(channel(), settings()).healthy)

    @patch("iptv_builder.health.urlopen")
    def test_401_403_are_restricted_not_healthy(self, opened):
        for code in (401, 403):
            with self.subTest(code=code):
                opened.reset_mock()
                opened.side_effect = http_error(code)
                result = probe(channel(), settings())
                self.assertEqual(result.status, "restricted")
                self.assertEqual(result.reason, f"HTTP {code}")
                self.assertEqual(opened.call_count, 1)

    @patch("iptv_builder.health.urlopen")
    def test_transient_503_is_retried_before_success(self, opened):
        opened.side_effect = [http_error(503), Response(HLS)]
        self.assertTrue(probe(channel(), settings()).healthy)
        self.assertEqual(opened.call_count, 2)

    @patch("iptv_builder.health.urlopen")
    def test_404_is_not_retried(self, opened):
        opened.side_effect = http_error(404)
        self.assertEqual(probe(channel(), settings()).reason, "HTTP 404")
        self.assertEqual(opened.call_count, 1)

    @patch("iptv_builder.health.urlopen")
    def test_timeout_and_dns_have_distinct_statuses(self, opened):
        for error, status in (
            (TimeoutError(), "timeout"),
            (URLError(TimeoutError()), "timeout"),
            (URLError("DNS"), "network_error"),
        ):
            opened.reset_mock()
            opened.side_effect = error
            self.assertEqual(probe(channel(), settings()).status, status)
            self.assertEqual(opened.call_count, 2)

    @patch("iptv_builder.health.urlopen")
    def test_range_416_falls_back_to_bounded_get_without_range(self, opened):
        opened.side_effect = [http_error(416), Response(HLS)]
        self.assertTrue(probe(channel(), settings()).healthy)
        requests = [call.args[0] for call in opened.call_args_list]
        self.assertIn("Range", dict(requests[0].header_items()))
        self.assertNotIn("Range", dict(requests[1].header_items()))

    @patch("iptv_builder.health.urlopen")
    def test_master_checks_child_and_uses_healthy_lower_variant(self, opened):
        master = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=2000,RESOLUTION=1920x1080\nhigh.m3u8\n#EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=1280x720\nlow.m3u8\n"
        opened.side_effect = [Response(master), http_error(404), Response(HLS)]
        result = probe(channel(), settings())
        self.assertTrue(result.healthy)
        self.assertEqual(result.resolution, 720)
        self.assertEqual(opened.call_args.args[0].full_url, "https://streams.test/low.m3u8")

    @patch("iptv_builder.health.urlopen")
    def test_segment_sampling_is_optional_and_forwards_headers(self, opened):
        item = channel(
            directives=(
                "#EXTVLCOPT:http-referrer=https://origin.test/",
                "#EXTVLCOPT:http-user-agent=Required Agent",
            )
        )
        opened.side_effect = [
            Response(HLS),
            Response(TS, "video/mp2t", "https://streams.test/segment.ts"),
        ]
        self.assertTrue(probe(item, settings(check_segments=True)).healthy)
        for call in opened.call_args_list:
            headers = {key.lower(): value for key, value in call.args[0].header_items()}
            self.assertEqual(headers["referer"], "https://origin.test/")
            self.assertEqual(headers["user-agent"], "Required Agent")

    @patch("iptv_builder.health.urlopen")
    def test_failed_sampled_segment_is_not_healthy(self, opened):
        opened.side_effect = [Response(HLS), http_error(403)]
        self.assertEqual(probe(channel(), settings(check_segments=True)).status, "restricted")

    @patch("iptv_builder.health.urlopen")
    def test_cyclic_master_and_non_http_segments_are_invalid(self, opened):
        opened.return_value = Response(b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nlive.m3u8\n")
        self.assertEqual(probe(channel(), settings()).status, "invalid")
        opened.return_value = Response(b"#EXTM3U\n#EXTINF:6,\nfile:///etc/passwd\n")
        self.assertEqual(probe(channel(), settings()).status, "invalid")

    @patch("iptv_builder.health.urlopen")
    def test_oversized_manifest_is_not_silently_truncated(self, opened):
        opened.return_value = Response(HLS + b"# comment\n" * 100)
        self.assertEqual(probe(channel(), settings(probe_bytes=512)).status, "invalid")

    @patch("iptv_builder.health.urlopen")
    def test_transport_stream_mp4_and_dash(self, opened):
        for body, content_type in (
            (TS, "text/plain"),
            (b"\x00\x00\x00\x18ftypisom", "application/octet-stream"),
            (
                b'<MPD xmlns="urn:mpeg:dash:schema:mpd:2011"><Period><AdaptationSet><Representation id="video" bandwidth="1000"><BaseURL>video.mp4</BaseURL></Representation></AdaptationSet></Period></MPD>',
                "application/dash+xml",
            ),
        ):
            with self.subTest(body=body[:20]):
                opened.return_value = Response(body, content_type)
                self.assertTrue(probe(channel(), settings()).healthy)

    @patch("iptv_builder.health.urlopen")
    def test_empty_malformed_or_oversized_dash_is_invalid(self, opened):
        for body in (b"<MPD></MPD>", b"<MPD><Period></MPD>", b"<MPD>" + b" " * 1024 + b"</MPD>"):
            with self.subTest(body=body[:30]):
                opened.return_value = Response(body, "application/dash+xml")
                self.assertEqual(probe(channel(), settings(probe_bytes=512)).status, "invalid")

    @patch("iptv_builder.health.probe")
    def test_duplicate_requests_are_probed_once(self, mocked):
        from iptv_builder.health import ProbeResult

        mocked.return_value = ProbeResult("healthy", "fixture")
        first = channel()
        second = channel(tvg_id="Another.us")
        results = check_candidates([first, second], settings())
        self.assertEqual(len(results), 1)
        self.assertEqual(mocked.call_count, 1)

    @patch("iptv_builder.health.urlopen")
    def test_interrupted_http_body_is_retried(self, opened):
        from http.client import IncompleteRead

        opened.side_effect = [IncompleteRead(b"partial"), Response(HLS)]
        self.assertTrue(probe(channel(), settings()).healthy)
        self.assertEqual(opened.call_count, 2)

    @patch("iptv_builder.health.urlopen")
    def test_invalid_header_encoding_is_reported_not_raised(self, opened):
        opened.side_effect = UnicodeEncodeError("ascii", "فارسی", 0, 1, "fixture")
        self.assertEqual(probe(channel(), settings()).status, "invalid")
        self.assertEqual(opened.call_count, 1)

    @patch("iptv_builder.health.urlopen")
    def test_disabled_checks_do_not_make_network_requests(self, opened):
        self.assertEqual(probe(channel(), settings(check_links=False)).status, "unchecked")
        opened.assert_not_called()
