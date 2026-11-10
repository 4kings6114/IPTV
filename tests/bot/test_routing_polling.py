import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from iptv_bot.bot import Bot, RateLimiter
from iptv_bot.content import ContentService, DataError, SearchResults, Snapshot
from iptv_bot.state import StateError, StateStore
from iptv_bot.transport import NetworkError, TelegramError
from tests.bot.helpers import TOKEN, Clock, callback, message, settings


class BotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.settings = settings(self.temp.name)
        self.client = Mock()
        self.client.updates.return_value = []
        self.content = Mock()
        self.content.links = ContentService(self.settings, Mock()).links
        self.content.stats.return_value = Snapshot("کانال‌ها: 4585", 0)
        self.content.status.return_value = Snapshot("✅ publisher status", 0)
        self.content.search.return_value = SearchResults(
            tuple(f"News {i}" for i in range(19)), 19, 0, False
        )
        self.store = StateStore(Path(self.temp.name), self.settings.scope)
        self.bot = Bot(
            self.settings,
            self.client,
            self.content,
            self.store,
            username="ExampleBot",
            clock=self.clock,
            jitter=lambda: 0,
        )

    def output(self):
        return self.client.send.call_args.args[1]

    def test_all_existing_commands_and_new_profile_links(self):
        commands = (
            "start",
            "menu",
            "help",
            "link",
            "links",
            "stats",
            "status",
            "persian",
            "news",
            "music",
            "sports",
            "movies",
            "kids",
            "profiles",
            "lowbandwidth",
            "favorites",
            "dashboard",
        )
        for index, name in enumerate(commands):
            self.clock.advance(31)
            self.client.reset_mock()
            self.bot.process_update(message(f"/{name}", identifier=index))
            self.client.send.assert_called_once()
        self.assertIn("github.io/IPTV", self.output())
        self.bot.process_update(message("/favorites"))
        self.assertIn("منتشرشده", self.output())
        self.assertIn("favorites.m3u", self.output())

    def test_unauthorized_chat_or_sender_does_not_fetch_or_send(self):
        self.bot.process_update(message("/stats", chat=999))
        self.bot.process_update(callback("stats", chat=999))
        self.bot.process_update(message("/stats", user=456))
        self.client.send.assert_not_called()
        self.client.answer_callback.assert_not_called()
        self.content.stats.assert_not_called()

    def test_group_sender_allowlist(self):
        value = settings(self.temp.name, TELEGRAM_CHAT_IDS="-100123", BOT_ALLOWED_USER_IDS="456")
        bot = Bot(
            value, self.client, self.content, self.store, username="ExampleBot", clock=self.clock
        )
        bot.process_update(message("/stats", chat=-100123, user=789))
        self.content.stats.assert_not_called()
        bot.process_update(message("/stats", chat=-100123, user=456))
        self.content.stats.assert_called_once()
        self.assertEqual(self.client.send.call_args.args[0], -100123)

    def test_mentions_for_another_bot_and_noncommand_edits_are_ignored(self):
        for text in ("hello", "/help@AnotherBot", "/help@AnotherBot extra"):
            self.bot.process_update(message(text))
        self.bot.process_update({"update_id": 3, "edited_message": message()["message"]})
        self.client.send.assert_not_called()
        self.bot.process_update(message("/HELP@ExampleBot"))
        self.assertIn("/stats", self.output())

    def test_callback_is_acknowledged_before_download(self):
        order = []
        self.client.answer_callback.side_effect = lambda *a: order.append("ack")
        self.content.stats.side_effect = lambda: (order.append("fetch"), Snapshot("stats", 0))[1]
        self.bot.process_update(callback("stats"))
        self.assertEqual(order, ["ack", "fetch"])
        self.client.edit.assert_called_once()
        self.assertIn("نه وضعیت زنده", self.client.edit.call_args.args[2])

    def test_uneditable_old_message_falls_back_without_crashing(self):
        self.client.edit.side_effect = TelegramError(400, kind="edit_missing")
        self.bot.process_update(callback("menu"))
        self.client.send.assert_called_once()
        self.client.reset_mock()
        self.client.edit.side_effect = TelegramError(400, kind="not_modified")
        self.bot.process_update(callback("menu"))
        self.client.send.assert_not_called()
        old = callback("menu")
        old["callback_query"]["message"]["date"] = 0
        self.bot.process_update(old)
        self.client.send.assert_called_once()

    def test_expired_callback_ack_still_allows_safe_response(self):
        self.client.answer_callback.side_effect = TelegramError(400, kind="callback_expired")
        self.bot.process_update(callback("menu"))
        self.client.edit.assert_called_once()

    def test_unsupported_inline_or_malformed_callback_is_ignored(self):
        self.bot.process_update(
            {
                "update_id": 1,
                "callback_query": {
                    "id": "cb",
                    "inline_message_id": "inline",
                    "from": {"id": 123},
                    "data": "stats",
                },
            }
        )
        self.bot.process_update({"update_id": 2, "callback_query": []})
        self.client.answer_callback.assert_not_called()
        self.content.stats.assert_not_called()

    def test_search_is_paginated_and_does_not_refetch_for_next_page(self):
        self.bot.process_update(message("/search news"))
        self.assertIn("1 تا 8", self.output())
        keyboard = self.client.send.call_args.args[2]
        next_page = keyboard["inline_keyboard"][0][0]["callback_data"]
        self.assertLessEqual(len(next_page.encode()), 64)
        self.bot.process_update(callback(next_page))
        self.assertIn("9 تا 16", self.client.edit.call_args.args[2])
        self.content.search.assert_called_once_with("news")

    def test_search_expiration_and_group_user_ownership(self):
        value = settings(self.temp.name, TELEGRAM_CHAT_IDS="-100123")
        bot = Bot(
            value, self.client, self.content, self.store, username="ExampleBot", clock=self.clock
        )
        bot.process_update(message("/search news", chat=-100123, user=456))
        key = next(iter(bot.sessions))
        bot.process_update(callback(f"search:{key}:1", chat=-100123, user=789))
        self.client.edit.assert_not_called()
        self.assertIn("کاربر دیگری", self.client.answer_callback.call_args.args[1])
        self.clock.advance(601)
        bot.process_update(callback(f"search:{key}:1", chat=-100123, user=456))
        self.assertIn("منقضی", self.client.edit.call_args.args[2])

    def test_bad_and_empty_search_does_not_download(self):
        for text in ("/search", "/search " + "x" * 129):
            self.bot.process_update(message(text))
        self.content.search.assert_not_called()
        self.content.search.return_value = SearchResults((), 0, 0, False)
        self.bot.process_update(message("/search missing"))
        self.assertIn("پیدا نشد", self.output())
        self.content.search.side_effect = DataError(TOKEN)
        self.bot.process_update(message("/search test"))
        self.assertNotIn(TOKEN, self.output())
        self.assertIn("در دسترس نیست", self.output())

    def test_cache_age_and_stale_warning_are_visible(self):
        self.content.stats.return_value = Snapshot("stats", 600, True)
        self.bot.process_update(message("/stats"))
        self.assertIn("منبع تازه", self.output())
        self.assertIn("10 دقیقه", self.output())
        self.content.status.side_effect = DataError(TOKEN)
        self.bot.process_update(message("/status"))
        self.assertNotIn(TOKEN, self.output())

    def test_cursor_is_only_saved_after_success(self):
        update = message("/help", identifier=100)
        self.client.updates.return_value = [update]
        self.client.send.side_effect = NetworkError(TOKEN)
        with self.assertRaises(NetworkError):
            self.bot.poll_once()
        self.assertEqual(self.bot.offset, 0)
        self.assertEqual(self.store.load(), 0)
        self.client.send.side_effect = None
        self.bot.poll_once()
        self.assertEqual(self.bot.offset, 101)
        self.assertEqual(self.store.load(), 101)
        self.bot.poll_once()
        self.assertEqual(self.client.send.call_count, 2)

    def test_temporary_delivery_failure_refunds_rate_limit(self):
        value = settings(self.temp.name, BOT_RATE_LIMIT="1")
        bot = Bot(
            value, self.client, self.content, self.store, username="ExampleBot", clock=self.clock
        )
        self.client.updates.return_value = [message(identifier=100)]
        self.client.send.side_effect = TelegramError(429, retry_after=9)
        with self.assertRaises(TelegramError):
            bot.poll_once()
        self.client.send.side_effect = None
        bot.poll_once()
        self.assertEqual(self.client.send.call_count, 2)
        self.assertEqual(self.store.load(), 101)

    def test_permanent_bad_delivery_skips_poison_update(self):
        self.client.updates.return_value = [message(identifier=100)]
        self.client.send.side_effect = TelegramError(403)
        with self.assertLogs("iptv_bot", level="WARNING"):
            self.bot.poll_once()
        self.assertEqual(self.store.load(), 101)

    def test_unauthorized_and_unsupported_updates_advance_without_reply(self):
        self.client.updates.return_value = [
            message(chat=999, identifier=100),
            {"update_id": 101, "edited_message": {}},
        ]
        self.bot.poll_once()
        self.assertEqual(self.store.load(), 102)
        self.client.send.assert_not_called()

    def test_malformed_or_out_of_order_batch_does_not_partially_process(self):
        for batch in (
            [message(identifier=2), message(identifier=1)],
            [message(identifier=2), {}],
            [{"update_id": True}],
            [None],
            {},
            [message(identifier=i) for i in range(51)],
        ):
            self.client.updates.return_value = batch
            with self.subTest(batch_type=type(batch)), self.assertRaises(NetworkError):
                self.bot.poll_once()
            self.assertEqual(self.store.load(), 0)
        self.client.send.assert_not_called()

    def test_state_failure_stops_before_acknowledging_future_updates(self):
        self.client.updates.return_value = [message(identifier=100), message(identifier=101)]
        self.store.save = Mock(side_effect=StateError(TOKEN))
        with self.assertLogs("iptv_bot", level="ERROR") as logs:
            self.assertEqual(self.bot.run(), 2)
        self.assertEqual(self.client.send.call_count, 1)
        self.assertEqual(self.bot.offset, 0)
        self.assertNotIn(TOKEN, str(logs.output))

    def test_fatal_polling_errors_stop_instead_of_looping_forever(self):
        for code in (400, 401, 403, 409):
            self.client.updates.side_effect = TelegramError(code)
            with self.subTest(code=code), self.assertLogs("iptv_bot", level="ERROR"):
                self.assertEqual(self.bot.run(), 2)

    def test_retry_after_is_honored_and_backoff_is_not_reset_on_failure(self):
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        bot = Bot(self.settings, self.client, self.content, self.store, stop=stop, jitter=lambda: 0)
        self.client.updates.side_effect = TelegramError(429, retry_after=17)
        with self.assertLogs("iptv_bot", level="WARNING"):
            self.assertEqual(bot.run(), 0)
        stop.wait.assert_called_once_with(17)
        stop.reset_mock()
        stop.is_set.side_effect = [False, False, False, True]
        self.client.updates.side_effect = TelegramError(500)
        with self.assertLogs("iptv_bot", level="WARNING"):
            self.assertEqual(bot.run(), 0)
        self.assertEqual([call.args[0] for call in stop.wait.call_args_list], [1, 2, 4])

    def test_shutdown_and_internal_errors_do_not_leak_exception_urls(self):
        self.bot.stop.set()
        self.assertEqual(self.bot.run(), 0)
        self.bot.stop.clear()
        self.client.updates.side_effect = RuntimeError(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates"
        )
        with self.assertLogs("iptv_bot", level="ERROR") as logs:
            self.assertEqual(self.bot.run(), 2)
        self.assertNotIn(TOKEN, str(logs.output))


class LimiterTests(unittest.TestCase):
    def test_user_window_global_limit_and_refund(self):
        clock = Clock()
        limit = RateLimiter(2, 30, 3, clock)
        first = limit.acquire((1, 1))
        self.assertIsNotNone(first)
        self.assertIsNotNone(limit.acquire((1, 1)))
        self.assertIsNone(limit.acquire((1, 1)))
        limit.refund(first)
        self.assertIsNotNone(limit.acquire((1, 1)))
        self.assertIsNotNone(limit.acquire((2, 2)))
        self.assertIsNone(limit.acquire((3, 3)))
        clock.advance(31)
        self.assertIsNone(limit.acquire((1, 1)))
        clock.advance(30)
        self.assertIsNotNone(limit.acquire((1, 1)))

    def test_high_cardinality_is_memory_bounded(self):
        clock = Clock()
        limit = RateLimiter(1, 1, 1, clock)
        for index in range(4200):
            self.assertIsNotNone(limit.acquire((index, index)))
            clock.advance(61)
        self.assertLessEqual(len(limit.users), 4096)
        self.assertLessEqual(len(limit.global_events), 1)
