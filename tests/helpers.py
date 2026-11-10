"""Offline fixtures; no test contacts public stream/source servers."""

import io
from pathlib import Path

from iptv_builder.config import Settings
from iptv_builder.m3u import Channel

ROOT = Path(__file__).resolve().parents[1]


def channel(
    name="Example (720p)",
    url="https://streams.test/live.m3u8",
    tvg_id="Example.us@HD",
    group="test",
    directives=(),
    source="fixture",
    manual=False,
):
    return Channel(
        (f'#EXTINF:-1 tvg-id="{tvg_id}" group-title="{group}",{name}', *directives),
        url,
        source,
        manual,
    )


def settings(**kwargs):
    return Settings(stream_template="", retry_delay=0, **kwargs)


class Response(io.BytesIO):
    def __init__(
        self,
        body=b"",
        content_type="application/vnd.apple.mpegurl",
        url="https://streams.test/live.m3u8",
    ):
        super().__init__(body)
        self.headers = {"Content-Type": content_type}
        self.url = url

    def geturl(self):
        return self.url


HLS = b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nsegment.ts\n"
TS = b"\x47" + b"\x00" * 187 + b"\x47" + b"\x00" * 187
