"""Public diagnostics must not disclose request credentials or signed URLs."""

import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit

_SECRET = re.compile(
    r"token|auth|signature|(?:^|[-_])sig(?:$|[-_])|password|secret|session|credential|api[-_]?key|^key$|hdnea|hmac|jwt|policy|expires|x-amz-|license",
    re.I,
)


def sensitive_url(value: str) -> bool:
    try:
        parsed = urlsplit(value.split("|", 1)[0])
        return bool(
            parsed.username is not None
            or parsed.password is not None
            or any(_SECRET.search(key) for key, _ in parse_qsl(parsed.query))
            or _SECRET.search(parsed.path)
            or any(len(part) >= 64 for part in parsed.path.split("/"))
        )
    except ValueError:
        return True


def public_url(value: str) -> str:
    try:
        raw = value.split("|", 1)[0]
        parsed = urlsplit(raw)
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parsed.port is not None:
            host += f":{parsed.port}"
        path = (
            "/[redacted]"
            if _SECRET.search(parsed.path)
            or any(len(part) >= 64 for part in parsed.path.split("/"))
            else parsed.path
        )
        return urlunsplit((parsed.scheme, host, path, "[redacted]" if parsed.query else "", ""))
    except ValueError:
        return "[invalid URL]"


def exportable_channel(channel) -> bool:
    if sensitive_url(channel.url) or any(
        key not in {"user-agent", "referer", "origin"} for key in channel.headers
    ):
        return False
    if any(_SECRET.search(key) for key in channel.attrs):
        return False
    for line in channel.ext[1:]:
        if _SECRET.search(line.partition("=")[0]):
            return False
        for url in re.findall(r"https?://[^\s<>\"']+", line):
            if sensitive_url(url):
                return False
    return not any(
        sensitive_url(value)
        for value in channel.headers.values()
        if value.startswith(("http://", "https://"))
    )


def public_data(value):
    if isinstance(value, dict):
        # 'm3u' is generated only from exportable_channel-approved entries;
        # preserving exact directives is necessary for a working local export.
        return {key: item if key == "m3u" else public_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [public_data(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"https?://[^\s<>\"']+", lambda match: public_url(match[0]), value)
    return value
