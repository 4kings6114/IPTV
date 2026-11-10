import json
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import replace
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from iptv_builder.config import PlaybackOptions
from iptv_builder.dashboard import render_dashboard
from iptv_builder.playback import sample, sample_channels
from tests.helpers import channel, settings

STREAMS = json.dumps(
    {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "height": 720},
            {"codec_type": "audio", "codec_name": "aac"},
        ]
    }
).encode()


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.config = settings(playback=PlaybackOptions(enabled=True))

    @patch("iptv_builder.playback.shutil.which", side_effect=lambda name: "/bin/" + name)
    @patch(
        "iptv_builder.playback._run",
        side_effect=[(0, STREAMS), (0, b"out_time_us=2000000\nprogress=end\n")],
    )
    def test_bounded_inspection_and_real_decode_must_both_succeed(self, run, which):
        item = channel(directives=("#EXTVLCOPT:http-referrer=https://player.test/",))
        result = sample(item, self.config)
        self.assertEqual(result["status"], "decoded")
        self.assertTrue(result["audio"] and result["video"])
        self.assertEqual(result["resolution"], 720)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertIn("-t", commands[1])
        self.assertIn("-protocol_whitelist", commands[0])
        self.assertNotIn(
            "file", commands[0][commands[0].index("-protocol_whitelist") + 1].split(",")
        )
        self.assertIn("referer: https://player.test/\r\n", commands[0])
        self.assertTrue(
            all(0 < call.args[1] <= self.config.playback.timeout for call in run.call_args_list)
        )

    @patch("iptv_builder.playback.shutil.which", return_value="/bin/tool")
    def test_empty_streams_tool_failures_bad_json_and_timeout_are_not_playable(self, which):
        for responses, status in [
            ([(1, b"private-url")], "failed"),
            ([(0, b"{}")], "failed"),
            ([(0, b"broken")], "failed"),
            ([(0, STREAMS), (0, b"out_time_us=0")], "failed"),
            ([(0, STREAMS), (1, b"out_time_us=10")], "failed"),
        ]:
            with patch("iptv_builder.playback._run", side_effect=responses):
                result = sample(channel(), self.config)
                self.assertEqual(result["status"], status)
                self.assertNotIn("private-url", json.dumps(result))
        with patch("iptv_builder.playback._run", side_effect=subprocess.TimeoutExpired("tool", 1)):
            self.assertEqual(sample(channel(), self.config)["status"], "timeout")

    @patch("iptv_builder.playback.shutil.which", return_value=None)
    def test_missing_ffmpeg_is_reported_without_breaking_build(self, which):
        self.assertEqual(sample(channel(), self.config)["status"], "unavailable")

    @patch(
        "iptv_builder.playback.shutil.which",
        side_effect=lambda name: "/bin/ffmpeg" if name == "ffmpeg" else None,
    )
    @patch("iptv_builder.playback._run", return_value=(0, b"out_time_us=10000\n"))
    def test_ffmpeg_can_decode_even_when_ffprobe_is_not_available(self, run, which):
        self.assertEqual(sample(channel(), self.config)["status"], "decoded")
        self.assertEqual(run.call_count, 1)

    @patch("iptv_builder.playback.sample", return_value={"status": "decoded"})
    def test_sample_budget_and_oldest_first_rotation(self, mocked):
        first = channel(tvg_id="One.us", url="https://s.test/one")
        second = channel(tvg_id="Two.us", url="https://s.test/two")
        config = replace(self.config, playback=replace(self.config.playback, max_channels=1))
        report, state = sample_channels(
            [first, second], config, {first.probe_key: {"checked_at": "2026-01-01T00:00:00+00:00"}}
        )
        self.assertEqual(report["sampled"], 1)
        self.assertEqual(report["samples"][0]["identity"], second.identity)
        self.assertIn(second.probe_key, state)
        mocked.reset_mock()
        self.assertFalse(sample_channels([first], settings(), state)[0]["enabled"])
        mocked.assert_not_called()

    @patch("iptv_builder.playback.subprocess.run")
    def test_subprocess_has_no_shell_closed_stdin_and_a_wall_clock_timeout(self, run):
        from iptv_builder.playback import _run

        run.return_value.returncode = 1
        code, output = _run(["ffmpeg", "--invalid"], 0.01)
        self.assertEqual(code, 1)
        self.assertEqual(output, b"")
        self.assertEqual(run.call_args.kwargs["timeout"], 0.01)
        self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotIn("shell", run.call_args.kwargs)

    @unittest.skipUnless(shutil.which("ffmpeg"), "optional ffmpeg executable is not installed")
    def test_actual_audio_video_decode_over_local_http(self):
        ffmpeg = shutil.which("ffmpeg")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            generated = subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=160x90:rate=10",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=44100",
                    "-t",
                    "1",
                    "-threads",
                    "1",
                    "-c:v",
                    "mpeg2video",
                    "-c:a",
                    "mp2",
                    "-f",
                    "mpegts",
                    str(root / "sample.ts"),
                ],
                capture_output=True,
                timeout=10,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr.decode())

            class Handler(SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=str(root), **kwargs)

                def log_message(self, *args):
                    pass

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                item = channel(url=f"http://127.0.0.1:{server.server_port}/sample.ts")
                config = replace(self.config, playback=replace(self.config.playback, seconds=0.5))
                result = sample(item, config)
                self.assertEqual(result["status"], "decoded", result)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


class DashboardTests(unittest.TestCase):
    def test_report_json_is_safe_from_script_breakout_and_round_trips(self):
        malicious = '</script><script>alert("x")</script>&\u2028'
        report = {"channels": 1, "channel_catalog": [{"name": malicious}], "generated_at": "now"}
        html = render_dashboard(report)
        self.assertNotIn(malicious, html)
        payload = html.split('<script id="report" type="application/json">', 1)[1].split(
            "</script>", 1
        )[0]
        self.assertEqual(json.loads(payload), report)
        self.assertNotIn("<script", payload)
        self.assertIn("textContent", html)
        self.assertNotIn("innerHTML", html)
        self.assertNotIn("fetch(", html)
        self.assertIn("connect-src 'none'", html)
        self.assertIn('id="health"', html)
        self.assertIn('id="epg"', html)

    @unittest.skipUnless(shutil.which("node"), "Node is only used for optional JS syntax QA")
    def test_dashboard_javascript_parses(self):
        html = render_dashboard({})
        script = html.rsplit("<script>", 1)[1].split("</script>", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dashboard.js"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run(
                ["node", "--check", str(path)], capture_output=True, text=True, timeout=10
            )
            self.assertEqual(result.returncode, 0, result.stderr)
