import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from iptv_bot.bot import Bot
from iptv_bot.content import ContentService, DataError, ResourceCache
from iptv_bot.state import StateError, StateStore
from iptv_bot.transport import HTTPFailure, HTTPTransport, NetworkError
from tests.bot.helpers import Clock, Response, callback, encoded, message, settings


class EdgeTests(unittest.TestCase):
    def test_failed_refresh_cannot_extend_stale_data_past_its_maximum_age(self):
        clock = Clock()
        cache = ResourceCache(clock)
        self.assertEqual(cache.get("stats", 2, 20, lambda: "good").value, "good")
        clock.advance(15)

        def slow_failure():
            clock.advance(10)
            raise NetworkError("offline")

        with self.assertRaises(DataError):
            cache.get("stats", 2, 20, slow_failure)

    def test_upstream_retry_after_is_honored_by_resource_cache(self):
        clock = Clock()
        cache = ResourceCache(clock)
        loader = Mock(side_effect=HTTPFailure(429, retry_after=120))
        with self.assertRaises(DataError):
            cache.get("status", 2, 30, loader)
        clock.advance(100)
        with self.assertRaises(DataError):
            cache.get("status", 2, 30, loader)
        self.assertEqual(loader.call_count, 1)
        clock.advance(21)
        with self.assertRaises(DataError):
            cache.get("status", 2, 30, loader)
        self.assertEqual(loader.call_count, 2)

    def test_bad_github_json_is_a_controlled_resource_error(self):
        for run in (
            {"id": True, "head_branch": "main", "status": "completed"},
            {"id": 1, "head_branch": "main", "status": "completed", "conclusion": []},
            {"id": 1, "head_branch": "main", "status": []},
        ):
            transport = Mock()
            transport.request.return_value = encoded({"workflow_runs": [run]})
            service = ContentService(settings("."), transport)
            with self.subTest(run=run), self.assertRaises(DataError):
                service.status()

    def test_cursor_cannot_rewind(self):
        with tempfile.TemporaryDirectory() as root:
            store = StateStore(Path(root), "scope")
            store.save(42)
            with self.assertRaises(StateError):
                store.save(41)
            self.assertEqual(store.load(), 42)

    def test_http_read_deadline_is_checked_between_chunks(self):
        clock = Clock()
        response = Response(b"data")
        original = response.read

        def slow_read(size):
            clock.advance(10)
            return original(size)

        response.read = slow_read
        transport = HTTPTransport(Mock(return_value=response), clock)
        with self.assertRaises(NetworkError):
            transport.request("https://example.invalid", timeout=5)

    def test_group_commands_for_unknown_apps_are_not_answered(self):
        with tempfile.TemporaryDirectory() as root:
            config = settings(root, TELEGRAM_CHAT_IDS="-100123")
            client = Mock()
            bot = Bot(
                config, client, Mock(), StateStore(Path(root), config.scope), username="ExampleBot"
            )
            bot.process_update(message("/unrelated_command", chat=-100123, user=123))
            client.send.assert_not_called()
            bot.process_update(message("/help", chat=-100123, user=123))
            client.send.assert_called_once()

    def test_long_callback_response_is_sent_without_silent_truncation(self):
        with tempfile.TemporaryDirectory() as root:
            config = settings(root)
            client = Mock()
            bot = Bot(config, client, Mock(), StateStore(Path(root), config.scope))
            value = callback()["callback_query"]
            context = bot._context(value["message"], value["from"])
            bot._reply(context, "x" * 4000, edit=True)
            client.edit.assert_not_called()
            self.assertEqual(len(client.send.call_args.args[1]), 4000)

    def test_successful_poll_resets_retry_backoff(self):
        with tempfile.TemporaryDirectory() as root:
            config = settings(root)
            client, stop = Mock(), Mock()
            client.updates.side_effect = [
                NetworkError("offline"),
                [],
                NetworkError("offline"),
                NetworkError("offline"),
            ]
            stop.is_set.side_effect = [False, False, False, False, True]
            bot = Bot(
                config,
                client,
                Mock(),
                StateStore(Path(root), config.scope),
                stop=stop,
                jitter=lambda: 0,
            )
            with self.assertLogs("iptv_bot", level="WARNING"):
                self.assertEqual(bot.run(), 0)
            self.assertEqual([call.args[0] for call in stop.wait.call_args_list], [1, 1, 2])

    def test_bad_callback_payloads_never_fetch_resources(self):
        with tempfile.TemporaryDirectory() as root:
            config = settings(root)
            client, content = Mock(), Mock()
            bot = Bot(config, client, content, StateStore(Path(root), config.scope))
            for data in ([], None, "x" * 65):
                update = callback()
                update["callback_query"]["data"] = data
                bot.process_update(update)
            content.stats.assert_not_called()
            content.status.assert_not_called()
            content.search.assert_not_called()
