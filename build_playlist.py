#!/usr/bin/env python3
"""Builds playlist.m3u with live-link checking, backup and detailed stats."""
import re, sys, time, os, collections, concurrent.futures, urllib.request, urllib.error, shutil
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STREAMS = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/{}.m3u"
FREETV  = "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"

# ---------- Settings ----------
CHECK_LINKS   = True          # بررسی زنده بودن لینک‌ها
MAX_WORKERS   = 20            # تعداد همزمان تست لینک
TIMEOUT       = 6             # ثانیه برای هر تست
DROP_DEAD     = True          # لینک‌های مرده را حذف کند
SAVE_BACKUP   = True          # پشتیبان‌گیری از نسخه قبلی

FA_TV, FA_MOV, FA_MUS, FA_NEWS = "فارسی - تلویزیون", "فارسی - فیلم و سریال", "فارسی - موزیک", "اخبار - فارسی"
EN_NEWS, SPORT, MUSIC, MOVIES  = "اخبار - انگلیسی", "ورزشی", "موسیقی", "فیلم"
KIDS, DOC, OTHER               = "کودکان", "مستند", "سایر"
ORDER = [FA_TV, FA_MOV, FA_MUS, FA_NEWS, EN_NEWS, SPORT, MUSIC, MOVIES, KIDS, DOC, OTHER]

def get(url, tries=3):
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "playlist-bot/2.0"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            time.sleep(1.5)
    return ""

def parse(txt):
    lines = txt.replace("\r", "").split("\n")
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            ext = [lines[i]]
            j = i + 1
            while j < len(lines) and lines[j].startswith("#"):
                ext.append(lines[j]); j += 1
            if j < len(lines) and lines[j].startswith("http"):
                out.append((ext, lines[j].strip()))
            i = j + 1
        else:
            i += 1
    return out

def name_of(ext):
    return ext[0].rsplit(",", 1)[-1].strip()

def with_group(ext, group):
    line = re.sub(r'group-title="[^"]*"', f'group-title="{group}"', ext[0])
    if "group-title=" not in line:
        line = re.sub(r'(#EXTINF:-?\d+)', rf'\1 group-title="{group}"', line, 1)
    return [line] + ext[1:]

def is_alive(url):
    """Quick check if stream URL responds."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers={
            "User-Agent": "Mozilla/5.0 (playlist-bot)",
            "Range": "bytes=0-1"
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return 200 <= r.status < 400
    except Exception:
        # fallback: try GET first bytes
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (playlist-bot)",
                "Range": "bytes=0-1023"
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return 200 <= r.status < 500
        except Exception:
            return False

def check_links(entries):
    """Returns (alive_list, dead_count)"""
    if not CHECK_LINKS or not entries:
        return entries, 0

    print(f"Checking {len(entries)} links (timeout={TIMEOUT}s, workers={MAX_WORKERS})...")
    alive, dead = [], 0

    def worker(item):
        g, ext, url = item
        return item, is_alive(url)

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(worker, e) for e in entries]
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            item, ok = fut.result()
            if ok:
                alive.append(item)
            else:
                dead += 1
            if i % 50 == 0 or i == len(entries):
                print(f"  checked {i}/{len(entries)} — alive so far: {len(alive)}")

    return alive, dead

def load_old_urls(path):
    if not os.path.exists(path):
        return set()
    urls = set()
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line.startswith("http"):
            urls.add(line)
    return urls

def main():
    playlist_path = os.path.join(HERE, "playlist.m3u")
    backup_path   = os.path.join(HERE, "playlist-backup.m3u")
    stats_path    = os.path.join(HERE, "build_stats.txt")

    old_urls = load_old_urls(playlist_path)

    # ----- Backup -----
    if SAVE_BACKUP and os.path.exists(playlist_path):
        shutil.copy2(playlist_path, backup_path)
        print(f"Backup saved → playlist-backup.m3u")

    entries = []

    # Iranian / Afghan
    for code in ("ir", "af"):
        for ext, url in parse(get(STREAMS.format(code))):
            name = name_of(ext).lower()
            g = FA_TV
            if any(x in name for x in ("movie", "film", "series", "serial", "cinema")):
                g = FA_MOV
            elif any(x in name for x in ("music", "موزیک")):
                g = FA_MUS
            elif any(x in name for x in ("news", "خبر", "press")):
                g = FA_NEWS
            entries.append((g, with_group(ext, g), url))

    # Popular countries
    for code in ("us", "uk", "de", "fr", "tr", "ru"):
        for ext, url in parse(get(STREAMS.format(code))):
            name = name_of(ext).lower()
            g = OTHER
            if "news" in name:   g = EN_NEWS
            elif "sport" in name: g = SPORT
            elif "music" in name or "mtv" in name: g = MUSIC
            elif "movie" in name or "cinema" in name: g = MOVIES
            elif "kid" in name or "cartoon" in name: g = KIDS
            elif "doc" in name:  g = DOC
            entries.append((g, with_group(ext, g), url))

    # Free-TV
    for ext, url in parse(get(FREETV)):
        name = name_of(ext).lower()
        g = OTHER
        if "news" in name:   g = EN_NEWS
        elif "sport" in name: g = SPORT
        elif "music" in name: g = MUSIC
        entries.append((g, with_group(ext, g), url))

    # manual.m3u
    manual = os.path.join(HERE, "manual.m3u")
    if os.path.exists(manual):
        for ext, url in parse(open(manual, encoding="utf-8").read()):
            m = re.search(r'group-title="([^"]*)"', ext[0])
            g = m.group(1) if m else FA_MOV
            entries.append((g, with_group(ext, g), url))

    # Deduplicate by URL
    seen, unique = set(), []
    for item in entries:
        if item[2] not in seen:
            seen.add(item[2])
            unique.append(item)

    print(f"Collected {len(unique)} unique links")

    # Live check
    alive, dead_count = check_links(unique)
    if DROP_DEAD:
        final_entries = alive
    else:
        final_entries = unique

    # Sort
    idx = {g: i for i, g in enumerate(ORDER)}
    final_entries.sort(key=lambda x: (idx.get(x[0], 99), name_of(x[1]).lower()))

    # Write playlist
    with open(playlist_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\n")
        for g, ext, url in final_entries:
            f.write("\n".join(ext) + "\n" + url + "\n")

    new_urls = {url for _, _, url in final_entries}
    added   = len(new_urls - old_urls)
    removed = len(old_urls - new_urls)
    total   = len(final_entries)

    c = collections.Counter(g for g, _, _ in final_entries)

    # Stats for Telegram / log
    lines = [
        f"OK: {total} channels",
        f"Added: {added} | Removed: {removed} | Dead removed: {dead_count}",
        f"Groups: {len(c)}",
    ]
    for g in ORDER:
        if g in c:
            lines.append(f"  {g}: {c[g]}")

    summary = " | ".join(lines[:2])
    print("\n".join(lines))

    with open(stats_path, "w", encoding="utf-8") as f:
        f.write(summary + "\n")
        f.write(f"Total: {total}\nAdded: {added}\nRemoved: {removed}\nDead: {dead_count}\n")
        f.write(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")

if __name__ == "__main__":
    main()
