"""Optional short decoder samples. Diagnostic only, never a publication gate."""

import json
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone

from .config import Settings
from .m3u import Channel


def _run(command: list[str], timeout: float) -> tuple[int, bytes]:
    # Files, not PIPEs: a broken tool/stream must not fill Python's RAM or block
    # on unread stderr. Only a bounded JSON/progress prefix is read afterwards.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=errors,
            timeout=max(0.01, timeout),
            check=False,
        )
        output.seek(0)
        return process.returncode, output.read(65536)


def sample(channel: Channel, settings: Settings) -> dict:
    options = settings.playback
    ffmpeg = shutil.which(options.ffmpeg)
    ffprobe = shutil.which(options.ffprobe) if options.ffprobe else None
    if not ffmpeg:
        return {"status": "unavailable", "reason": "ffmpeg is not installed"}
    # Only network transports needed by HTTP/HLS/DASH; never local files,
    # concat/subfile/data URLs embedded in an untrusted remote manifest.
    network = [
        "-protocol_whitelist",
        "http,https,tcp,tls,crypto,httpproxy",
        "-rw_timeout",
        str(int(min(settings.timeout, options.timeout) * 1000000)),
        "-analyzeduration",
        str(int(options.seconds * 1000000)),
        "-probesize",
        "1048576",
    ]
    if channel.headers:
        network += [
            "-headers",
            "".join(f"{key}: {value}\r\n" for key, value in sorted(channel.headers.items())),
        ]
    deadline = time.monotonic() + options.timeout
    details = {}
    try:
        if ffprobe:
            code, body = _run(
                [
                    ffprobe,
                    "-v",
                    "fatal",
                    *network,
                    "-read_intervals",
                    f"%+{options.seconds}",
                    "-show_entries",
                    "stream=codec_type,codec_name,height",
                    "-of",
                    "json",
                    channel.request_url,
                ],
                deadline - time.monotonic(),
            )
            if code:
                return {"status": "failed", "reason": "ffprobe could not inspect the stream"}
            data = json.loads(body)
            streams = data.get("streams", [])
            if not isinstance(streams, list):
                raise ValueError("Invalid stream list")
            video = [
                item
                for item in streams
                if isinstance(item, dict) and item.get("codec_type") == "video"
            ]
            audio = [
                item
                for item in streams
                if isinstance(item, dict) and item.get("codec_type") == "audio"
            ]
            if not video and not audio:
                return {"status": "failed", "reason": "No audio or video streams found"}
            details = {
                "video": bool(video),
                "audio": bool(audio),
                "codecs": sorted(
                    {str(item.get("codec_name", "unknown"))[:40] for item in video + audio}
                ),
                "resolution": max(
                    (item.get("height", 0) for item in video if type(item.get("height")) is int),
                    default=0,
                ),
            }
        code, progress = _run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-v",
                "fatal",
                "-xerror",
                "-threads",
                "1",
                *network,
                "-readrate",
                "1",
                "-i",
                channel.request_url,
                "-t",
                str(options.seconds),
                "-map",
                "0:v:0?",
                "-map",
                "0:a:0?",
                "-threads",
                "1",
                "-progress",
                "pipe:1",
                "-nostats",
                "-f",
                "null",
                "-",
            ],
            deadline - time.monotonic(),
        )
        # Exit zero on an empty input is not a successful playback sample.
        progress_text = progress.decode("utf-8", "replace")
        duration = [
            line.partition("=")[2]
            for line in progress_text.splitlines()
            if line.startswith("out_time_us=")
        ]
        decoded = any(value.lstrip("-").isdigit() and int(value) > 0 for value in duration)
        if code or not decoded:
            return {
                **details,
                "status": "failed",
                "reason": "Decoder failed or produced no audio/video",
            }
        return {
            **details,
            "status": "decoded",
            "seconds": options.seconds,
            "reason": "Short audio/video decode succeeded"
            + (" (ffprobe unavailable)" if not ffprobe else ""),
        }
    except subprocess.TimeoutExpired:
        return {
            **details,
            "status": "timeout",
            "reason": "Playback sample exceeded its wall-clock limit",
        }
    except (OSError, ValueError, TypeError):
        return {
            **details,
            "status": "failed",
            "reason": "Playback tool returned invalid data or could not run",
        }


def sample_channels(
    channels: list[Channel], settings: Settings, previous: dict
) -> tuple[dict, dict]:
    if not settings.playback.enabled:
        return {"enabled": False}, previous
    known = {channel.probe_key: channel for channel in channels}

    # Oldest observations first; no guarantee about unsampled channels.
    def order(item):
        old = previous.get(item.probe_key, {})
        checked_at = old.get("checked_at", "") if isinstance(old, dict) else ""
        return checked_at if isinstance(checked_at, str) else "", item.identity

    selected = sorted(known.values(), key=order)[: settings.playback.max_channels]
    records, state = (
        [],
        {key: value for key, value in previous.items() if key in known and isinstance(value, dict)},
    )
    for channel in selected:
        result = sample(channel, settings)
        checked_at = datetime.now(timezone.utc).isoformat()
        records.append(
            {"identity": channel.identity, "name": channel.name, **result, "checked_at": checked_at}
        )
        state[channel.probe_key] = {**result, "checked_at": checked_at}
    return {
        "enabled": True,
        "sampled": len(records),
        "total_channels": len(channels),
        "samples": records,
    }, state
