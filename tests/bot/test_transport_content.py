import io
import json
import unittest
import urllib.error
from unittest.mock import Mock

from iptv_bot.content import (
    ContentService,
    DataError,
    ResourceCache,
    normalize_query,
    playlist_names,
)
from iptv_bot.transport import (
    HTTPFailure,
    HTTPTransport,
    NetworkError,
    TelegramClient,
    TelegramError,
    clip_text,
    decode_json,
    split_text,
    text_units,
)
from tests.bot.helpers import TOKEN, Clock, Response, encoded, settings


class TransportTests(unittest.TestCase):
    def test_json_payload_and_api_envelope(self):
        transport = Mock()
        transport.request.return_value = encoded({"ok": True, "result": {"message_id": 1}})
        client = TelegramClient(TOKEN, transport)
        client.send(123, "سلام\nWorld", {"inline_keyboard": []})
        args, kwargs = transport.request.call_args
        self.assertTrue(args[0].endswith("/sendMessage"))
        payload = json.loads(kwargs["data"])
        self.assertEqual(payload["text"], "سلام\nWorld")
        self.assertNotIn("parse_mode", payload)
        self.assertTrue(payload["link_preview_options"]["is_disabled"])
        self.assertIn("reply_markup", payload)

    def test_ok_false_on_http_200_is_not_success(self):
        transport = Mock()
        transport.request.return_value = encoded(
            {
                "ok": False,
                "error_code": 429,
                "description": TOKEN,
                "parameters": {"retry_after": 17},
            }
        )
        with self.assertRaises(TelegramError) as failure:
            TelegramClient(TOKEN, transport).call("getMe")
        self.assertEqual(failure.exception.code, 429)
        self.assertEqual(failure.exception.retry_after, 17)
        self.assertNotIn(TOKEN, str(failure.exception))

    def test_http_429_and_permanent_edit_kinds(self):
        transport = Mock()
        for code, description, kind in (
            (400, "Bad Request: message is not modified", "not_modified"),
            (400, "message to edit not found", "edit_missing"),
            (400, "query is too old", "callback_expired"),
            (401, TOKEN, "request"),
        ):
            transport.request.side_effect = HTTPFailure(
                code, encoded({"ok": False, "error_code": code, "description": description})
            )
            with self.subTest(kind=kind), self.assertRaises(TelegramError) as failure:
                TelegramClient(TOKEN, transport).call("getMe")
            self.assertEqual(failure.exception.kind, kind)
            self.assertFalse(failure.exception.retryable)
        transport.request.side_effect = HTTPFailure(429, b"not JSON", retry_after=5)
        with self.assertRaises(TelegramError) as failure:
            TelegramClient(TOKEN, transport).call("getMe")
        self.assertEqual(failure.exception.retry_after, 5)
        self.assertTrue(failure.exception.retryable)

    def test_invalid_json_and_envelopes(self):
        transport = Mock()
        for value in (
            b"html",
            b"[]",
            encoded({"ok": "true", "result": 1}),
            b'{"ok":true}',
            b'{"ok":NaN}',
            b'{"ok":true,"result":' + b"9" * 100 + b"}",
        ):
            transport.request.return_value = value
            with self.subTest(value=value[:25]), self.assertRaises(NetworkError):
                TelegramClient(TOKEN, transport).call("getMe")
        with self.assertRaises(NetworkError):
            decode_json(b"\xff")

    def test_bounded_unicode_messages_and_secret_redaction(self):
        transport = Mock()
        transport.request.return_value = encoded({"ok": True, "result": True})
        client = TelegramClient(TOKEN, transport)
        text = "📺سلام" * 2000
        parts = split_text(text)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(text_units(part) <= 3500 for part in parts))
        self.assertLessEqual(text_units(clip_text(text)), 3500)
        client.send(123, text, {"inline_keyboard": []})
        payloads = [json.loads(call.kwargs["data"]) for call in transport.request.call_args_list]
        self.assertTrue(all(text_units(value["text"]) <= 3500 for value in payloads))
        self.assertTrue(all("reply_markup" not in value for value in payloads[:-1]))
        self.assertIn("reply_markup", payloads[-1])
        client.send(123, TOKEN)
        self.assertNotIn(TOKEN, transport.request.call_args.kwargs["data"].decode())
        client.edit(123, 1, text)
        self.assertLessEqual(
            text_units(json.loads(transport.request.call_args.kwargs["data"])["text"]), 3500
        )

    def test_polling_only_requests_supported_update_types(self):
        transport = Mock()
        transport.request.return_value = encoded({"ok": True, "result": []})
        TelegramClient(TOKEN, transport).updates(12, 30)
        kwargs = transport.request.call_args.kwargs
        payload = json.loads(kwargs["data"])
        self.assertEqual(payload["offset"], 12)
        self.assertEqual(payload["allowed_updates"], ["message", "callback_query"])
        self.assertEqual(kwargs["timeout"], 40)

    def test_http_timeout_body_limit_and_safe_errors(self):
        opener = Mock(return_value=Response(b"hello"))
        transport = HTTPTransport(opener)
        self.assertEqual(transport.request("https://example.invalid/", max_bytes=5), b"hello")
        for response in (
            Response(b"123456"),
            Response(b"", {"Content-Length": "6"}),
            Response(b"", {"Content-Encoding": "gzip"}),
        ):
            opener.return_value = response
            with self.assertRaises(NetworkError):
                transport.request("https://example.invalid/", max_bytes=5)
        opener.side_effect = urllib.error.URLError(f"https://api.telegram.org/bot{TOKEN}/getMe")
        with self.assertRaises(NetworkError) as failure:
            transport.request("https://example.invalid/")
        self.assertNotIn(TOKEN, str(failure.exception))
        self.assertIsNone(failure.exception.__cause__)

    def test_https_only_no_redirects_and_error_body_limit(self):
        opener = Mock()
        transport = HTTPTransport(opener)
        for url in (
            "http://example.invalid",
            "file:///etc/passwd",
            "https://user:pass@example.invalid",
        ):
            with self.assertRaises(NetworkError):
                transport.request(url)
        opener.assert_not_called()
        error = urllib.error.HTTPError(
            "https://example.invalid", 429, TOKEN, {"Retry-After": "8"}, io.BytesIO(b"{}")
        )
        opener.side_effect = error
        with self.assertRaises(HTTPFailure) as failure:
            transport.request("https://example.invalid")
        self.assertEqual(failure.exception.retry_after, 8)
        self.assertNotIn(TOKEN, str(failure.exception))
        default = HTTPTransport()
        from iptv_bot.transport import _NoRedirect

        self.assertTrue(
            any(isinstance(handler, _NoRedirect) for handler in default.opener.__self__.handlers)
        )


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.transport = Mock()
        self.service = ContentService(settings("."), self.transport, self.clock)

    def test_quote_aware_m3u_names_not_metadata_or_last_comma(self):
        text = '\ufeff#EXTM3U\n#EXTINF:-1 group-title="News, World" tvg-id="BBC.us",A, B News\n#EXTVLCOPT:http-user-agent=Player\nhttps://stream.invalid/live\n#EXTINF:-1,News Two\nhttps://stream.invalid/two\n'
        self.assertEqual(playlist_names(text), ("A, B News", "News Two"))
        self.transport.request.return_value = text.encode("utf-8")
        self.assertEqual(self.service.search("A, B").names, ("A, B News",))
        self.assertEqual(self.service.search("BBC").total, 0)
        self.assertEqual(self.transport.request.call_count, 1)

    def test_persian_arabic_letters_diacritics_digits_and_search_limit(self):
        self.assertEqual(normalize_query("كِي ۱۲۳"), normalize_query("کی 123"))
        self.assertEqual(normalize_query("ایران\u200cخبر"), normalize_query("ایران خبر"))
        self.transport.request.return_value = (
            "#EXTM3U\n"
            + "".join(f"#EXTINF:-1,Channel {i}\nhttps://stream.invalid/{i}\n" for i in range(250))
        ).encode()
        results = self.service.search("channel")
        self.assertEqual(results.total, 250)
        self.assertEqual(len(results.names), 200)
        for query in ("", " " * 2, "a" * 129):
            with self.assertRaises(DataError):
                self.service.search(query)

    def test_bad_playlist_is_not_cached_as_success(self):
        for text in (
            "<html>Error</html>",
            "#EXTM3U\n#EXTINF:-1,No URL",
            "#EXTM3U\n#EXTINF:-1,bad\nfile:///etc/passwd",
            "#EXTM3U\n" + "a" * 17000,
        ):
            with self.subTest(text=text[:30]), self.assertRaises(DataError):
                playlist_names(text)

    def test_status_is_filtered_to_publisher_and_branch(self):
        self.transport.request.return_value = encoded(
            {
                "workflow_runs": [
                    {
                        "id": 42,
                        "head_branch": "main",
                        "status": "completed",
                        "conclusion": "success",
                        "created_at": "2026-11-10T15:00:00Z",
                        "html_url": "https://attacker.invalid",
                    }
                ]
            }
        )
        value = self.service.status().value
        self.assertIn("موفق", value)
        self.assertIn("/actions/runs/42", value)
        self.assertNotIn("attacker", value)
        self.assertIn("/actions/workflows/main.yml/runs?", self.transport.request.call_args.args[0])
        self.assertIn("branch=main", self.transport.request.call_args.args[0])
        self.service.status()
        self.assertEqual(self.transport.request.call_count, 1)

    def test_status_empty_or_wrong_branch_is_not_false_success(self):
        self.transport.request.return_value = encoded({"workflow_runs": []})
        self.assertIn("پیدا نشد", self.service.status().value)
        self.clock.advance(121)
        self.transport.request.return_value = encoded(
            {
                "workflow_runs": [
                    {
                        "id": 1,
                        "head_branch": "preview",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            }
        )
        result = self.service.status()
        self.assertTrue(result.stale)
        self.assertIn("پیدا نشد", result.value)

    def test_stats_validation_size_bound_and_stale_fallback(self):
        self.transport.request.return_value = "کانال‌ها: 4585".encode()
        self.assertIn("4585", self.service.stats().value)
        self.assertEqual(self.transport.request.call_args.kwargs["max_bytes"], 65536)
        self.clock.advance(301)
        self.transport.request.return_value = b"<html>failure</html>"
        result = self.service.stats()
        self.assertTrue(result.stale)
        self.assertIn("4585", result.value)
        calls = self.transport.request.call_count
        self.service.stats()
        self.assertEqual(self.transport.request.call_count, calls)
        self.clock.advance(3600)
        with self.assertRaises(DataError):
            self.service.stats()

    def test_cache_uses_monotonic_expiry_and_does_not_cache_errors(self):
        cache = ResourceCache(self.clock)
        loader = Mock(side_effect=NetworkError("no data"))
        with self.assertRaises(DataError):
            cache.get("item", 5, 20, loader)
        with self.assertRaises(DataError):
            cache.get("item", 5, 20, loader)
        self.assertEqual(loader.call_count, 1)
        self.clock.advance(31)
        loader.side_effect = None
        loader.return_value = "fresh"
        self.assertFalse(cache.get("item", 5, 20, loader).stale)
        self.clock.advance(6)
        loader.side_effect = NetworkError("offline")
        self.assertTrue(cache.get("item", 5, 20, loader).stale)
