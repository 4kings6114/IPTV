"""Authorized routing, paginated search and restart-safe long polling."""

import logging
import random
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass

from .content import DataError, clean_text, normalize_query
from .state import StateError
from .transport import NetworkError, TelegramError, clip_text, text_units

LOGGER = logging.getLogger("iptv_bot")
CATEGORIES = {
    "persian": "🇮🇷 فارسی",
    "news": "📰 اخبار",
    "music": "🎵 موزیک",
    "sports": "⚽ ورزشی",
    "movies": "🎬 فیلم",
    "kids": "👶 کودک",
}
COMMANDS = [
    ("start", "شروع و منوی اصلی"),
    ("help", "راهنما"),
    ("link", "لینک پلی‌لیست‌ها"),
    ("stats", "آمار آخرین انتشار"),
    ("status", "وضعیت workflow ساخت پلی‌لیست"),
    ("search", "جستجوی نام کانال"),
    ("dashboard", "داشبورد"),
    ("lowbandwidth", "پلی‌لیست کم‌مصرف"),
    ("profiles", "پروفایل‌های منتشرشده"),
    ("favorites", "علاقه‌مندی‌های منتشرشدهٔ پروژه"),
    *[(key, label.split(" ", 1)[1]) for key, label in CATEGORIES.items()],
]


def _id(value, *, user=False):
    return type(value) is int and value != 0 and abs(value) < 2**63 and (not user or value > 0)


@dataclass(frozen=True)
class Context:
    chat: int
    user: int
    message: int | None = None
    editable: bool = False


@dataclass(frozen=True)
class Ticket:
    key: tuple
    stamp: float
    serial: int


class RateLimiter:
    def __init__(self, per_user=10, window=30, global_limit=120, clock=time.monotonic):
        self.per_user, self.window, self.global_limit, self.clock = (
            per_user,
            window,
            global_limit,
            clock,
        )
        self.users, self.global_events, self.serial = OrderedDict(), deque(), 0

    def acquire(self, key):
        now = self.clock()
        while self.global_events and self.global_events[0][0] <= now - 60:
            self.global_events.popleft()
        if len(self.global_events) >= self.global_limit:
            return None
        events = self.users.get(key, deque())
        while events and events[0][0] <= now - self.window:
            events.popleft()
        if len(events) >= self.per_user:
            return None
        self.serial += 1
        event = (now, self.serial)
        events.append(event)
        self.users[key] = events
        self.users.move_to_end(key)
        while len(self.users) > 4096:
            self.users.popitem(last=False)
        self.global_events.append(event)
        return Ticket(key, now, self.serial)

    def refund(self, ticket):
        # A temporary delivery failure must not turn a retried command into a flood drop.
        event = (ticket.stamp, ticket.serial)
        for events in (self.global_events, self.users.get(ticket.key, deque())):
            try:
                events.remove(event)
            except ValueError:
                pass


@dataclass
class SearchSession:
    chat: int
    user: int
    query: str
    results: object
    created: float


class Bot:
    def __init__(
        self,
        settings,
        client,
        content,
        store,
        *,
        username="",
        clock=time.monotonic,
        stop=None,
        jitter=None,
    ):
        self.settings, self.client, self.content, self.store = settings, client, content, store
        self.username, self.clock = username.casefold(), clock
        self.stop = stop or threading.Event()
        self.jitter = jitter or (lambda: random.uniform(0, 0.5))
        self.offset = store.load()
        self.sessions = OrderedDict()
        self.limiter = RateLimiter(
            settings.rate_limit, settings.rate_window, settings.global_rate_limit, clock
        )

    @staticmethod
    def back_keyboard():
        return {"inline_keyboard": [[{"text": "⬅️ منوی اصلی", "callback_data": "menu"}]]}

    def menu(self):
        categories = [
            {"text": label, "callback_data": f"cat:{key}"} for key, label in CATEGORIES.items()
        ]
        rows = [categories[index : index + 2] for index in range(0, len(categories), 2)]
        rows.extend(
            [
                [
                    {"text": "📥 همه لینک‌ها", "callback_data": "all_links"},
                    {"text": "🔎 جستجو", "callback_data": "search_help"},
                ],
                [
                    {"text": "📊 آمار", "callback_data": "stats"},
                    {"text": "✅ وضعیت ساخت", "callback_data": "status"},
                ],
                [{"text": "📶 پروفایل‌ها", "callback_data": "profiles"}],
            ]
        )
        if self.settings.dashboard_url:
            rows.append([{"text": "🌐 داشبورد", "url": self.settings.dashboard_url}])
        return {"inline_keyboard": rows}

    def _context(self, message, user):
        if not isinstance(message, dict) or not isinstance(user, dict) or user.get("is_bot"):
            return None
        chat = message.get("chat")
        if not isinstance(chat, dict):
            return None
        kind = chat.get("type")
        if not isinstance(kind, str) or kind not in {"private", "group", "supergroup"}:
            return None
        chat_id, user_id = chat.get("id"), user.get("id")
        if not _id(chat_id) or not _id(user_id, user=True):
            return None
        if chat["type"] == "private" and chat_id != user_id:
            return None
        if not self.settings.public and chat_id not in self.settings.chat_ids:
            return None
        if self.settings.user_ids and user_id not in self.settings.user_ids:
            return None
        message_id = message.get("message_id")
        if type(message_id) is not int or message_id <= 0:
            message_id = None
        return Context(
            chat_id, user_id, message_id, message_id is not None and message.get("date", 0) != 0
        )

    def _reply(self, context, text, keyboard=None, *, edit=False):
        if edit and context.editable and text_units(text) <= 3500:
            try:
                self.client.edit(context.chat, context.message, text, keyboard)
                return
            except TelegramError as error:
                if error.kind == "not_modified":
                    return
                if error.kind != "edit_missing":
                    raise
        self.client.send(context.chat, text, keyboard)

    def help_text(self):
        return (
            "📺 ربات IPTV\n\n/start یا /menu — منو\n/link — لینک‌ها\n"
            "/stats — تصویر آخرین انتشار؛ نه آمار زنده\n/status — workflow ساخت پلی‌لیست\n"
            "/search <نام> — جستجوی کانال با صفحه‌بندی\n"
            "/dashboard — داشبورد\n/profiles — پروفایل‌ها\n/lowbandwidth — کم‌مصرف\n"
            "/favorites — علاقه‌مندی‌های منتشرشدهٔ پروژه، نه فهرست شخصی تلگرام\n"
            "/persian /news /music /sports /movies /kids\n\n"
            "این ربات پلی‌لیست را تغییر نمی‌دهد، ویدئو میزبانی نمی‌کند و پخش همهٔ کانال‌ها را تضمین نمی‌کند."
        )

    def links_text(self):
        labels = {
            "playlist": "📺 همهٔ کانال‌ها",
            **CATEGORIES,
            "lowbandwidth": "📶 کم‌مصرف",
            "fa-en": "فارسی/انگلیسی",
            "favorites": "⭐ علاقه‌مندی‌های منتشرشدهٔ پروژه",
            "backup": "نسخهٔ قبلی؛ سلامت آن تضمین نیست",
        }
        return "📥 پلی‌لیست‌های پروژه\n\n" + "\n\n".join(
            f"{label}\n{self.content.links[key]}" for key, label in labels.items()
        )

    @staticmethod
    def _cache_note(snapshot, elapsed=0):
        age = max(0, int((snapshot.age + elapsed) // 60))
        if snapshot.stale:
            return f"\n\n⚠️ منبع تازه در دسترس نیست؛ دادهٔ قبلی از حدود {age} دقیقه پیش نمایش داده می‌شود."
        if age:
            return f"\n\nدادهٔ کش‌شده، حدود {age} دقیقه پیش."
        return ""

    def _render(self, action):
        if action == "menu":
            return "📺 خوش آمدی! یک گزینه را انتخاب کن:", self.menu()
        if action == "help":
            return self.help_text(), self.back_keyboard()
        if action == "all_links":
            return self.links_text(), self.back_keyboard()
        if action == "search_help":
            return "نام کانال را با دستور بفرست:\n/search BBC\n/search ایران", self.back_keyboard()
        if action == "dashboard":
            return (
                self.settings.dashboard_url or "برای این مخزن لینک داشبورد تنظیم نشده است."
            ), self.back_keyboard()
        if action == "profiles":
            return (
                f"📶 کم‌مصرف (بدون تبدیل ویدئو):\n{self.content.links['lowbandwidth']}\n\n"
                f"فارسی/انگلیسی:\n{self.content.links['fa-en']}\n\n"
                "حجم واقعی مصرف و پخش به منبع بستگی دارد؛ لینک همهٔ کانال‌ها در این پروفایل‌ها موجود نیست."
            ), self.back_keyboard()
        if action in {"stats", "status"}:
            try:
                snapshot = self.content.stats() if action == "stats" else self.content.status()
                prefix = "📊 آخرین انتشار معنادار (نه وضعیت زنده):\n\n" if action == "stats" else ""
                return prefix + clip_text(str(snapshot.value), 3000) + self._cache_note(
                    snapshot
                ), self.back_keyboard()
            except DataError:
                return (
                    "اطلاعات منبع فعلاً در دسترس نیست؛ کمی بعد دوباره تلاش کنید.",
                    self.back_keyboard(),
                )
        if action.startswith("cat:"):
            key = action[4:]
            if key in CATEGORIES or key in {"lowbandwidth", "favorites"}:
                label = CATEGORIES.get(
                    key, "📶 کم‌مصرف" if key == "lowbandwidth" else "⭐ علاقه‌مندی‌های منتشرشدهٔ پروژه"
                )
                return f"{label}\n{self.content.links[key]}", self.back_keyboard()
        return "این گزینه قدیمی یا نامعتبر است؛ منوی اصلی را باز کنید.", self.back_keyboard()

    def _search(self, context, query):
        query = clean_text(query).strip()
        if not normalize_query(query) or len(query) > 128:
            self._reply(context, "استفاده: /search <نام کانال>\nعبارت باید ۱ تا ۱۲۸ نویسه باشد.")
            return
        try:
            results = self.content.search(query)
        except DataError:
            self._reply(context, "جستجو فعلاً در دسترس نیست؛ کمی بعد تلاش کنید.")
            return
        if not results.total:
            self._reply(context, f"برای «{query}» کانالی پیدا نشد." + self._cache_note(results))
            return
        now = self.clock()
        self.sessions = OrderedDict(
            (key, value) for key, value in self.sessions.items() if now - value.created < 600
        )
        key = secrets.token_hex(6)
        self.sessions[key] = SearchSession(context.chat, context.user, query, results, now)
        while len(self.sessions) > 128:
            self.sessions.popitem(last=False)
        text, keyboard = self._search_page(self.sessions[key], key, 0)
        self._reply(context, text, keyboard)

    def _search_page(self, session, key, page):
        names = session.results.names
        start = page * 8
        selected = names[start : start + 8]
        text = f"🔎 نتایج «{session.query}»\n\n" + "\n".join(
            f"• {clip_text(name, 160)}" for name in selected
        )
        text += (
            f"\n\nنمایش {start + 1} تا {start + len(selected)} از {session.results.total} نتیجه."
        )
        if session.results.total > len(names):
            text += "\nحداکثر ۲۰۰ نتیجه نمایش داده می‌شود؛ برای نتیجهٔ دقیق‌تر عبارت را کامل‌تر کنید."
        text += self._cache_note(session.results, self.clock() - session.created)
        buttons = []
        if page:
            buttons.append({"text": "قبلی", "callback_data": f"search:{key}:{page - 1}"})
        if start + 8 < len(names):
            buttons.append({"text": "بعدی", "callback_data": f"search:{key}:{page + 1}"})
        rows = [buttons] if buttons else []
        rows.append([{"text": "⬅️ منوی اصلی", "callback_data": "menu"}])
        return text, {"inline_keyboard": rows}

    def _callback(self, context, callback):
        identifier, data = callback.get("id"), callback.get("data", "")
        if not isinstance(identifier, str) or not 0 < len(identifier) <= 256:
            return
        match = (
            re.fullmatch(r"search:([0-9a-f]{12}):([0-9]{1,3})", data)
            if isinstance(data, str) and len(data) <= 64
            else None
        )
        session = self.sessions.get(match[1]) if match else None
        foreign = session is not None and (
            session.chat != context.chat or session.user != context.user
        )
        try:
            if foreign:
                self.client.answer_callback(
                    identifier, "این جستجو متعلق به کاربر دیگری است؛ /search را بفرستید."
                )
            else:
                self.client.answer_callback(identifier)
        except TelegramError as error:
            if error.kind != "callback_expired":
                raise
        if foreign:
            # A group member must not overwrite another user's search message.
            return
        # Acknowledge before any potentially slow GitHub download.
        if not isinstance(data, str) or len(data.encode("utf-8", "replace")) > 64:
            return
        if data.startswith("search:"):
            match = re.fullmatch(r"search:([0-9a-f]{12}):([0-9]{1,3})", data)
            session = self.sessions.get(match[1]) if match else None
            page = int(match[2]) if match else -1
            if (
                session is None
                or session.chat != context.chat
                or session.user != context.user
                or self.clock() - session.created >= 600
                or not 0 <= page * 8 < len(session.results.names)
            ):
                self._reply(
                    context,
                    "جستجو منقضی شده یا برای این کاربر نیست؛ /search را دوباره بفرستید.",
                    self.back_keyboard(),
                    edit=True,
                )
                return
            text, keyboard = self._search_page(session, match[1], page)
        else:
            text, keyboard = self._render(data)
        self._reply(context, text, keyboard, edit=True)

    def process_update(self, update):
        callback = update.get("callback_query")
        if callback is not None:
            if not isinstance(callback, dict) or "message" in update:
                return
            context = self._context(callback.get("message"), callback.get("from"))
            command = None
        else:
            message = update.get("message")
            if not isinstance(message, dict):
                return
            context = self._context(message, message.get("from"))
            text = message.get("text")
            if not isinstance(text, str) or len(text) > 8192:
                return
            command = re.fullmatch(
                r"/([A-Za-z0-9_]{1,32})(?:@([A-Za-z0-9_]{5,32}))?(?:\s+(.*))?", text.strip(), re.S
            )
            if not command or (command[2] and command[2].casefold() != self.username):
                return
        if context is None:
            return
        if command is not None and context.chat < 0 and not command[2]:
            known = {name for name, _ in COMMANDS} | {"menu", "links"}
            if command[1].casefold() not in known:
                return
        ticket = self.limiter.acquire((context.chat, context.user))
        if ticket is None:
            return
        try:
            if callback is not None:
                self._callback(context, callback)
            else:
                name, argument = command[1].casefold(), command[3] or ""
                if name == "search":
                    self._search(context, argument)
                else:
                    aliases = {
                        "start": "menu",
                        "menu": "menu",
                        "link": "all_links",
                        "links": "all_links",
                        "lowbandwidth": "cat:lowbandwidth",
                        "favorites": "cat:favorites",
                    }
                    action = aliases.get(name, f"cat:{name}" if name in CATEGORIES else name)
                    text, keyboard = self._render(action)
                    self._reply(context, text, keyboard)
        except TelegramError as error:
            if error.retryable or error.code in {401, 409}:
                self.limiter.refund(ticket)
                raise
            LOGGER.warning(
                "Delivery rejected; update skipped (code=%s, kind=%s)", error.code, error.kind
            )
        except NetworkError:
            self.limiter.refund(ticket)
            raise

    def poll_once(self):
        updates = self.client.updates(self.offset, self.settings.poll_timeout)
        if not isinstance(updates, list) or len(updates) > 50:
            raise NetworkError("Invalid update batch")
        previous = -1
        for update in updates:
            if not isinstance(update, dict):
                raise NetworkError("Invalid update envelope")
            identifier = update.get("update_id")
            if (
                type(identifier) is not int
                or not 0 <= identifier < 2**63 - 1
                or identifier <= previous
            ):
                raise NetworkError("Invalid update sequence")
            previous = identifier
        for update in updates:
            if self.stop.is_set():
                break
            if update["update_id"] < self.offset:
                continue
            self.process_update(update)
            # Processing precedes persistence and the next Telegram acknowledgement.
            next_offset = update["update_id"] + 1
            self.store.save(next_offset)
            self.offset = next_offset
        return len(updates)

    def run(self):
        backoff = 1
        while not self.stop.is_set():
            try:
                self.poll_once()
                backoff = 1
            except StateError:
                LOGGER.error("State persistence failed; polling stopped to avoid losing updates")
                return 2
            except TelegramError as error:
                if not error.retryable:
                    LOGGER.error(
                        "Polling stopped; check bot credentials, webhook and other pollers (code=%s)",
                        error.code,
                    )
                    return 2
                delay = error.retry_after or (backoff + self.jitter())
                LOGGER.warning(
                    "Temporary Telegram failure; waiting before retry (code=%s)", error.code
                )
                self.stop.wait(delay)
                backoff = min(backoff * 2, 60)
            except NetworkError:
                LOGGER.warning("Temporary network/protocol failure; waiting before retry")
                self.stop.wait(backoff + self.jitter())
                backoff = min(backoff * 2, 60)
            except KeyboardInterrupt:
                self.stop.set()
            except Exception as error:
                # Exception text/tracebacks can contain token-bearing request URLs.
                LOGGER.error(
                    "Unexpected internal failure; polling stopped (%s)", type(error).__name__
                )
                return 2
        return 0
