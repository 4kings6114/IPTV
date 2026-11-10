"""Validated settings and literal .env loading; never execute shell config."""

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlsplit

DEFAULT_REPO = "4kings6114/IPTV"
ENV_KEYS = {
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_CHAT_IDS",
    "BOT_ALLOWED_USER_IDS",
    "BOT_PUBLIC",
    "IPTV_REPO",
    "IPTV_BRANCH",
    "IPTV_DASHBOARD_URL",
    "BOT_STATE_DIR",
    "BOT_POLL_TIMEOUT",
    "BOT_HTTP_TIMEOUT",
    "BOT_RATE_LIMIT",
    "BOT_RATE_WINDOW",
    "BOT_GLOBAL_RATE_LIMIT",
    "BOT_LOG_LEVEL",
}


class ConfigError(ValueError):
    """Safe diagnostic containing field names, not configuration values."""


def load_environment(path: Path, environ=None, *, required=False) -> dict:
    result = dict(os.environ if environ is None else environ)
    if not path.exists():
        if required:
            raise ConfigError("فایل تنظیمات در دسترس نیست.")
        return result
    try:
        with path.open("rb") as source:
            raw = source.read(16385)
        if len(raw) > 16384:
            raise ConfigError("فایل تنظیمات بزرگ‌تر از ۱۶ KiB است.")
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise ConfigError("خواندن فایل تنظیمات ممکن نیست؛ UTF-8 استفاده کنید.") from None
    found = set()
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or key not in ENV_KEYS or key in found:
            raise ConfigError(f"کلید ناشناخته، تکراری یا نامعتبر در خط {number} فایل تنظیمات.")
        found.add(key)
        if value.startswith(("'", '"')):
            match = re.fullmatch(r"(['\"])(.*?)\1\s*(?:#.*)?", value)
            if not match or match[1] in match[2]:
                raise ConfigError(f"نقل‌قول نامعتبر در خط {number} فایل تنظیمات.")
            value = match[2]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        if any(ord(char) < 32 for char in value):
            raise ConfigError(f"مقدار چندخطی یا کنترلی در خط {number} فایل تنظیمات.")
        # Existing environment variables take precedence. No interpolation/eval.
        result.setdefault(key, value)
    return result


def _boolean(env, key, default=False):
    value = env.get(key, str(default)).strip().lower()
    if value not in {"true", "false", "1", "0"}:
        raise ConfigError(f"{key} باید true یا false باشد.")
    return value in {"true", "1"}


def _integer(env, key, default, minimum, maximum):
    value = env.get(key, str(default)).strip()
    if not re.fullmatch(r"[0-9]{1,6}", value):
        raise ConfigError(f"{key} باید عدد صحیح باشد.")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ConfigError(f"{key} خارج از بازهٔ مجاز است.")
    return number


def _ids(value, key, *, users=False):
    result = set()
    if not value.strip():
        return frozenset()
    for part in value.split(","):
        part = part.strip()
        if not re.fullmatch(r"-?[0-9]{1,19}", part):
            raise ConfigError(f"{key} باید شامل شناسه‌های عددی جداشده با کاما باشد.")
        number = int(part)
        if number == 0 or abs(number) > 2**63 - 1 or (users and number < 0):
            raise ConfigError(f"شناسهٔ نامعتبر در {key}.")
        result.add(number)
    if len(result) > 1000:
        raise ConfigError(f"{key} بیش از ۱۰۰۰ شناسه دارد.")
    return frozenset(result)


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    chat_ids: frozenset[int]
    user_ids: frozenset[int]
    public: bool
    repo: str
    branch: str
    dashboard_url: str
    state_dir: Path
    poll_timeout: int = 30
    http_timeout: int = 20
    rate_limit: int = 10
    rate_window: int = 30
    global_rate_limit: int = 120
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: dict, root: Path, *, allow_unconfigured_chats=False):
        token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not re.fullmatch(r"[0-9]{5,16}:[A-Za-z0-9_-]{20,128}", token):
            raise ConfigError("TELEGRAM_BOT_TOKEN خالی یا نامعتبر است؛ مقدار آن را در چت نفرستید.")
        singular = _ids(env.get("TELEGRAM_CHAT_ID", ""), "TELEGRAM_CHAT_ID")
        plural = _ids(env.get("TELEGRAM_CHAT_IDS", ""), "TELEGRAM_CHAT_IDS")
        if singular and plural and singular != plural:
            raise ConfigError(
                "TELEGRAM_CHAT_ID و TELEGRAM_CHAT_IDS متفاوت‌اند؛ فقط یکی را تنظیم کنید."
            )
        chats = plural or singular
        public = _boolean(env, "BOT_PUBLIC")
        if public and chats:
            raise ConfigError("حالت عمومی و فهرست چت خصوصی را هم‌زمان فعال نکنید.")
        if not public and not chats and not allow_unconfigured_chats:
            raise ConfigError(
                "حالت خصوصی است؛ TELEGRAM_CHAT_IDS را تنظیم کنید. عمومی‌شدن خودکار نیست."
            )
        users = _ids(env.get("BOT_ALLOWED_USER_IDS", ""), "BOT_ALLOWED_USER_IDS", users=True)
        repo = env.get("IPTV_REPO", DEFAULT_REPO).strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repo):
            raise ConfigError("IPTV_REPO باید owner/repository باشد.")
        if repo.split("/")[1] in {".", ".."}:
            raise ConfigError("نام مخزن در IPTV_REPO نامعتبر است.")
        branch = env.get("IPTV_BRANCH", "main").strip()
        if (
            not re.fullmatch(r"[A-Za-z0-9_.\-/]{1,150}", branch)
            or any(part in {"", ".", ".."} for part in branch.split("/"))
            or ".." in branch
            or branch.endswith(".lock")
        ):
            raise ConfigError("IPTV_BRANCH نامعتبر است.")
        default_dashboard = "https://4kings6114.github.io/IPTV/" if repo == DEFAULT_REPO else ""
        dashboard = env.get("IPTV_DASHBOARD_URL", default_dashboard).strip()
        if dashboard:
            try:
                parsed = urlsplit(dashboard)
                valid = (
                    parsed.scheme == "https"
                    and parsed.hostname
                    and not parsed.username
                    and not parsed.password
                    and not parsed.query
                    and not parsed.fragment
                    and parsed.port in {None, 443}
                    and not any(char.isspace() for char in dashboard)
                )
            except ValueError:
                valid = False
            if not valid:
                raise ConfigError(
                    "IPTV_DASHBOARD_URL باید لینک عمومی HTTPS بدون توکن یا اطلاعات ورود باشد."
                )
        state_value = env.get("BOT_STATE_DIR", ".cache/telegram-bot").strip()
        if not state_value or any(ord(char) < 32 for char in state_value):
            raise ConfigError("BOT_STATE_DIR نامعتبر است.")
        state = Path(state_value).expanduser()
        if not state.is_absolute():
            state = root / state
        level = env.get("BOT_LOG_LEVEL", "INFO").upper().strip()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ConfigError("BOT_LOG_LEVEL نامعتبر است.")
        return cls(
            token,
            chats,
            users,
            public,
            repo,
            branch,
            dashboard,
            state,
            _integer(env, "BOT_POLL_TIMEOUT", 30, 1, 50),
            _integer(env, "BOT_HTTP_TIMEOUT", 20, 1, 60),
            _integer(env, "BOT_RATE_LIMIT", 10, 1, 120),
            _integer(env, "BOT_RATE_WINDOW", 30, 1, 3600),
            _integer(env, "BOT_GLOBAL_RATE_LIMIT", 120, 1, 600),
            level,
        )

    @property
    def scope(self):
        # Rotation of a token for the SAME bot keeps its cursor; another bot does not.
        return hashlib.sha256(self.token.partition(":")[0].encode()).hexdigest()[:16]

    @property
    def raw_base(self):
        return f"https://raw.githubusercontent.com/{self.repo}/{quote(self.branch, safe='')}"
