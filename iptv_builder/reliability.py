"""Bounded daily observations, not a claim of continuous uptime."""

import math
from datetime import datetime, timedelta, timezone


def daily_observation(old: dict, checked_at: str, healthy: bool, days: int = 30) -> list[dict]:
    try:
        now = datetime.fromisoformat(checked_at)
        if now.tzinfo is None:
            return []
        today = now.astimezone(timezone.utc).date()
    except (TypeError, ValueError):
        return []
    records = {}
    for item in old.get("daily", []) if isinstance(old.get("daily"), list) else []:
        try:
            day = datetime.strptime(item["date"], "%Y-%m-%d").date()
            ok, failed = item["ok"], item["failed"]
            if (
                today - timedelta(days=days - 1) <= day <= today
                and all(type(value) is int and 0 <= value <= 24 for value in (ok, failed))
                and 0 < ok + failed <= 24
            ):
                records[day.isoformat()] = {"date": day.isoformat(), "ok": ok, "failed": failed}
        except (KeyError, TypeError, ValueError):
            continue
    # Replayed/cached observations cannot increase confidence or daily totals.
    try:
        previous_time = datetime.fromisoformat(old.get("checked_at", ""))
        if previous_time.tzinfo is not None and previous_time >= now:
            return [records[key] for key in sorted(records)]
    except (TypeError, ValueError):
        pass
    item = records.setdefault(today.isoformat(), {"date": today.isoformat(), "ok": 0, "failed": 0})
    if item["ok"] + item["failed"] < 24:
        item["ok" if healthy else "failed"] += 1
    return [records[key] for key in sorted(records)]


def reliability(record: dict, now: str | None = None, days: int = 30) -> dict:
    try:
        stamp = datetime.fromisoformat(now) if now else datetime.now(timezone.utc)
        today = stamp.astimezone(timezone.utc).date()
    except (TypeError, ValueError):
        today = datetime.now(timezone.utc).date()
    good, weight, observations, observed_days = 1.0, 2.0, 0, 0
    seen_days = set()
    for item in (
        record.get("daily", [])
        if isinstance(record, dict) and isinstance(record.get("daily"), list)
        else []
    ):
        try:
            age = (today - datetime.strptime(item["date"], "%Y-%m-%d").date()).days
            ok, failed = item["ok"], item["failed"]
            if (
                not 0 <= age < days
                or not all(type(value) is int and 0 <= value <= 24 for value in (ok, failed))
                or not 0 < ok + failed <= 24
            ):
                continue
            if item["date"] in seen_days:
                continue
            seen_days.add(item["date"])
            importance = math.exp(-age * math.log(2) / 7)
            good += importance * ok / (ok + failed)
            weight += importance
            observations += ok + failed
            observed_days += 1
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "score": round(100 * good / weight),
        "observed_days": observed_days,
        "observations": observations,
        "window_days": days,
    }


def stability_rank(record: dict) -> int:
    score = reliability(record)
    # Neutral prior for unknown links; only three distinct observed UTC dates
    # may influence alternative selection. Five-point bands limit churn.
    return score["score"] // 5 if score["observed_days"] >= 3 else 10
