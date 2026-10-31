#!/usr/bin/env python3
"""
IPTV builder:
- فقط کشورهای مجاز
- بدون هند و آفریقا
- دسته‌بندی شبیه mehdi.m3u (منطقه‌ای + فارسی)
"""
import re, time, os, collections, concurrent.futures, urllib.request, shutil
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
STREAMS = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/{}.m3u"
FREETV  = "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"

CHECK_LINKS = True
MAX_WORKERS = 20
TIMEOUT     = 7
DROP_DEAD   = True
SAVE_BACKUP = True

# فقط این کشورها
ALLOWED_COUNTRIES = {
    "ir", "af",
    "us", "uk", "ca", "au", "nz", "ie",
    "de", "fr", "es", "it", "nl", "pl", "se", "no", "dk", "fi",
    "be", "at", "ch", "pt", "gr", "cz", "ro", "hu", "ua", "ru", "tr",
    "mx", "br", "ar", "cl", "co", "pe",
}

# کشورهای ممنوع (هند + آفریقا و ...)
BLOCKED_COUNTRIES = {
    "in", "pk", "bd", "lk", "np", "bt", "mv",  # هند و جنوب آسیا
    "ng", "za", "eg", "ke", "gh", "tz", "ug", "et", "ma", "dz", "tn",
    "sn", "ci", "cm", "ao", "zw", "mz", "rw", "sd", "ly", "so", "cd",
    "bj", "bf", "ml", "ne", "td", "cf", "ga", "cg", "bi", "mw", "zm",
    "na", "bw", "ls", "sz", "gm", "sl", "lr", "gn", "gw", "mr", "dj",
    "er", "ss", "st", "sc", "km", "mg", "mu", "yt", "re",
    "cn", "jp", "kr", "tw", "hk", "sg", "th", "vn", "id", "my", "ph",  # آسیای شرقی حذف
}

# کلمات کلیدی برای رد کردن هند / آفریقا / آسیای شرقی ناخواسته
BLOCK_KEYWORDS = [
    "india", "indian", "bollywood", "hindi", "bharat", "doordarshan",
    "zee ", "sony sab", "colors tv", "star plus", "aaj tak", "ndtv",
    "africa", "african", "nigeria", "nairobi", "lagos", "ghana",
    "kenya", "ethiopia", "tanzania", "uganda", "senegal", "cameroon",
    "south africa", "algerian", "egyptian", "moroccan",
]

# منطقه هر کشور
REGION = {
    "ir": "fa", "af": "fa",
    "us": "en", "uk": "en", "ca": "en", "au": "en", "nz": "en", "ie": "en",
    "de": "eu", "fr": "eu", "es": "eu", "it": "eu", "nl": "eu", "pl": "eu",
    "se": "eu", "no": "eu", "dk": "eu", "fi": "eu", "be": "eu", "at": "eu",
    "ch": "eu", "pt": "eu", "gr": "eu", "cz": "eu", "ro": "eu", "hu": "eu",
    "ua": "eu", "ru": "eu", "tr": "me",
    "mx": "latam", "br": "latam", "ar": "latam", "cl": "latam",
    "co": "latam", "pe": "latam",
}

# نام گروه‌ها (شبیه mehdi.m3u)
FA_TV   = "فارسی - تلویزیون"
FA_MOV  = "فارسی - فیلم و سریال"
FA_MUS  = "فارسی - موزیک"
FA_NEWS = "اخبار - فارسی"

EN_NEWS = "اخبار - انگلیسی"
SPORT   = "ورزشی"

MUS_EN    = "موزیک - انگلیسی‌زبان"
MUS_EU    = "موزیک - اروپا"
MUS_LATAM = "موزیک - آمریکای لاتین"
MUS_ME    = "موزیک - خاورمیانه"
MUS_ROCK  = "موزیک - راک"
MUS_MTV   = "موزیک - MTV"
MUS_HIP   = "موزیک - هیپ‌هاپ و رپ"
MUS_OTHER = "موزیک - سایر"

MOV_EN    = "فیلم - انگلیسی‌زبان"
MOV_EU    = "فیلم - اروپا"
MOV_LATAM = "فیلم - آمریکای لاتین"
MOV_ME    = "فیلم - خاورمیانه"
MOV_ACT   = "فیلم اکشن"
MOV_HOR   = "فیلم ترسناک"
MOV_CLASS = "کلاسیک (فیلم و سریال قدیمی)"
SERIES    = "سریال و کمدی"

ENT_EN    = "سرگرمی - انگلیسی‌زبان"
ENT_EU    = "سرگرمی - اروپا"
ENT_LATAM = "سرگرمی - آمریکای لاتین"
ENT_ME    = "سرگرمی - خاورمیانه"

KIDS_EN    = "کودک و کارتون - انگلیسی‌زبان"
KIDS_EU    = "کودک و کارتون - اروپا"
KIDS_LATAM = "کودک و کارتون - آمریکای لاتین"
KIDS_ME    = "کودک و کارتون - خاورمیانه"

DOC     = "مستند و علمی - انگلیسی"
LIFE    = "سبک زندگی، آشپزی و سفر"
ANIMALS = "حیوانات و طبیعت"
OTHER   = "سایر"

ORDER = [
    FA_TV, FA_MOV, FA_MUS, FA_NEWS,
    EN_NEWS, SPORT,
    MUS_EN, MUS_EU, MUS_LATAM, MUS_ME, MUS_ROCK, MUS_MTV, MUS_HIP, MUS_OTHER,
    MOV_EN, MOV_EU, MOV_LATAM, MOV_ME, MOV_ACT, MOV_HOR, MOV_CLASS, SERIES,
    ENT_EN, ENT_EU, ENT_LATAM, ENT_ME,
    KIDS_EN, KIDS_EU, KIDS_LATAM, KIDS_ME,
    DOC, LIFE, ANIMALS, OTHER,
]

SPLIT = {
    "persian.m3u": {FA_TV, FA_MOV, FA_MUS, FA_NEWS},
    "news.m3u":    {FA_NEWS, EN_NEWS},
    "music.m3u":   {FA_MUS, MUS_EN, MUS_EU, MUS_LATAM, MUS_ME, MUS_ROCK, MUS_MTV, MUS_HIP, MUS_OTHER},
    "sports.m3u":  {SPORT},
    "movies.m3u":  {FA_MOV, MOV_EN, MOV_EU, MOV_LATAM, MOV_ME, MOV_ACT, MOV_HOR, MOV_CLASS, SERIES},
    "kids.m3u":    {KIDS_EN, KIDS_EU, KIDS_LATAM, KIDS_ME},
}

# فقط استریم کشورهای مجاز + Free-TV (بعد فیلتر می‌شود)
EXTRA_SOURCES = [
    FREETV,
    "https://iptv-org.github.io/iptv/languages/fas.m3u",
]

EPG_URLS = [
    "https://epg.pw/xmltv/epg_IR.xml",
    "https://epg.pw/xmltv/epg_US.xml",
    "https://epg.pw/xmltv/epg_GB.xml",
    "https://epg.pw/xmltv/epg_DE.xml",
]

def get(url, tries=3):
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "playlist-bot/4.0"})
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

def attrs_of(ext_line):
    """استخراج tvg-id و group-title از خط EXTINF"""
    tvg_id = ""
    m = re.search(r'tvg-id="([^"]*)"', ext_line)
    if m:
        tvg_id = m.group(1)
    return tvg_id

def country_from_tvg(tvg_id):
    """مثال: CNN.us@SD → us | IRIB1.ir → ir"""
    if not tvg_id:
        return ""
    # pattern: Name.CC or Name.CC@...
    m = re.search(r'\.([a-z]{2})(?:@|$)', tvg_id.lower())
    if m:
        return m.group(1)
    return ""

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

def is_blocked_name(name):
    n = name.lower()
    return any(k in n for k in BLOCK_KEYWORDS)

def region_of(code):
    return REGION.get(code, "en")

def classify(name, code=""):
    n = name.lower()
    reg = region_of(code)

    # فارسی
    if code in ("ir", "af") or any(x in n for x in (
        "iran", "irib", "persian", "farsi", "tehran", "press tv",
        "ifilm", "manoto", "gem tv", "iran international"
    )):
        if any(x in n for x in ("movie", "film", "series", "serial", "cinema", "namayesh", "tamasha", "nasim")):
            return FA_MOV
        if any(x in n for x in ("music", "موزیک", "avang", "navahang")):
            return FA_MUS
        if any(x in n for x in ("news", "خبر", "press", "irinn", "iran international")):
            return FA_NEWS
        return FA_TV

    # اخبار
    if any(x in n for x in ("news", "cnn", "bbc", "sky news", "al jazeera", "euronews", "dw ", "cgtn", "france 24")):
        return EN_NEWS

    # ورزش
    if any(x in n for x in ("sport", "espn", "football", "soccer", "nba", "nfl", "mlb", "nhl", "olymp")):
        return SPORT

    # کودک
    if any(x in n for x in ("kid", "child", "cartoon", "disney", "nickelodeon", "nick jr", "cartoon network", "boomerang", "pbs kids")):
        if reg == "eu": return KIDS_EU
        if reg == "latam": return KIDS_LATAM
        if reg == "me": return KIDS_ME
        return KIDS_EN

    # موزیک تخصصی
    if "mtv" in n:
        return MUS_MTV
    if any(x in n for x in ("rock", "metal", "classic rock")):
        return MUS_ROCK
    if any(x in n for x in ("hip hop", "hip-hop", "rap", "r&b")):
        return MUS_HIP
    if any(x in n for x in ("music", "radio", "hits", "vevo", "clubbing")):
        if reg == "eu": return MUS_EU
        if reg == "latam": return MUS_LATAM
        if reg == "me": return MUS_ME
        if reg == "en": return MUS_EN
        return MUS_OTHER

    # فیلم تخصصی
    if any(x in n for x in ("horror", "terror", "scream")):
        return MOV_HOR
    if any(x in n for x in ("action", "thriller")):
        return MOV_ACT
    if any(x in n for x in ("classic", "vintage", "retro cinema")):
        return MOV_CLASS
    if any(x in n for x in ("series", "comedy", "sitcom", "soap")):
        return SERIES
    if any(x in n for x in ("movie", "cinema", "film", "hollywood", "hollywood", "hbo", "cinemax")):
        if reg == "eu": return MOV_EU
        if reg == "latam": return MOV_LATAM
        if reg == "me": return MOV_ME
        return MOV_EN

    # مستند / طبیعت / سبک زندگی
    if any(x in n for x in ("animal", "nature", "wildlife", "nat geo", "national geographic")):
        return ANIMALS
    if any(x in n for x in ("doc", "documentary", "discovery", "history", "science")):
        return DOC
    if any(x in n for x in ("lifestyle", "cooking", "food", "travel", "home", "garden", "fashion")):
        return LIFE

    # سرگرمی عمومی
    if reg == "eu": return ENT_EU
    if reg == "latam": return ENT_LATAM
    if reg == "me": return ENT_ME
    if reg == "en": return ENT_EN
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

def collect_from_url(url, forced_code=""):
    """forced_code: وقتی از streams/{cc}.m3u می‌آید"""
    txt = get(url)
    if not txt:
        return []
    out = []
    for ext, stream in parse(txt):
        name = name_of(ext)
        if is_blocked_name(name):
            continue

        tvg = attrs_of(ext[0])
        code = forced_code or country_from_tvg(tvg)

        # اگر کد کشور معلوم است و مجاز نیست → رد
        if code:
            if code in BLOCKED_COUNTRIES:
                continue
            if code not in ALLOWED_COUNTRIES:
                continue
        else:
            # بدون کد کشور: فقط اگر نام بلاک نشده باشد و از Free-TV/عمومی باشد
            # برای ایمنی بیشتر رد می‌کنیم مگر forced
            if not forced_code and url == FREETV:
                # Free-TV: تلاش برای حدس از نام
                pass
            elif not forced_code:
                continue

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

    # 1) فقط کشورهای مجاز
    print(f"Countries ({len(ALLOWED_COUNTRIES)})...")
    for code in sorted(ALLOWED_COUNTRIES):
        got = collect_from_url(STREAMS.format(code), forced_code=code)
        if got:
            print(f"  {code}: {len(got)}")
            entries.extend(got)

    # 2) منابع اضافی (فیلترشده)
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
            g = m.group(1) if m else FA_TV
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
        else:
            # فایل خالی نماند — حداقل هدر
            write_m3u(os.path.join(HERE, fname), [], epg=True)

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
    for g, n in c.most_common():
        if g not in ORDER:
            lines.append(f"  {g}: {n}")
    lines.append(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    text = "\n".join(lines)
    open(stats_path, "w", encoding="utf-8").write(text + "\n")
    print(text)

if __name__ == "__main__":
    main()
