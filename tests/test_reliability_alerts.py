import io
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from iptv_builder.alerts import alert_plan, watched_failures
from iptv_builder.builder import select_channels, update_history
from iptv_builder.health import ProbeResult
from iptv_builder.reliability import daily_observation, reliability, stability_rank
from tests.helpers import channel, settings

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)


def days(ok, failed=0):
    return {
        "daily": [
            {"date": (NOW - timedelta(days=age)).date().isoformat(), "ok": ok, "failed": failed}
            for age in range(5)
        ]
    }


class ReliabilityTests(unittest.TestCase):
    def test_one_day_cannot_count_as_several_days_or_continuous_uptime(self):
        record = {}
        for second in range(60):
            stamp = (NOW + timedelta(seconds=second)).isoformat()
            record = {"checked_at": stamp, "daily": daily_observation(record, stamp, True)}
        score = reliability(record, NOW.isoformat())
        self.assertEqual(score["observed_days"], 1)
        self.assertLessEqual(score["observations"], 24)
        self.assertLess(score["score"], 100)
        self.assertEqual(stability_rank(record), 10)

    def test_cache_unchecked_and_replayed_observation_do_not_add_confidence(self):
        item = channel()
        stamp = NOW.isoformat()
        old = {
            item.probe_key: {"checked_at": stamp, "daily": days(1)["daily"], "status": "healthy"}
        }
        cached = ProbeResult("healthy", "cached", cached=True, checked_at=stamp)
        self.assertEqual(update_history({item.probe_key: cached}, old, stamp), old)
        self.assertEqual(
            update_history({item.probe_key: ProbeResult("unchecked", "disabled")}, old, stamp), old
        )
        self.assertEqual(
            daily_observation(old[item.probe_key], stamp, True),
            sorted(old[item.probe_key]["daily"], key=lambda item: item["date"]),
        )

    def test_expired_future_corrupt_and_naive_dates_do_not_create_confidence(self):
        record = {
            "daily": [
                None,
                {},
                {"date": "bad", "ok": 1, "failed": 0},
                {"date": "2026-12-10", "ok": 1, "failed": 0},
                {"date": "2026-09-10", "ok": 1, "failed": 0},
                {"date": "2026-11-10", "ok": True, "failed": 0},
            ]
        }
        self.assertEqual(reliability(record, NOW.isoformat())["observed_days"], 0)
        self.assertEqual(daily_observation({}, "2026-11-10T12:00:00", True), [])
        self.assertEqual(daily_observation({}, "invalid", True), [])

    def test_multi_day_success_scores_above_failures_with_recency_weighting(self):
        good = reliability(days(1), NOW.isoformat())
        bad = reliability(days(0, 1), NOW.isoformat())
        self.assertGreater(good["score"], bad["score"])
        self.assertEqual(good["observed_days"], 5)
        mixed = {"daily": [days(1)["daily"][0], {"date": "2026-10-15", "ok": 0, "failed": 1}]}
        self.assertGreater(reliability(mixed, NOW.isoformat())["score"], 50)

    def test_duplicate_daily_buckets_cannot_fake_several_observed_days(self):
        record = {"daily": [days(1)["daily"][0]] * 100}
        self.assertEqual(reliability(record, NOW.isoformat())["observed_days"], 1)

    def test_temporarily_absent_link_history_is_bounded_and_retained_for_thirty_days(self):
        old = {
            "recent": {"checked_at": (NOW - timedelta(days=2)).isoformat()},
            "expired": {"checked_at": (NOW - timedelta(days=31)).isoformat()},
            "future": {"checked_at": (NOW + timedelta(days=1)).isoformat()},
        }
        self.assertEqual(set(update_history({}, old, NOW.isoformat())), {"recent"})

    def test_stability_breaks_healthy_alternative_ties_before_resolution_but_not_health(self):
        high = channel(name="High 1080p", url="https://unstable.test/high")
        low = channel(name="Low 480p", url="https://stable.test/low")
        observations = {
            high.probe_key: ProbeResult("healthy", "valid"),
            low.probe_key: ProbeResult("healthy", "valid"),
        }
        with patch("iptv_builder.reliability.datetime") as clock:
            clock.now.return_value = NOW
            clock.fromisoformat.side_effect = datetime.fromisoformat
            clock.strptime.side_effect = datetime.strptime
            history = {high.probe_key: days(1, 5), low.probe_key: days(5)}
            self.assertEqual(
                select_channels([high, low], observations, history, [], settings()).channels[0].url,
                low.url,
            )
            observations[low.probe_key] = ProbeResult("timeout", "timeout")
            self.assertEqual(
                select_channels([high, low], observations, history, [], settings()).channels[0].url,
                high.url,
            )


class AlertTests(unittest.TestCase):
    def test_ordinary_changed_and_unchanged_builds_are_silent(self):
        for status in ("published", "unchanged"):
            self.assertEqual(
                alert_plan({"status": status, "channels": 100, "removed": 1}, {})[0], []
            )

    def test_major_drop_is_targeted_and_deduplicated_until_it_clears(self):
        report = {"channels": 70, "previous_channels": 100, "removed": 30}
        first, state = alert_plan(report, {})
        self.assertEqual(first[0]["kind"], "major_drop")
        self.assertFalse(alert_plan(report, state)[0])
        _, resolved = alert_plan({"channels": 70, "removed": 0}, state)
        self.assertTrue(alert_plan(report, resolved)[0])
        self.assertFalse(
            alert_plan({"channels": 1000, "previous_channels": 1000, "removed": 20}, {})[0]
        )

    def test_favourite_incident_only_rearms_after_a_fresh_healthy_observation(self):
        item = {"identity": "id:example.us", "name": "Example", "consecutive_failures": 2}
        report = {"watched_failures": [item]}
        first, state = alert_plan(report, {})
        self.assertEqual(len(first), 1)
        self.assertFalse(alert_plan(report, state)[0])
        _, cached = alert_plan({"status": "unchanged"}, state)
        self.assertFalse(alert_plan(report, cached)[0])
        _, fresh = alert_plan({"fresh_healthy_watched": [item["identity"]]}, cached)
        self.assertEqual(len(alert_plan(report, fresh)[0]), 1)

    def test_blocked_build_cannot_clear_previous_favourite_incident_or_leak_error_url(self):
        _, state = alert_plan({"watched_failures": [{"identity": "id:x", "name": "x"}]}, {})
        events, next_state = alert_plan(
            {"status": "blocked", "error": "https://private.test/?token=secret"}, state, failed=True
        )
        self.assertNotIn("secret", str(events))
        self.assertEqual(len(next_state["active"]), 2)
        self.assertFalse(alert_plan({"status": "blocked"}, next_state, failed=True)[0])

    def test_watch_alert_requires_all_alternatives_repeatedly_failing(self):
        high, low = channel(), channel(url="https://other.test/low")
        bad = ProbeResult("timeout", "timeout")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "important_channels.json"
            results = {high.probe_key: bad, low.probe_key: bad}
            history = {
                high.probe_key: {"consecutive_failures": 2},
                low.probe_key: {"consecutive_failures": 1},
            }
            self.assertFalse(watched_failures([high, low], results, history, {high.identity}, path))
            history[low.probe_key]["consecutive_failures"] = 2
            self.assertEqual(
                len(watched_failures([high, low], results, history, {high.identity}, path)), 1
            )
            results[low.probe_key] = ProbeResult("healthy", "cached", cached=True)
            self.assertFalse(watched_failures([high, low], results, history, {high.identity}, path))
            path.write_text('{"channels":["Example.us"]}')
            results[low.probe_key] = bad
            self.assertTrue(watched_failures([high, low], results, history, set(), path))

    def test_telegram_does_not_deliver_or_mark_new_incidents_on_an_ordinary_run(self):
        from iptv_builder.telegram import main

        env = {
            "TG_TOKEN": "test",
            "TG_CHAT": "test",
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_RUN_ID": "1",
            "BUILD_JOB_STATUS": "success",
        }
        with (
            patch.dict("os.environ", env),
            patch("iptv_builder.telegram.read_json", return_value={}),
            patch("iptv_builder.telegram.send_message") as sent,
            patch("iptv_builder.telegram.atomic_write") as stored,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(main(), 0)
            sent.assert_not_called()
            stored.assert_called_once()

    def test_invalid_important_channel_config_is_rejected_before_network(self):
        from iptv_builder.builder import run_build

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "important_channels.json").write_text('{"channels":"not-a-list"}')
            with (
                patch("iptv_builder.builder.fetch_sources") as network,
                self.assertRaises(ValueError),
            ):
                run_build(settings(), root)
            network.assert_not_called()
