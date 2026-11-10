"""Bounded HTTP and Telegram API calls with credential-free diagnostics."""

import json
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class NetworkError(RuntimeError):
    retry_after = None
    retryable = True


class HTTPFailure(NetworkError):
    def __init__(self, status, body=b"", retry_after=None):
        self.status, self.body, self.retry_after = status, body, retry_after
        super().__init__(f"HTTP request rejected ({status})")


class TelegramError(NetworkError):
    def __init__(self, code, *, kind="request", retry_after=None):
        self.code, self.kind, self.retry_after = code, kind, retry_after
        self.retryable = code == 429 or code >= 500 or code == 0
        super().__init__(f"Telegram request failed ({code}, {kind})")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _positive_number(value):
    if type(value) is int and 0 < value <= 86400:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,5}", value):
        number = int(value)
        if 0 < number <= 86400:
            return number
    return None


def decode_json(body):
    def integer(value):
        if len(value) > 20:
            raise ValueError("Oversized integer")
        return int(value)

    def constant(value):
        raise ValueError("Non-finite number")

    try:
        return json.loads(body.decode("utf-8"), parse_int=integer, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise NetworkError("Invalid JSON response") from None


class HTTPTransport:
    def __init__(self, opener=None, clock=time.monotonic):
        self.opener = opener or urllib.request.build_opener(_NoRedirect()).open
        self.clock = clock

    def _read(self, response, limit, deadline):
        encoding = response.headers.get("Content-Encoding", "identity").lower()
        if encoding not in {"", "identity"}:
            raise NetworkError("Unexpected HTTP encoding")
        length = response.headers.get("Content-Length")
        if length is not None:
            if not re.fullmatch(r"[0-9]{1,12}", str(length)) or int(length) > limit:
                raise NetworkError("HTTP response exceeds the size limit")
        chunks, size = [], 0
        while True:
            if self.clock() >= deadline:
                raise NetworkError("HTTP response deadline exceeded")
            chunk = response.read(min(65536, limit - size + 1))
            if not chunk:
                return b"".join(chunks)
            size += len(chunk)
            if size > limit:
                raise NetworkError("HTTP response exceeds the size limit")
            chunks.append(chunk)

    def request(self, url, *, data=None, headers=None, timeout=20, max_bytes=2 * 1024 * 1024):
        try:
            parsed = urlsplit(url)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname
                and not parsed.username
                and not parsed.password
            )
        except ValueError:
            valid = False
        if not valid:
            raise NetworkError("Only HTTPS resource URLs are accepted")
        if data is not None and len(data) > 65536:
            raise NetworkError("HTTP request body exceeds the size limit")
        fields = {"User-Agent": "iptv-telegram-bot/2.0", "Accept-Encoding": "identity"}
        fields.update(headers or {})
        req = urllib.request.Request(
            url, data=data, headers=fields, method="POST" if data is not None else "GET"
        )
        deadline = self.clock() + timeout
        try:
            with self.opener(req, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                body = self._read(response, max_bytes, deadline)
                if not 200 <= status < 300:
                    raise HTTPFailure(status, body[:16384])
                return body
        except urllib.error.HTTPError as error:
            body = b""
            try:
                body = self._read(error, 16384, deadline)
            except (OSError, NetworkError):
                pass
            finally:
                error.close()
            retry = _positive_number(error.headers.get("Retry-After")) if error.headers else None
            raise HTTPFailure(error.code, body, retry) from None
        except (NetworkError, HTTPFailure):
            raise
        except (OSError, TimeoutError, urllib.error.URLError, ValueError):
            # urllib errors may include the complete /bot<TOKEN>/ URL.
            raise NetworkError("Network request failed") from None


def text_units(text):
    return len(text.encode("utf-16-le", "replace")) // 2


def clip_text(text, maximum=3500):
    if text_units(text) <= maximum:
        return text
    used, output = 0, []
    for char in text:
        cost = 2 if ord(char) > 0xFFFF else 1
        if used + cost > maximum - 1:
            break
        output.append(char)
        used += cost
    return "".join(output) + "…"


def split_text(text, maximum=3500):
    if maximum < 2:
        raise ValueError("Message size limit is too small")
    parts, current, units = [], [], 0
    for char in text:
        cost = 2 if ord(char) > 0xFFFF else 1
        if units + cost > maximum:
            parts.append("".join(current))
            current, units = [], 0
        current.append(char)
        units += cost
    if current:
        parts.append("".join(current))
    return parts or ["—"]


class TelegramClient:
    def __init__(self, token, transport=None, timeout=20):
        self._token = token
        self._base = f"https://api.telegram.org/bot{token}"
        self.transport, self.timeout = transport or HTTPTransport(), timeout

    @staticmethod
    def _error(data, status=0, retry=None):
        code = data.get("error_code", status)
        if type(code) is not int or not 0 <= code <= 599:
            code = status
        description = data.get("description", "")
        description = description.lower() if isinstance(description, str) else ""
        parameters = data.get("parameters")
        if isinstance(parameters, dict):
            retry = _positive_number(parameters.get("retry_after")) or retry
        kind = "request"
        if code == 400 and "message is not modified" in description:
            kind = "not_modified"
        elif code == 400 and any(
            word in description
            for word in (
                "message to edit not found",
                "message can't be edited",
                "message_id_invalid",
            )
        ):
            kind = "edit_missing"
        elif code == 400 and any(
            word in description
            for word in ("query is too old", "query_id_invalid", "query id is invalid")
        ):
            kind = "callback_expired"
        return TelegramError(code, kind=kind, retry_after=retry)

    def call(self, method, data=None, *, timeout=None):
        if not re.fullmatch(r"[A-Za-z]{3,40}", method):
            raise ValueError("Invalid API method")
        payload = json.dumps(data or {}, ensure_ascii=False).encode("utf-8")
        try:
            body = self.transport.request(
                f"{self._base}/{method}",
                data=payload,
                headers={"Content-Type": "application/json"},
                timeout=timeout or self.timeout,
            )
        except HTTPFailure as error:
            try:
                parsed = decode_json(error.body)
            except NetworkError:
                parsed = {}
            raise self._error(
                parsed if isinstance(parsed, dict) else {}, error.status, error.retry_after
            ) from None
        result = decode_json(body)
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            raise NetworkError("Invalid Telegram response envelope")
        if not result["ok"]:
            raise self._error(result) from None
        if "result" not in result:
            raise NetworkError("Missing Telegram result")
        return result["result"]

    def send(self, chat_id, text, keyboard=None):
        parts = split_text(text.replace(self._token, "[redacted]"))
        for index, part in enumerate(parts):
            payload = {
                "chat_id": chat_id,
                "text": part,
                "link_preview_options": {"is_disabled": True},
            }
            if keyboard is not None and index == len(parts) - 1:
                payload["reply_markup"] = keyboard
            self.call("sendMessage", payload)

    def edit(self, chat_id, message_id, text, keyboard=None):
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": clip_text(text.replace(self._token, "[redacted]")),
            "link_preview_options": {"is_disabled": True},
        }
        if keyboard is not None:
            payload["reply_markup"] = keyboard
        self.call("editMessageText", payload)

    def answer_callback(self, callback_id, text=None):
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = clip_text(text, 180)
        self.call("answerCallbackQuery", payload)

    def updates(self, offset, poll_timeout):
        return self.call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": poll_timeout,
                "limit": 50,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=poll_timeout + 10,
        )
