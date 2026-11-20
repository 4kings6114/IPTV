"""Bounded standard-library clients. Never log token-bearing request URLs."""

import json
import re
import time
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class ServiceError(RuntimeError):
    def __init__(self, service, code=0, retry_after=30):
        self.service, self.code = service, code
        self.retry_after = max(5, min(3600, retry_after))
        # Do NOT retain the original exception/URL/response body in a message.
        super().__init__(
            f"{service}: HTTP {code}" if code else f"{service}: connection/response error"
        )


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(service, url, *, payload=None, maximum=2 * 1024 * 1024, timeout=35):
    headers = {"User-Agent": "IPTV-Local-Bot/1.0", "Accept": "application/vnd.github.raw+json"}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
    request = Request(url, data=body, headers=headers)
    deadline = time.monotonic() + timeout
    try:
        # No redirects: do not forward Telegram tokens to another endpoint.
        with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            chunks, size = [], 0
            while True:
                if time.monotonic() > deadline:
                    raise ServiceError(service)
                chunk = response.read(min(65536, maximum - size + 1))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > maximum:
                    raise ServiceError(service)
            return json.loads(b"".join(chunks))
    except HTTPError as error:
        code = error.code
        wait = 900 if service == "GitHub" and code in {403, 429} else 30
        try:
            wait = int(error.headers.get("Retry-After", wait))
        except (ValueError, TypeError):
            pass
        if service == "Telegram" and code == 429:
            try:
                data = json.loads(error.read(65536))
                delay = data.get("parameters", {}).get("retry_after")
                if type(delay) is int:
                    wait = delay
            except (OSError, ValueError, AttributeError, HTTPException):
                pass
        error.close()
        raise ServiceError(service, code, wait) from None
    except (URLError, OSError, ValueError, HTTPException):
        raise ServiceError(service) from None


def text_chunks(text, limit=1800):
    """1800 Python characters are <=3600 Telegram UTF-16 code units."""
    while len(text) > limit:
        split = text.rfind("\n", 0, limit)
        if split < limit // 2:
            split = limit
        yield text[:split]
        text = text[split:].lstrip("\n")
    if text:
        yield text


class Telegram:
    def __init__(self, token):
        self._base = f"https://api.telegram.org/bot{token}/"

    def call(self, method, payload):
        result = request_json("Telegram", self._base + method, payload=payload)
        if not isinstance(result, dict) or result.get("ok") is not True:
            data = result if isinstance(result, dict) else {}
            parameters = data.get("parameters", {})
            wait = parameters.get("retry_after", 30) if isinstance(parameters, dict) else 30
            if type(wait) is not int:
                wait = 30
            code = data.get("error_code", 0)
            raise ServiceError("Telegram", code if type(code) is int else 0, wait)
        return result.get("result")

    def send(self, chat_id, text, keyboard=None):
        for chunk in text_chunks(text):
            payload = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
            if keyboard:
                payload["reply_markup"] = keyboard
            self.call("sendMessage", payload)

    def updates(self, offset):
        result = self.call(
            "getUpdates",
            {
                "offset": offset,
                "timeout": 20,
                "limit": 50,
                "allowed_updates": ["message"],
            },
        )
        if not isinstance(result, list):
            raise ServiceError("Telegram")
        return result


class GitHub:
    def __init__(self, config):
        self.config = config
        self.base = f"https://api.github.com/repos/{config.repository}"

    def commits(self):
        query = urlencode({"path": "build_report.json", "sha": self.config.branch, "per_page": 20})
        rows = request_json("GitHub", f"{self.base}/commits?{query}")
        if not isinstance(rows, list) or any(
            not isinstance(row, dict)
            or not isinstance(row.get("sha"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", row["sha"])
            for row in rows
        ):
            raise ServiceError("GitHub")
        return [row["sha"] for row in rows]

    def report(self, sha):
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ServiceError("GitHub")
        query = urlencode({"ref": sha})
        # Raw media type supports the current >1 MiB report; base64 Contents
        # responses do not include content for files of this size.
        return request_json(
            "GitHub",
            f"{self.base}/contents/build_report.json?{query}",
            maximum=32 * 1024 * 1024,
            timeout=60,
        )

    def latest_run(self):
        query = urlencode({"branch": self.config.branch, "per_page": 1})
        data = request_json("GitHub", f"{self.base}/actions/workflows/main.yml/runs?{query}")
        if not isinstance(data, dict) or not isinstance(data.get("workflow_runs"), list):
            raise ServiceError("GitHub")
        rows = data["workflow_runs"]
        if not rows:
            return {}
        row = rows[0]
        if (
            not isinstance(row, dict)
            or type(row.get("id")) is not int
            or row["id"] <= 0
            or not isinstance(row.get("status"), str)
            or (row.get("conclusion") is not None and not isinstance(row["conclusion"], str))
            or (row.get("run_attempt") is not None and type(row["run_attempt"]) is not int)
        ):
            raise ServiceError("GitHub")
        return {
            key: row.get(key) for key in ("id", "status", "conclusion", "run_attempt", "updated_at")
        }
