#!/usr/bin/env python3
"""Builds playlist.m3u from the public iptv-org + Free-TV data."""
import csv, io, re, sys, time, os, collections, concurrent.futures as cf, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
DB = "https://raw.githubusercontent.com/iptv-org/database/master/data/"
STREAMS = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/{}.m3u"
FREETV = "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"

DROP_BAD_LABELS = True
SKIP_CATEGORIES = {"religious", "shop", "legislative", "xxx"}
SKIP_COUNTRIES = {"IN"}

FA_TV, FA_MOV, FA_MUS, FA_NEWS = "فارسی - تلویزیون", "فارسی - فیلم و سریال", "فارسی - موزیک", "اخبار - فارسی"
EN_NEWS, SPORT, ACTION, HORROR = "اخبار - انگلیسی", "ورزشی", "فیلم اکشن", "فیلم ترسناک"
SERIES, DOC, ANIMALS = "سریال و کمدی", "مستند و علمی - انگلیسی", "حیوانات و طبیعت"
LIFE, CLASSIC = "سبک زندگی، آشپزی و سفر", "کلاسیک (فیلم و سریال قدیمی)"
ROCK, METAL, HIPHOP, MTV = "موزیک - راک", "موزیک - متال", "موزیک - هیپ‌هاپ و رپ", "موزیک - MTV"
REGIONS = ["انگلیسی‌زبان", "اروپا", "آمریکای لاتین", "آسیا", "خاورمیانه و آفریقا", "سایر"]
BASE_ORDER = [FA_TV, FA_MOV, FA_MUS, FA_NEWS, EN_NEWS, SPORT, ACTION, HORROR, SERIES, DOC, ANIMALS, LIFE, CLASSIC]
BIG = [("فیلم", "movies"), ("کودک و کارتون", "kids"), ("موزیک", "music"), ("سرگرمی", "ent")]
ORDER = BASE_ORDER[:]
for label, _ in BIG:
    if label == "موزیک":
        ORDER += [ROCK, METAL, HIPHOP, MTV]
    ORDER += [f"{label} - {r}" for r in REGIONS]

def _reg(name, codes):
    return {c: name for c in codes.split()}
REGION_OF = {}
REGION_OF.update(_reg(REGIONS[0], "US UK GB CA AU NZ IE"))
REGION_OF.update(_reg(REGIONS[1], "AL AD AT BY BE BA BG HR CY CZ DK EE FI FR DE GR HU IS IT XK LV LI LT LU MT MD MC ME NL MK NO PL PT RO RU SM RS SK SI ES SE CH UA VA"))
REGION_OF.update(_reg(REGIONS[2], "AR BO BR CL CO CR CU DO EC SV GT HN MX NI PA PY PE PR UY VE HT JM TT"))
REGION_OF.update(_reg(REGIONS[3], "AF BD BT BN KH CN HK ID JP KZ KG LA MO MY MV MN MM NP KP PK PH SG KR LK TW TJ TH TL TM UZ VN AM AZ GE"))
REGION_OF.update(_reg(REGIONS[4], "AE BH EG IQ IR IL JO KW LB OM PS QA SA SY TR YE DZ LY MA SD TN AO BJ BW BF BI CM CV CF TD CD CG CI DJ GQ ER ET GA GM GH GN GW KE LS LR MG MW ML MR MU MZ NA NE NG RW SN SC SL SO ZA SS TZ TG UG ZM ZW"))

RX_BAD = re.compile(r"\[(Not 24/7|Geo-blocked)\]|\b(144|240|288|360|396)p")
RX_ROCK = re.compile(r"\brock\b|\brocks\b|kerrang|punk|grunge|loudwire|hard ?rock|\bindie\b")
RX_METAL = re.compile(r"\bmetal\b|heavy metal|headbang")
RX_HIPHOP = re.compile(r"hip[ -]?hop|\brap\b|\btrap\b|\burban\b")
RX_HORROR = re.compile(r"horror|scary|thriller|haunt|zombie|nightmare|chiller")
RX_ACTION = re.compile(r"\baction\b|martial|kung ?fu")
RX_ANIMAL = re.compile(r"\b(animals?|wild|wildlife|nature|pets?|zoo|safari|dogs?|cats?|horses?|birds?|ocean|marine)\b")

def get(url, tries=3):
    err = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "playlist-bot/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code == 404: return ""
            err = e; time.sleep(2)
        except Exception as e:
            err = e; time.sleep(2)
    print("WARN: could not download", url, err, file=sys.stderr)
    return ""

def parse(txt):
    lines = txt.replace("\r", "").split("\n"); out = []; i = 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            ext = [lines[i]]; j = i + 1
            while j < len(lines) and lines[j].startswith("#"):
                ext.append(lines[j]); j += 1
            if j < len(lines) and lines[j].startswith("http"):
                out.append((ext, lines[j].strip()))
            i = j + 1
        else:
            i += 1
    return out

def name_of(ext): return ext[0].rsplit(",", 1)[-1].strip()
def cid_of(ext):
    m = re.search(r'tvg-id="([^"]*)"', ext[0]); return m.group(1).split("@")[0] if m else ""
def res_of(name):
    m = re.search(r"(\d{3,4})p", name); return int(m.group(1)) if m else 0
def base_name(name):
    return re.sub(r"\s*\(?\d{3,4}p\)?|\s*\[.*?\]|\s*HD|\s*FHD|\s*4K", "", name, flags=re.I).strip()

def with_group(ext, group, new_name=None):
    line = ext[0]
    line = re.sub(r'group-title="[^"]*"', f'group-title="{group}"', line)
    if "group-title=" not in line:
        line = line.replace("#EXTINF:", f'#EXTINF:-1 group-title="{group}",', 1) if "#EXTINF:-1" in line else re.sub(r'(#EXTINF:-?\d+)', rf'\1 group-title="{group}"', line, 1)
    if new_name:
        line = re.sub(r",[^,]*$", f",{new_name}", line)
    return [line] + ext[1:]

def main():
    entries = []
    # Persian channels from various sources
    ir_streams = get(STREAMS.format("ir"))
    for ext, url in parse(ir_streams):
        name = name_of(ext)
        if any(x in name.lower() for x in ["gem", "manoto", "bbc", "iran", "irib", "press", "voa", "radio farda"]):
            g = FA_TV
            if any(x in name.lower() for x in ["movie", "film", "series", "serial"]):
                g = FA_MOV
            elif "music" in name.lower() or "موزیک" in name:
                g = FA_MUS
            elif "news" in name.lower() or "خبر" in name:
                g = FA_NEWS
            entries.append((g, with_group(ext, g), url, cid_of(ext)))

    # Free-TV
    for ext, url in parse(get(FREETV)):
        name = name_of(ext).lower()
        g = "سایر"
        if RX_ANIMAL.search(name): g = ANIMALS
        elif RX_HORROR.search(name): g = HORROR
        elif RX_ACTION.search(name): g = ACTION
        elif "news" in name: g = EN_NEWS
        elif "sport" in name: g = SPORT
        elif "music" in name or "mtv" in name: g = MTV
        elif "cook" in name or "travel" in name or "lifestyle" in name: g = LIFE
        elif "classic" in name: g = CLASSIC
        elif "comedy" in name or "series" in name: g = SERIES
        elif "doc" in name or "science" in name: g = DOC
        entries.append((g, with_group(ext, g), url, cid_of(ext)))

    # manual links
    mp = os.path.join(HERE, "manual.m3u")
    if os.path.exists(mp):
        for ext, url in parse(open(mp, encoding="utf-8").read()):
            m = re.search(r'group-title="([^"]*)"', ext[0])
            entries.append((m.group(1) if m else FA_MOV, ext, url, ""))

    # exclusions
    ex_path = os.path.join(HERE, "exclude.txt")
    excl = []
    if os.path.exists(ex_path):
        excl = [l.strip().lower() for l in open(ex_path, encoding="utf-8") if l.strip() and not l.startswith("#")]

    seen = set(); best = {}
    for g, ext, url, cid in entries:
        low = (url + " " + name_of(ext)).lower()
        if any(x in low for x in excl) or url in seen: continue
        seen.add(url)
        key = (g, cid, base_name(name_of(ext))) if cid else (g, url, "")
        if key not in best or res_of(name_of(ext)) > res_of(name_of(best[key][1])):
            best[key] = (g, ext, url)

    final = list(best.values())
    idx = {g: i for i, g in enumerate(ORDER)}
    final.sort(key=lambda x: (idx.get(x[0], 99), x[0], re.sub(r"^[^\w]+", "", name_of(x[1])).lower()))

    out = os.path.join(HERE, "playlist.m3u")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\n")
        for g, ext, url in final:
            f.write("\n".join(with_group(ext, g)) + "\n" + url + "\n")

    c = collections.Counter(g for g, _, _ in final)
    print(f"OK: {len(final)} channels in {len(c)} groups -> {out}")
    for g in ORDER:
        if g in c: print(f"  {g}: {c[g]}")

if __name__ == "__main__":
    main()
