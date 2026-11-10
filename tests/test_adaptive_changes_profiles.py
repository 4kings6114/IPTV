import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from unittest.mock import patch

from iptv_builder.builder import select_channels, update_history
from iptv_builder.changes import channel_changes, remember_channels
from iptv_builder.config import HealthCacheOptions, HostLimitOptions
from iptv_builder.health import ProbeResult, check_candidates, retry_after
from iptv_builder.m3u import parse
from iptv_builder.personalization import load_personalization, personalized_outputs, source_rank
from tests.helpers import HLS, channel, settings

OK = ProbeResult("healthy", "Valid HLS", 720)
BAD = ProbeResult("timeout", "Request timed out")


class AdaptiveHealthTests(unittest.TestCase):
    def setUp(self):
        self.item = channel()
        self.now = datetime.now(timezone.utc)
        self.config = settings(
            health_cache=HealthCacheOptions(hours=1, max_hours=8, successes_per_step=2)
        )

    def history(self, hours=0.5, **extra):
        return {
            self.item.probe_key: {
                "status": "healthy",
                "reason": "valid",
                "resolution": 480,
                "checked_at": (self.now - timedelta(hours=hours)).isoformat(),
                "consecutive_successes": 2,
                **extra,
            }
        }

    @patch("iptv_builder.health.probe", return_value=OK)
    def test_cached_success_preserves_observation_time_resolution_and_streak(self, probe):
        old = self.history()
        with redirect_stdout(io.StringIO()):
            results = check_candidates([self.item], self.config, old)
        probe.assert_not_called()
        result = results[self.item.probe_key]
        self.assertTrue(result.cached)
        self.assertEqual(result.checked_at, old[self.item.probe_key]["checked_at"])
        self.assertEqual(result.resolution, 480)
        self.assertEqual(update_history(results, old, self.now.isoformat()), old)

    @patch("iptv_builder.health.probe", return_value=OK)
    def test_adaptive_ttl_expiry_failure_future_and_corruption_force_fresh_checks(self, probe):
        cases = [
            self.history(hours=3),
            self.history(status="timeout"),
            self.history(hours=-1),
            self.history(checked_at="bad"),
            self.history(consecutive_successes="many"),
            self.history(resolution="HD"),
            self.history(checked_at="2026-01-01T00:00:00"),
        ]
        for old in cases:
            with self.subTest(old=old), redirect_stdout(io.StringIO()):
                result = check_candidates([self.item], self.config, old)[self.item.probe_key]
                self.assertFalse(result.cached)
                self.assertTrue(result.checked_at)
        self.assertEqual(probe.call_count, len(cases))

    @patch("iptv_builder.health.probe", return_value=OK)
    def test_expanded_ttl_and_force_check_and_disabled_checks(self, probe):
        old = self.history(hours=3, consecutive_successes=4)
        with redirect_stdout(io.StringIO()):
            self.assertTrue(
                check_candidates([self.item], self.config, old)[self.item.probe_key].cached
            )
            forced = replace(self.config, health_cache=replace(self.config.health_cache, hours=0))
            self.assertFalse(check_candidates([self.item], forced, old)[self.item.probe_key].cached)
        self.assertEqual(probe.call_count, 1)
        probe.reset_mock()
        unchecked = check_candidates([self.item], replace(self.config, check_links=False), old)
        self.assertEqual(unchecked[self.item.probe_key].status, "unchecked")
        probe.assert_not_called()

    def test_fresh_observations_advance_only_the_appropriate_streak(self):
        old = self.history(consecutive_successes=4)
        now = self.now.isoformat()
        healthy = update_history({self.item.probe_key: OK}, old, now)[self.item.probe_key]
        self.assertEqual(healthy["consecutive_successes"], 5)
        failed = update_history({self.item.probe_key: BAD}, old, now)[self.item.probe_key]
        self.assertEqual(failed["consecutive_successes"], 0)
        self.assertEqual(failed["consecutive_failures"], 1)
        self.assertEqual(failed["last_success"], old[self.item.probe_key].get("last_success"))

    def test_retry_after_is_bounded_and_invalid_values_are_ignored(self):
        self.assertEqual(retry_after("120", 3), 3)
        self.assertEqual(retry_after("-5", 3), 0)
        self.assertEqual(retry_after("not-a-date", 3), 0)
        self.assertEqual(retry_after("Tue, 10 Nov 2020 12:00:00 GMT", 3), 0)

    def test_real_parallel_requests_obey_per_host_concurrency(self):
        state = {"active": 0, "maximum": 0, "requests": 0}
        lock = Lock()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                with lock:
                    state["active"] += 1
                    state["maximum"] = max(state["maximum"], state["active"])
                    state["requests"] += 1
                time.sleep(0.03)
                self.send_response(200)
                self.send_header("Content-Length", str(len(HLS)))
                self.end_headers()
                self.wfile.write(HLS)
                with lock:
                    state["active"] -= 1

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            channels = [
                channel(url=f"http://127.0.0.1:{server.server_port}/{index}.m3u8")
                for index in range(8)
            ]
            with redirect_stdout(io.StringIO()):
                results = check_candidates(
                    channels, settings(max_workers=8, host_limit=HostLimitOptions(concurrent=2))
                )
            self.assertEqual(state["requests"], 8)
            self.assertGreater(state["maximum"], 1)
            self.assertLessEqual(state["maximum"], 2)
            self.assertTrue(all(result.healthy for result in results.values()))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class ChangeTests(unittest.TestCase):
    def test_url_headers_and_quality_changes_are_not_added_or_removed_channels(self):
        old = channel(url="https://old.test/live", name="Example (480p)")
        new = channel(url="https://new.test/live", name="Canonical (1080p)")
        changes = channel_changes(
            [old],
            [new],
            {new.probe_key: ProbeResult("healthy", "valid", 1080)},
            {old.probe_key: {"resolution": 480}},
            {},
        )
        self.assertFalse(changes["added_channels"])
        self.assertFalse(changes["removed_channels"])
        self.assertEqual(len(changes["url_changes"]), 1)
        self.assertEqual(changes["quality_changes"][0]["direction"], "up")
        header = replace(old, ext=(*old.ext, "#EXTVLCOPT:http-referrer=https://player.test/"))
        delta = channel_changes([old], [header], {header.probe_key: OK}, {}, {})
        self.assertTrue(delta["url_changes"][0]["headers_changed"])

    def test_true_additions_removals_and_recoveries_are_distinct(self):
        old, new = channel(), channel(tvg_id="New.us", url="https://new.test/stream")
        changes = channel_changes([old], [new], {new.probe_key: OK}, {}, {})
        self.assertEqual(len(changes["added_channels"]), 1)
        self.assertEqual(len(changes["removed_channels"]), 1)
        recovered = channel_changes(
            [], [old], {old.probe_key: OK}, {}, {old.identity: {"status": "healthy"}}
        )
        self.assertFalse(recovered["added_channels"])
        self.assertEqual(recovered["recovered_channels"][0]["kind"], "returned")
        health = channel_changes(
            [old], [old], {old.probe_key: OK}, {old.probe_key: {"status": "timeout"}}, {}
        )
        self.assertEqual(health["recovered_channels"][0]["kind"], "health_restored")
        cached = channel_changes(
            [old],
            [old],
            {old.probe_key: replace(OK, cached=True)},
            {},
            {old.identity: {"status": "timeout"}},
        )
        self.assertFalse(cached["recovered_channels"])

    def test_unverified_return_is_neither_new_nor_falsely_recovered(self):
        item = channel()
        changes = channel_changes(
            [],
            [item],
            {item.probe_key: ProbeResult("unchecked", "disabled")},
            {},
            {item.identity: {"status": "healthy"}},
        )
        self.assertFalse(changes["added_channels"])
        self.assertFalse(changes["recovered_channels"])
        self.assertEqual(len(changes["returned_unverified_channels"]), 1)

    def test_unknown_quality_is_not_falsely_reported_as_upgrade(self):
        old = channel(name="Example")
        new = channel(name="Example HD")
        self.assertFalse(
            channel_changes([old], [new], {new.probe_key: OK}, {}, {})["quality_changes"]
        )

    def test_tombstones_expire_and_corrupt_or_future_dates_are_ignored(self):
        now = datetime.now(timezone.utc)
        previous = {
            "old": {"last_seen": (now - timedelta(days=91)).isoformat()},
            "recent": {"last_seen": (now - timedelta(days=2)).isoformat(), "status": "healthy"},
            "future": {"last_seen": (now + timedelta(days=1)).isoformat()},
            "invalid": {"last_seen": "bad"},
        }
        item = channel()
        remembered = remember_channels(previous, [item], {item.probe_key: OK}, now.isoformat())
        self.assertEqual(set(remembered), {"recent", item.identity})


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = settings()

    def test_profile_selects_actual_lower_quality_alternative_not_just_filters_main(self):
        high = (
            channel(name="Example 1080p", url="https://s.test/high")
            .with_attribute("tvg-language", "en")
            .with_attribute("tvg-country", "us")
        )
        low = (
            channel(name="Example 480p", url="https://s.test/low")
            .with_attribute("tvg-language", "en")
            .with_attribute("tvg-country", "us")
        )
        results = {
            high.probe_key: ProbeResult("healthy", "valid", 1080),
            low.probe_key: ProbeResult("healthy", "valid", 480),
        }
        (self.root / "favorites.json").write_text('{"channels":["Example.us"]}')
        (self.root / "profiles.json").write_text(
            '{"profiles":{"limited":{"languages":["English"],"countries":["US"],"max_resolution":720}}}'
        )
        favorites, profiles = load_personalization(self.config, self.root)
        output, report = personalized_outputs(
            self.config,
            [high, low],
            [high],
            results,
            {},
            [],
            favorites,
            profiles,
            (),
            select_channels,
        )
        self.assertEqual(parse(output["profile-limited.m3u"])[0].url, low.url)
        self.assertEqual(parse(output["favorites.m3u"])[0].url, high.url)
        self.assertEqual(report["profiles"]["limited"]["channels"], 1)

    def test_unknown_quality_is_excluded_under_a_cap_unless_explicitly_allowed(self):
        item = channel(name="Example")
        profile = {"limited": {"max_resolution": 720, "languages": [], "countries": []}}
        output, report = personalized_outputs(
            self.config,
            [item],
            [item],
            {item.probe_key: ProbeResult("healthy", "valid")},
            {},
            [],
            set(),
            profile,
            (),
            select_channels,
        )
        self.assertFalse(parse(output["profile-limited.m3u"]))
        self.assertEqual(report["profiles"]["limited"]["unknown_quality_omitted"], 1)
        profile["limited"]["include_unknown_resolution"] = True
        output, _ = personalized_outputs(
            self.config,
            [item],
            [item],
            {item.probe_key: OK},
            {},
            [],
            set(),
            profile,
            (),
            select_channels,
        )
        self.assertEqual(len(parse(output["profile-limited.m3u"])), 1)

    def test_preferred_sources_only_change_selection_among_healthy_eligible_candidates(self):
        high = channel(url="https://unreliable.test/high", name="Example 1080p")
        low = channel(url="https://preferred.test/low", name="Example 480p")
        config = settings(preferred_sources=("preferred.test",))
        chosen = select_channels(
            [high, low], {high.probe_key: OK, low.probe_key: OK}, {}, [], config
        )
        self.assertEqual(chosen.channels[0].url, low.url)
        chosen = select_channels(
            [high, low], {high.probe_key: OK, low.probe_key: BAD}, {}, [], config
        )
        self.assertEqual(chosen.channels[0].url, high.url)
        self.assertEqual(
            source_rank(channel(url="https://preferred.test.evil/x"), ("https://preferred.test",)),
            1,
        )

    def test_invalid_profiles_and_favorites_fail_before_any_build(self):
        cases = [
            {"profiles": {"../../escape": {}}},
            {"profiles": {"okay": {"max_resolution": True}}},
            {"profiles": {"okay": {"countries": ["USA"]}}},
            {"profiles": {"okay": {"typo": True}}},
            {"profiles": {"okay": {"include_unknown_resolution": "yes"}}},
        ]
        for data in cases:
            (self.root / "profiles.json").write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_personalization(self.config, self.root)
        (self.root / "profiles.json").unlink()
        (self.root / "favorites.json").write_text('{"channels":[12]}')
        with self.assertRaises(ValueError):
            load_personalization(self.config, self.root)


class RedirectLimitTests(unittest.TestCase):
    def test_redirect_destinations_share_host_limits_and_do_not_receive_private_headers(self):
        state = {"active": 0, "maximum": 0, "auth": [], "original_auth": []}
        lock = Lock()

        class Target(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                with lock:
                    state["active"] += 1
                    state["maximum"] = max(state["maximum"], state["active"])
                    state["auth"].append(
                        (self.headers.get("Authorization"), self.headers.get("Cookie"))
                    )
                time.sleep(0.02)
                self.send_response(200)
                self.send_header("Content-Length", str(len(HLS)))
                self.end_headers()
                with lock:
                    state["active"] -= 1
                self.wfile.write(HLS)

        target = ThreadingHTTPServer(("127.0.0.1", 0), Target)

        class Redirect(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                state["original_auth"].append(self.headers.get("Authorization"))
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.server_port}/live")
                self.end_headers()

        origins = [ThreadingHTTPServer(("127.0.0.1", 0), Redirect) for _ in range(2)]
        servers = [target, *origins]
        threads = [Thread(target=server.serve_forever, daemon=True) for server in servers]
        for thread in threads:
            thread.start()
        try:
            items = [
                channel(
                    url=f"http://127.0.0.1:{origins[index % 2].server_port}/{index}|Authorization=private&Cookie=session"
                )
                for index in range(6)
            ]
            with redirect_stdout(io.StringIO()):
                results = check_candidates(
                    items, settings(max_workers=6, host_limit=HostLimitOptions(concurrent=1))
                )
            self.assertTrue(all(value.healthy for value in results.values()))
            self.assertEqual(state["maximum"], 1)
            self.assertEqual(state["auth"], [(None, None)] * 6)
            self.assertEqual(state["original_auth"], ["private"] * 6)
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join(timeout=5)

    @patch("iptv_builder.health.urlopen")
    def test_non_http_cyclic_and_missing_redirect_destinations_are_invalid(self, opened):
        from urllib.error import HTTPError

        from iptv_builder.health import probe

        for headers in (
            {"Location": "file:///etc/passwd"},
            {"Location": channel().request_url},
            {},
        ):
            with self.subTest(headers=headers):
                opened.side_effect = HTTPError(
                    channel().request_url, 302, "redirect", headers, io.BytesIO()
                )
                self.assertEqual(probe(channel(), settings()).status, "invalid")
