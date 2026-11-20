"""Narrow, reviewed source-specific corrections to ambiguous upstream TVG IDs.

The German list uses the same generic ID for distinct regional services and
some IDs differ from the channel/feed IDs used by iptv-org. Never apply these
aliases to other lists or to an unexpected upstream ID/name combination.
"""

from .m3u import Channel

GERMAN_PUBLIC = "https://raw.githubusercontent.com/L834-57/iptv-de/main/de-public.m3u"
GERMAN_INTERNATIONAL = "https://raw.githubusercontent.com/L834-57/iptv-de/main/intl.m3u"

# Keys are the exact upstream TVG ID and display name, case folded.
_ALIASES = {
    GERMAN_PUBLIC: {
        ("das.erste.de", "das erste"): "DasErste.de",
        ("ard.alpha.de", "ard-alpha"): "ARDalpha.de",
        ("br.de", "br fernsehen"): "BRFernsehen.de@Sud",
        ("hr.de", "hr-fernsehen"): "hrfernsehen.de",
        ("mdr.de", "mdr sachsen"): "MDRFernsehen.de@Sachsen",
        ("mdr.de", "mdr sachsen-anhalt"): "MDRFernsehen.de@SachsenAnhalt",
        ("ndr.de", "ndr hamburg"): "NDRFernsehen.de@Hamburg",
        ("ndr.de", "ndr niedersachsen"): "NDRFernsehen.de@Niedersachsen",
        ("ndr.de", "ndr schleswig-holstein"): "NDRFernsehen.de@SchleswigHolstein",
        ("ndr.de", "ndr mecklenburg-vorpommern"): "NDRFernsehen.de@MecklenburgVorpommern",
        ("rbb.de", "rbb berlin"): "rbbFernsehen.de@Berlin",
        ("rbb.de", "rbb brandenburg"): "rbbFernsehen.de@Brandenburg",
        ("swr/sr.de", "sr fernsehen"): "SRFernsehen.de",
        ("swr/sr.de", "swr baden-württemberg"): "SWRFernsehenBadenWurttemberg.de",
        ("swr/sr.de", "swr rheinland-pfalz"): "SWRFernsehenRheinlandPfalz.de",
        ("wdr.de", "wdr köln"): "WDRFernsehen.de@Koln",
    },
    GERMAN_INTERNATIONAL: {
        ("dw.de", "dw english"): "DW.de@English",
        ("france24.fr", "france 24 english"): "France24.fr@English",
        ("france24.fr", "france 24 français"): "France24.fr@French",
    },
}


def normalize_source_identity(channel: Channel) -> Channel:
    if channel.attrs.get("x-original-tvg-id"):
        return channel
    alias = _ALIASES.get(channel.source, {}).get(
        (channel.channel_id.casefold(), channel.name.casefold())
    )
    return channel.with_attribute("tvg-id", alias) if alias else channel
