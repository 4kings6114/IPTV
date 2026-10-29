#!/usr/bin/env python3
"""
جمع‌آوری کانال از چند منبع عمومی + کشورها + Free-TV + manual
"""
import re, time, os, collections, concurrent.futures, urllib.request, shutil
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STREAMS = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/{}.m3u"
FREETV  = "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"

CHECK_LINKS = True
MAX_WORKERS = 20
TIMEOUT     = 6
DROP_DEAD   = True
SAVE_BACKUP = True

# کشورها: فارسی + انگلیسی + اروپا + آمریکا
COUNTRIES = [
    "ir", "af",
    "us", "uk", "ca", "au", "nz", "ie",
    "de", "fr", "es", "it", "nl", "pl", "se", "no", "dk", "fi",
    "be", "at", "ch", "pt", "gr", "cz", "ro", "hu", "ua", "ru", "tr",
    "mx", "br", "ar", "cl", "co", "pe",
]

# منابع اضافی عمومی (هر خط = یک پلی‌لیست کامل M3U)
# هر وقت منبع جدیدی پیدا کردی، همین‌جا اضافه کن
EXTRA_SOURCES = [
    FREETV,
    "https://iptv-org.github.io/iptv/languages/fas.m3u",   # فارسی
    "https://iptv-org.github.io/iptv/languages/eng.m3u",   # انگلیسی
    "https://iptv-org.github.io/iptv/categories/news.m3u",
    "https://iptv-org.github.io/iptv/categories/sports.m3u",
    "https://iptv-org.github.io/iptv/categories/music.m3u",
    "https://iptv-org.github.io/iptv/categories/movies.m3u",
    "https://iptv-org.github.io/iptv/categories/documentary.m3u",
    "https://iptv-org.github.io/iptv/categories/kids.m3u",
    "https://iptv-org.github.io/iptv/countries/ir.m3u",
    "https://iptv-org.github.io/iptv/countries/us.m3u",
    "https://iptv-org.github.io/iptv/countries/uk.m3u",
    "https://iptv-org.github.io/iptv/countries/de.m3u",
    "https://iptv-org.github.io/iptv/countries/fr.m3u",
    "https://iptv-org.github.io/iptv/countries/tr.m3u",
    "https://iptv-org.github.io/iptv/countries/ca.m3u",
]

EPG_URLS = [
    "https://epg.pw/xmltv/epg_IR.xml",
    "https://epg.pw/xmltv/epg_US.xml",
    "https://epg.pw/xmltv/epg_GB.xml",
    "https://epg.pw/xmltv/epg_DE.xml",
]

FA_TV, FA_MOV, FA_MUS, FA_NEWS = "فارسی - تلویزیون", "فارسی - فیلم و سریال", "فارسی - موزیک", "اخبار - فارسی"
EN_NEWS, SPORT, MUSIC, MOVIES  = "اخبار - انگلیسی", "ورزشی", "موسیقی", "فیلم"
KIDS, DOC, OTHER               = "کودکان", "مستند", "سایر"
ORDER = [FA_TV, FA_MOV, FA_MUS, FA_NEWS, EN_NEWS, SPORT, MUSIC, MOVIES, KIDS, DOC, OTHER]

SPLIT = {
    "persian.m3u": {FA_TV, FA_MOV, FA_MUS, FA_NEWS},
    "news.m3u":    {FA_NEWS, EN_NEWS},
    "music.m3u":   {FA_MUS, MUSIC},
    "sports.m3u":  {SPORT},
    "movies.m3u":  {FA_MOV, MOVIES},
    "kids.m3u":    {KIDS},
}

PERSIAN_CODES = {"ir", "af"}

def get(url, tries=3):
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "playlist-bot/3.3"})
            with urllib.request.urlopen(req, timeout=50) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            time.sleep(1.2)
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
    m = re.search(r"(\d{3,4})\s*p", name, re.I)
    if m: return int(m.group(1))
    if re.search(r"\b4k\b|uhd|2160", name, re.I): return 2160
    if re.search(r"\bfhd\b|full\s*hd", name, re.I): return 1080
    if re.search(r"\bhd\b", name, re.I): return 720
    if re.search(r"\bsd\b", name, re.I): return 480
    return 0

def base_name(name):
    n = re.sub(r"\s*\(?\d{3,4}\s*p\)?", "", name, flags=re.I)
    n = re.sub(r"\s*\[.*?\]", "", n)
    n = re.sub(r"\s*(HD|FHD|4K|UHD|SD)\b", "", n, flags=re.I)
    return re.sub(r"\s+", " ", n).strip().lower()

def with_group(ext, group):
    line = re.sub(r'group-title="[^"]*"', f'group-title="{group}"', ext[0])
    if "group-title=" not in line:
        line = re.sub(r'(#EXTINF:-?\d+)', rf'\1 group-title="{group}"', line, 1)
    return [line] + ext[1:]

def classify(name, code=""):
    n = name.lower()
    if code in PERSIAN_CODES or any(x in n for x in ("iran", "irib", "persian", "farsi", "tehran")):
        if any(x in n for x in ("movie", "film", "series", "serial", "cinema")):
            return FA_MOV
        if any(x in n for x in ("music", "موزیک")):
            return FA_MUS
        if any(x in n for x in ("news", "خبر", "press", "irinn")):
            return FA_NEWS
        return FA_TV
    if "news" in n:
        return EN_NEWS
    if "sport" in n:
        return SPORT
    if "music" in n or "mtv" in n:
        return MUSIC
    if "movie" in n or "cinema" in n or "film" in n:
        return MOVIES
    if "kid" in n or "cartoon" in n or "child" in n:
        return KIDS
    if "doc" in n or "documentary" in n:
        return DOC
    return OTHER

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
            if ok:
                alive.append(item)
            else:
                dead += 1
            if i % 100 == 0 or i == len(entries):
                print(f"  {i}/{len(entries)} — alive: {len(alive)}")
    return alive, dead

def load_old_urls(path):
    if not os.path.exists(path):
        return set()
    return {l.strip() for l in open(path, encoding="utf-8", errors="replace") if l.startswith("http")}

def write_m3u(path, entries, epg=True):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        if epg and EPG_URLS:
            epg_attr = " ".join(f'url-tvg="{u}"' for u in EPG_URLS[:4])
            f.write(f"#EXTM3U {epg_attr}\n")
        else:
            f.write("#EXTM3U\n")
        for g, ext, url in entries:
            f.write("\n".join(ext) + "\n" + url + "\n")

def collect_from_url(url, code=""):
    txt = get(url)
    if not txt:
        return []
    out = []
    for ext, stream in parse(txt):
        name = name_of(ext)
        g = classify(name, code)
        out.append((g, with_group(ext, g), stream))
    return out

def main():
    playlist_path = os.path.join(HERE, "playlist.m3u")
    backup_path   = os.path.join(HERE, "playlist-backup.m3u")
    stats_path    = os.path.join(HERE, "build_stats.txt")

    old_urls = load_old_urls(playlist_path)

    if SAVE_BACKUP and os.path.exists(playlist_path):
        shutil.copy2(playlist_path, backup_path)
        print("Backup → playlist-backup.m3u")

    entries = []

    # 1) کشورها از iptv-org
    print(f"Countries ({len(COUNTRIES)})...")
    for code in COUNTRIES:
        got = collect_from_url(STREAMS.format(code), code)
        if got:
            print(f"  {code}: {len(got)}")
            entries.extend(got)

    # 2) منابع اضافی
    print(f"Extra sources ({len(EXTRA_SOURCES)})...")
    for url in EXTRA_SOURCES:
        got = collect_from_url(url)
        short = url.split("/")[-1]
        print(f"  {short}: {len(got)}")
        entries.extend(got)

    # 3) manual
    manual = os.path.join(HERE, "manual.m3u")
    if os.path.exists(manual):
        for ext, url in parse(open(manual, encoding="utf-8").read()):
            m = re.search(r'group-title="([^"]*)"', ext[0])
            g = m.group(1) if m else FA_MOV
            entries.append((g, with_group(ext, g), url))
        print("  manual.m3u added")

    # dedupe با اولویت کیفیت
    best = {}
    for g, ext, url in entries:
        key = (g, base_name(name_of(ext)))
        score = res_of(name_of(ext))
        if key not in best or score > best[key][0]:
            best[key] = (score, g, ext, url)

    unique = [(g, ext, url) for _, g, ext, url in best.values()]
    seen, deduped = set(), []
    for item in unique:
        if item[2] not in seen:
            seen.add(item[2])
            deduped.append(item)

    print(f"Collected {len(deduped)} unique links")

    alive, dead_count = check_links(deduped)
    final_entries = alive if DROP_DEAD else deduped

    idx = {g: i for i, g in enumerate(ORDER)}
    final_entries.sort(key=lambda x: (idx.get(x[0], 99), name_of(x[1]).lower()))

    write_m3u(playlist_path, final_entries, epg=True)

    for fname, groups in SPLIT.items():
        subset = [e for e in final_entries if e[0] in groups]
        if subset:
            write_m3u(os.path.join(HERE, fname), subset, epg=True)
            print(f"  {fname}: {len(subset)}")

    new_urls = {url for _, _, url in final_entries}
    added   = len(new_urls - old_urls)
    removed = len(old_urls - new_urls)
    total   = len(final_entries)
    c = collections.Counter(g for g, _, _ in final_entries)

    lines = [
        f"OK: {total} channels",
        f"Added: {added} | Removed: {removed} | Dead: {dead_count}",
    ]
    for g in ORDER:
        if g in c:
            lines.append(f"  {g}: {c[g]}")
    print("\n".join(lines))

    summary = f"OK: {total} | +{added} -{removed} | Dead:{dead_count}"
    with open(stats_path, "w", encoding="utf-8") as f:
        f.write(summary + "\n")
        f.write(f"Total: {total}\nAdded: {added}\nRemoved: {removed}\nDead: {dead_count}\n")
        f.write(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")
        for g in ORDER:
            if g in c:
                f.write(f"{g}: {c[g]}\n")

if __name__ == "__main__":
    main()
