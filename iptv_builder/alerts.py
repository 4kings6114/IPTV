"""Targeted incident alerts, deduplicated until the incident actually clears."""

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .m3u import stable_id


def load_watch_ids(important_file: Path) -> set[str]:
    if not important_file.exists():
        return set()
    if important_file.stat().st_size > 1048576:
        raise ValueError("Important-channel configuration exceeds 1 MiB")
    data = json.loads(important_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"channels"}:
        raise ValueError("Important-channel configuration must contain only channels")
    important = data["channels"]
    if not isinstance(important, list) or not all(
        isinstance(item, str)
        and item.strip()
        and len(item) < 256
        and not any(ord(char) < 32 for char in item)
        for item in important
    ):
        raise ValueError("Important-channel configuration must contain a list of IDs")
    normalized = [item.strip().casefold() for item in important]
    return {
        item if item.startswith("name:") else "id:" + stable_id(item.removeprefix("id:"))
        for item in normalized
    }


def watched_failures(
    channels, results, history, favorites: set[str], important_file: Path | None, threshold: int = 2
) -> list[dict]:
    watched = favorites | (load_watch_ids(important_file) if important_file is not None else set())
    groups = defaultdict(dict)
    for channel in channels:
        if channel.identity in watched:
            groups[channel.identity][channel.probe_key] = channel
    failed = []
    for identity, candidates in sorted(groups.items()):
        if any(
            results[key].healthy or results[key].status == "unchecked" or results[key].cached
            for key in candidates
        ):
            continue
        streak = min(history.get(key, {}).get("consecutive_failures", 0) for key in candidates)
        if streak >= threshold:
            failed.append(
                {
                    "identity": identity,
                    "name": next(iter(candidates.values())).name,
                    "consecutive_failures": streak,
                }
            )
    return failed


def alert_plan(report: dict, previous: dict, failed: bool = False) -> tuple[list[dict], dict]:
    events = []
    if failed:
        # Stable category, not changing run IDs, observation times or raw URLs.
        category = "blocked" if report.get("status") == "blocked" else "build_or_publish_failed"
        events.append(
            {
                "kind": category,
                "title": "به‌روزرسانی یا انتشار ناموفق بود؛ گزارش اجرا را بررسی کنید.",
            }
        )
    removed = report.get("removed", 0)
    old_count = report.get(
        "previous_unique_identities",
        report.get(
            "previous_channels", report.get("channels", 0) + removed - report.get("added", 0)
        ),
    )
    if (
        isinstance(old_count, int)
        and old_count > 0
        and removed >= 20
        and removed / old_count >= 0.1
    ):
        events.append(
            {
                "kind": "major_drop",
                "title": f"افت مهم: {removed} کانال از {old_count} کانال حذف شده است.",
            }
        )
    for item in report.get("watched_failures", [])[:200]:
        events.append(
            {
                "kind": "watched_failure",
                "identity": item["identity"],
                "title": f"کانال مهم/علاقه‌مندی چند بار ناموفق بوده: {item['name']} · {item['identity']}",
            }
        )
    old_active = previous.get("active", {})
    if not isinstance(old_active, dict):
        old_active = {}
    active, notify = {}, []
    for event in events:
        identity = event.get("identity", "")
        fingerprint = hashlib.sha256(
            json.dumps([event["kind"], identity], ensure_ascii=False).encode()
        ).hexdigest()
        active[fingerprint] = {"kind": event["kind"], "identity": identity}
        if fingerprint not in old_active:
            notify.append(event)
    # A blocked build cannot prove recovery of a previously failed favourite.
    # Keep those incidents active until a successful fresh observation clears it.
    fresh_watched = report.get("fresh_healthy_watched", [])
    for key, item in old_active.items():
        if (
            isinstance(item, dict)
            and item.get("kind") == "watched_failure"
            and item.get("identity") not in fresh_watched
        ):
            active.setdefault(key, item)
    return notify, {
        "schema_version": 1,
        "active": dict(list(active.items())[:512]),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
