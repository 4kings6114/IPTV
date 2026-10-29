#!/usr/bin/env python3
"""Builds playlist.m3u from iptv-org sources with Persian group titles."""
import re, sys, time, os, collections, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
STREAMS = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/{}.m3u"
FREETV = "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8"

# Persian groups
FA_TV   = "فارسی - تلویزیون"
FA_MOV  = "فارسی - فیلم و سریال"
FA_MUS  = "فارسی - موزیک"
FA_NEWS = "اخبار - فارسی"
EN_NEWS = "اخبار - انگلیسی"
SPORT   = "ورزشی"
MUSIC   = "موسیقی"
MOVIES  = "فیلم"
KIDS    = "کودکان"
DOC     = "مستند"
OTHER   = "سایر"

ORDER = [FA_TV, FA_MOV, FA_MUS, FA_NEWS, EN_NEWS, SPORT, MUSIC, MOVIES, KIDS, DOC, OTHER]

def get(url, tries=3):
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "playlist-bot/1.0"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            time.sleep(2)
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
    if 'group-title=' not in line:
        line = re.sub(r'(#EXTINF:-?\d+)', rf'\1 group-title="{group}"', line, 1)
    return [line] + ext[1:]

def main():
    entries = []

    # Iranian / Persian channels
    for code in ["ir", "af"]:
        for ext, url in parse(get(STREAMS.format(code))):
            name = name_of(ext).lower()
            g = FA_TV
            if any(x in name for x in ["movie", "film", "series", "serial", "cinema"]):
                g = FA_MOV
            elif any(x in name for x in ["music", "موزیک"]):
                g = FA_MUS
            elif any(x in name for x in ["news", "خبر", "press"]):
                g = FA_NEWS
            entries.append((g, with_group(ext, g), url))

    # Free-TV + some popular countries
    for code in ["us", "uk", "de", "fr", "tr", "ru"]:
        for ext, url in parse(get(STREAMS.format(code))):
            name = name_of(ext).lower()
            g = OTHER
            if "news" in name: g = EN_NEWS
            elif "sport" in name: g = SPORT
            elif "music" in name or "mtv" in name: g = MUSIC
            elif "movie" in name or "cinema" in name: g = MOVIES
            elif "kid" in name or "cartoon" in name: g = KIDS
            elif "doc" in name: g = DOC
            entries.append((g, with_group(ext, g), url))

    # Free-TV main playlist
    for ext, url in parse(get(FREETV)):
        name = name_of(ext).lower()
        g = OTHER
        if "news" in name: g = EN_NEWS
        elif "sport" in name: g = SPORT
        elif "music" in name: g = MUSIC
        entries.append((g, with_group(ext, g), url))

    # manual.m3u (your personal links)
    manual = os.path.join(HERE, "manual.m3u")
    if os.path.exists(manual):
        for ext, url in parse(open(manual, encoding="utf-8").read()):
            m = re.search(r'group-title="([^"]*)"', ext[0])
            g = m.group(1) if m else FA_MOV
            entries.append((g, with_group(ext, g), url))

    # Deduplicate by URL
    seen = set()
    final = []
    for g, ext, url in entries:
        if url in seen: continue
        seen.add(url)
        final.append((g, ext, url))

    # Sort
    idx = {g: i for i, g in enumerate(ORDER)}
    final.sort(key=lambda x: (idx.get(x[0], 99), name_of(x[1]).lower()))

    # Write
    out = os.path.join(HERE, "playlist.m3u")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("#EXTM3U\n")
        for g, ext, url in final:
            f.write("\n".join(ext) + "\n" + url + "\n")

    c = collections.Counter(g for g, _, _ in final)
    print(f"OK: {len(final)} channels in {len(c)} groups")
    for g in ORDER:
        if g in c:
            print(f"  {g}: {c[g]}")

if __name__ == "__main__":
    main()
