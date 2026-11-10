import io
import json
import os
from pathlib import Path

from iptv_bot.config import ENV_KEYS, Settings

TOKEN = "123456:" + "x" * 35
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_IDS": "123"}
ROOT = Path(__file__).resolve().parents[2]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Response(io.BytesIO):
    def __init__(self, body, headers=None, status=200):
        super().__init__(body)
        self.headers, self.status = headers or {}, status


def settings(root, **env):
    return Settings.from_env({**ENV, **env}, Path(root))


def message(text="/start", chat=123, user=None, identifier=1):
    user = chat if user is None else user
    return {
        "update_id": identifier,
        "message": {
            "message_id": 7,
            "date": 1000,
            "chat": {"id": chat, "type": "private" if chat > 0 else "supergroup"},
            "from": {"id": user, "is_bot": False},
            "text": text,
        },
    }


def callback(data="menu", chat=123, user=None, identifier=2):
    msg = message(chat=chat, user=user)["message"]
    return {
        "update_id": identifier,
        "callback_query": {
            "id": "callback-id",
            "data": data,
            "message": msg,
            "from": {"id": chat if user is None else user, "is_bot": False},
        },
    }


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


def process_env(**extra):
    value = {key: item for key, item in os.environ.items() if key not in ENV_KEYS}
    value.update(ENV)
    value.update(extra)
    return value
