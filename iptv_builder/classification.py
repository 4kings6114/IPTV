"""Classification prefers explicit metadata/overrides over name heuristics."""

import json
import re
from dataclasses import replace
from pathlib import Path

from . import catalog as g
from .config import Settings, Source, country_code
from .m3u import Channel, base_name, country_from_tvg, parse, stable_id

_LANGUAGE_ALIASES = {
    "persian": "fa",
    "farsi": "fa",
    "فارسی": "fa",
    "fas": "fa",
    "per": "fa",
    "english": "en",
    "eng": "en",
    "arabic": "ar",
    "ara": "ar",
    "spanish": "es",
    "spa": "es",
    "german": "de",
    "deu": "de",
    "ger": "de",
    "french": "fr",
    "fra": "fr",
    "fre": "fr",
    "polish": "pl",
    "pol": "pl",
    "portuguese": "pt",
    "por": "pt",
    "pashto": "ps",
    "pus": "ps",
    "dari": "fa",
    "prs": "fa",
    "turkish": "tr",
    "tur": "tr",
}


def language_code(value: str) -> str:
    first = re.split(r"[,;/]", value.lower())[0].strip()
    return _LANGUAGE_ALIASES.get(first, first.split("-")[0])


def override_id(value: str) -> str:
    stem, separator, variant = value.strip().casefold().partition("@")
    return stable_id(stem) + (separator + variant if separator else "")


def load_overrides(path: Path) -> dict:
    if not path.exists():
        return {"channels": {}, "names": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) - {"channels", "names"}:
        raise ValueError("overrides must contain only 'channels' and 'names'")
    result = {"channels": {}, "names": {}}
    for section in result:
        mapping = data.get(section, {})
        if not isinstance(mapping, dict):
            raise ValueError(f"overrides.{section} must be an object")
        for key, item in mapping.items():
            if not isinstance(item, dict) or set(item) - {
                "name",
                "group",
                "language",
                "country",
                "disabled",
            }:
                raise ValueError(f"Invalid override for {key}")
            for field, value in item.items():
                if field == "disabled":
                    if type(value) is not bool:
                        raise ValueError(f"disabled for {key} must be a boolean")
                elif (
                    not isinstance(value, str)
                    or not value.strip()
                    or any(char in value for char in '\r\n"')
                ):
                    raise ValueError(f"Invalid {field} override for {key}")
            if "country" in item and not re.fullmatch(r"[a-zA-Z]{2}", item["country"]):
                raise ValueError(f"Invalid country override for {key}")
            normalized = override_id(key) if section == "channels" else base_name(key)
            result[section][normalized] = item
    return result


def load_excludes(path: Path) -> tuple[set[str], set[str]]:
    names, urls = set(), set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                if line.lower().startswith(("http://", "https://")):
                    urls.add(line)
                else:
                    names.add(line.casefold())
    return names, urls


def override_for(channel: Channel, overrides: dict) -> dict:
    result = dict(overrides["names"].get(base_name(channel.name), {}))
    identifier = override_id(channel.attrs.get("tvg-id", ""))
    result.update(overrides["channels"].get(identifier.split("@", 1)[0], {}))
    result.update(overrides["channels"].get(identifier, {}))
    return result


def infer_language(name: str, explicit: str = "") -> str:
    if explicit:
        return language_code(explicit)
    lower = name.casefold()
    if re.search(r"\b(?:arabic|arab|العربية)\b", lower):
        return "ar"
    if re.search(r"\b(?:english|press\s+tv)\b", lower):
        return "en"
    if re.search(r"\b(?:hispan\s*tv|espa[ñn]ol)\b", lower):
        return "es"
    if re.search(
        r"\b(?:irib\w*|irinn\d*|iran|persian|farsi|tehran|ifilm\w*|manoto|gem\s*tv|avang|navahang|namayesh|tamasha|nasim)\b",
        lower,
    ):
        return "fa"
    return ""


def _topic(value: str, metadata: bool = False) -> str:
    lower = value.casefold()
    # Name expressions use token boundaries: 'Express' must never match 'press'.
    patterns = (
        (
            "news",
            r"\b(?:news|cnn|euronews|irinn\d*|cgtn|press\s+tv|iran\s+international|bbc\s+persian|al\s+jazeera|france\s*24)\b|اخبار|خبر",
        ),
        ("sport", r"\b(?:sports?|espn\w*|football|soccer|nba|nfl|mlb|nhl|olymp\w*)\b|ورزش"),
        (
            "kids",
            r"\b(?:kids?|children|child|cartoon\w*|disney\w*|nickelodeon|nick\s+jr|boomerang)\b|کودک|کارتون",
        ),
        (
            "animals",
            r"\b(?:animals?|nature|wildlife|nat\s*geo|national\s+geographic)\b|حیوانات|طبیعت",
        ),
        (
            "documentary",
            r"\b(?:doc|documentary|documentaries|discovery|history|science|education)\b|مستند|علمی",
        ),
        (
            "lifestyle",
            r"\b(?:lifestyle|cooking|food|travel|home|garden|fashion)\b|آشپزی|سبک زندگی|سفر",
        ),
    )
    for topic, pattern in patterns:
        if re.search(pattern, lower):
            return topic
    if not metadata:
        if re.search(r"\bmtv\w*\b", lower):
            return "mtv"
        if re.search(r"\b(?:rock|metal)\b", lower):
            return "rock"
        if re.search(r"\b(?:hip[\s-]?hop|rap|r\s*&\s*b)\b", lower):
            return "hiphop"
    # Movie names take precedence over generic words like 'Hits'.
    for topic, pattern in (
        ("horror", r"\b(?:horror|terror|scream)\b|ترسناک"),
        ("action", r"\b(?:action|thriller)\b|اکشن"),
        ("classic", r"\b(?:classic|vintage|retro\s+cinema)\b|کلاسیک"),
        ("series", r"\b(?:series|serial|comedy|sitcom|soap)\b|سریال|کمدی"),
        (
            "movies",
            r"\b(?:movies?|cinema|film\w*|ifilm\w*|hollywood|hbo\w*|cinemax\w*|namayesh|tamasha|nasim)\b|فیلم",
        ),
        ("music", r"\b(?:music|radio|hits|vevo|clubbing|avang|navahang)\b|موزیک|موسیقی"),
    ):
        if re.search(pattern, lower):
            return topic
    return ""


def classify(name: str, code: str = "", language: str = "", category: str = "") -> str:
    language = infer_language(name, language)
    region = g.REGION.get(country_code(code), "other")
    # Explicit non-English language beats an English-speaking country's default.
    english = language == "en" or (not language and region == "en")
    if region == "en" and language and language != "en":
        region = "other"
    topic = _topic(category, metadata=True) or _topic(name)
    if language == "fa":
        if topic == "news":
            return g.FA_NEWS
        if topic in {"movies", "series", "classic", "horror", "action"}:
            return g.FA_MOV
        if topic in {"music", "mtv", "rock", "hiphop"}:
            return g.FA_MUS
        if not topic:
            return g.FA_TV
    if topic == "news":
        return g.EN_NEWS if english else g.WORLD_NEWS
    if topic == "sport":
        return g.SPORT
    if topic == "animals":
        return g.ANIMALS
    if topic == "documentary":
        return g.DOC if english else g.DOC_OTHER
    if topic == "lifestyle":
        return g.LIFE
    if topic in {"mtv", "rock", "hiphop", "horror", "action", "classic", "series"}:
        return {
            "mtv": g.MUS_MTV,
            "rock": g.MUS_ROCK,
            "hiphop": g.MUS_HIP,
            "horror": g.MOV_HOR,
            "action": g.MOV_ACT,
            "classic": g.MOV_CLASS,
            "series": g.SERIES,
        }[topic]
    if topic == "music":
        return {"en": g.MUS_EN, "eu": g.MUS_EU, "latam": g.MUS_LATAM, "me": g.MUS_ME}.get(
            region, g.MUS_OTHER
        )
    if topic == "movies":
        return {"en": g.MOV_EN, "eu": g.MOV_EU, "latam": g.MOV_LATAM, "me": g.MOV_ME}.get(
            region, g.OTHER
        )
    if topic == "kids":
        return {"en": g.KIDS_EN, "eu": g.KIDS_EU, "latam": g.KIDS_LATAM, "me": g.KIDS_ME}.get(
            region, g.OTHER
        )
    return {"en": g.ENT_EN, "eu": g.ENT_EU, "latam": g.ENT_LATAM, "me": g.ENT_ME}.get(
        region, g.OTHER
    )


def prepare_channels(
    text: str,
    source: Source,
    settings: Settings,
    overrides: dict,
    excludes: tuple[set[str], set[str]],
    manual: bool = False,
) -> list[Channel]:
    names, urls = excludes
    result = []
    for original in parse(text, source.url, manual):
        channel = original
        override = override_for(channel, overrides)
        if override.get("disabled"):
            continue
        if "name" in override:
            channel = channel.renamed(override["name"])
        attrs = channel.attrs
        code = country_code(
            override.get("country")
            or country_from_tvg(attrs.get("tvg-id", ""))
            or re.split(r"[,;]", attrs.get("tvg-country", ""))[0]
            or source.country
        )
        if code and (code not in settings.allowed_countries or code in settings.blocked_countries):
            continue
        if not code and not (manual or source.allow_unknown_country):
            continue
        if channel.url in urls or channel.request_url in urls:
            continue
        candidates = (original.name.casefold(), channel.name.casefold())
        if any(
            word and word.casefold() in name
            for word in (*settings.blocked_keywords, *names)
            for name in candidates
        ):
            continue
        if code:
            channel = channel.with_attribute("tvg-country", code)
        language = override.get("language") or attrs.get("tvg-language") or source.language
        if language:
            channel = channel.with_attribute("tvg-language", language_code(language))
        group = override.get("group") or (attrs.get("group-title") if manual else "")
        if not group:
            group = classify(channel.name, code, language, attrs.get("group-title", ""))
        # Resolve conflicting classifications across duplicate source entries:
        # explicit group override > manual group > upstream/override language
        # > source language hint > name-only inference.
        priority = (
            4
            if override.get("group")
            else 3
            if manual and attrs.get("group-title")
            else 2
            if override.get("language") or attrs.get("tvg-language")
            else 1
            if source.language
            else 0
        )
        result.append(replace(channel.with_group(group), metadata_priority=priority))
    return result
