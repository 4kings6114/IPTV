#!/usr/bin/env python3
"""Builds playlist.m3u with live-check, quality priority, split playlists and EPG."""
import re, sys, time, os, collections, concurrent.futures, urllib.request, shutil
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STREAMS = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/{}.m3u"
FREETV  = "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"

# ---------- Settings ----------
CHECK_LINKS = True
MAX_WORKERS = 20
TIMEOUT     = 6
DROP_DEAD   = True
SAVE_BACKUP = True

# EPG sources (جدول پخش برنامه‌ها)
EPG_URLS = [
    "https://epg.pw/xmltv/epg_IR.xml",
    "https://epg.pw/xmltv/epg_US.xml",
    "https://epg.pw/xmltv/epg_GB.xml",
    "https://iptv-org.github.io/epg/guides/ir/tv.ir.xml.gz",
]

FA_TV, FA_MOV, FA_MUS, FA_NEWS = "فارسی - تلویزیون", "فارسی - فیلم و سریال", "فارسی - موزیک", "اخبار - فارسی"
EN_NEWS, SPORT, MUSIC, MOVIES  = "اخبار - انگلیسی", "ورزشی", "موسیقی", "فیلم"
KIDS, DOC, OTHER               = "کودکان", "مستند", "سایر"
ORDER = [FA_TV, FA_MOV, FA_MUS, FA_NEWS, EN_NEWS, SPORT, MUSIC, MOVIES, KIDS, DOC, OTHER]

# Split playlists
SPLIT = {
    "persian.m3u": {FA_TV, FA_MOV, FA_MUS, FA_NEWS},
    "news.m3u":    {FA_NEWS, EN_NEWS},
    "music.m3u":   {FA_MUS, MUSIC},
    "sports.m3u":  {SPORT},
    "movies.m3u":  {FA_MOV, MOVIES},
    "kids.m3u":    {KIDS},
}

def get(url, tries=3):
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "playlist-bot/3.0"})
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

def res_of(name):
    """Extract resolution number for quality priority (higher = better)."""
    m = re.search(r"(\d{3,4})\s*p", name, re.I)
    if m: return int(m.group(1))
    if re.search(r"\b4k\b|uhd|2160", name, re.I): return 2160
    if re.search(r"\bfhd\b|full\s*hd", name, re.I): return 1080
    if re.search(r"\bhd\b", name, re.I): return 720
    if re.search(r"\bsd\b", name, re.I): return 480
    return 0

def base_name(name):
    """Normalize channel name for deduplication."""
    n = re.sub(r"\s*\(?\d{3,4}\s*p\)?", "", name, flags=re.I)
    n = re.sub(r"\s*\[.*?\]", "", n)
    n = re.sub(r"\s*(HD|FHD|4K|UHD|SD)\b", "", n, flags=re.I)
    return re.sub(r"\s+", " ", n).strip().lower()

def with_group(ext, group):
    line = re.sub(r'group-title="[^"]*"', f'group-title="{group}"', ext[0])
    if "group-title=" not in line:
        line = re.sub(r'(#EXTINF:-?\d+)', rf'\1 group-title="{group}"', line, 1)
    return [line] + ext[1:]

def is_alive(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={
            "User-Agent": "Mozilla/5.0", "Range": "bytes=0-1"
        })
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return 200 <= r.status < 400
    except Exception:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Range": "bytes=0-1023"
            })
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return 200 <= r.status < 500
        except Exception:
            return False

def check_links(entries):
    if not CHECK_LINKS or not entries:
        return entries, 0
    print(f"Checking {len(entries)} links...")
    alive, dead = [], 0
    def worker(item):
        return item, is_alive(item[2])
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = [pool.submit(worker, e) for e in entries]
        for i, fut in enumerate(concurrent.futures.as_completed(futs), 1):
            item, ok = fut.result()
            if ok: alive
