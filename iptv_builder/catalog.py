"""Stable group labels and default geographic policy."""

# فقط این کشورها
ALLOWED_COUNTRIES = {
    "ir",
    "af",
    "us",
    "uk",
    "ca",
    "au",
    "nz",
    "ie",
    "de",
    "fr",
    "es",
    "it",
    "nl",
    "pl",
    "se",
    "no",
    "dk",
    "fi",
    "be",
    "at",
    "ch",
    "pt",
    "gr",
    "cz",
    "ro",
    "hu",
    "ua",
    "ru",
    "tr",
    "mx",
    "br",
    "ar",
    "cl",
    "co",
    "pe",
}

# کشورهای ممنوع (هند + آفریقا و ...)
BLOCKED_COUNTRIES = {
    "in",
    "pk",
    "bd",
    "lk",
    "np",
    "bt",
    "mv",  # هند و جنوب آسیا
    "ng",
    "za",
    "eg",
    "ke",
    "gh",
    "tz",
    "ug",
    "et",
    "ma",
    "dz",
    "tn",
    "sn",
    "ci",
    "cm",
    "ao",
    "zw",
    "mz",
    "rw",
    "sd",
    "ly",
    "so",
    "cd",
    "bj",
    "bf",
    "ml",
    "ne",
    "td",
    "cf",
    "ga",
    "cg",
    "bi",
    "mw",
    "zm",
    "na",
    "bw",
    "ls",
    "sz",
    "gm",
    "sl",
    "lr",
    "gn",
    "gw",
    "mr",
    "dj",
    "er",
    "ss",
    "st",
    "sc",
    "km",
    "mg",
    "mu",
    "yt",
    "re",
    "cn",
    "jp",
    "kr",
    "tw",
    "hk",
    "sg",
    "th",
    "vn",
    "id",
    "my",
    "ph",  # آسیای شرقی حذف
}

# کلمات کلیدی برای رد کردن هند / آفریقا / آسیای شرقی ناخواسته
BLOCK_KEYWORDS = [
    "india",
    "indian",
    "bollywood",
    "hindi",
    "bharat",
    "doordarshan",
    "zee ",
    "sony sab",
    "colors tv",
    "star plus",
    "aaj tak",
    "ndtv",
    "africa",
    "african",
    "nigeria",
    "nairobi",
    "lagos",
    "ghana",
    "kenya",
    "ethiopia",
    "tanzania",
    "uganda",
    "senegal",
    "cameroon",
    "south africa",
    "algerian",
    "egyptian",
    "moroccan",
]

# منطقه هر کشور
REGION = {
    "ir": "me",
    "af": "me",
    "us": "en",
    "uk": "en",
    "ca": "en",
    "au": "en",
    "nz": "en",
    "ie": "en",
    "de": "eu",
    "fr": "eu",
    "es": "eu",
    "it": "eu",
    "nl": "eu",
    "pl": "eu",
    "se": "eu",
    "no": "eu",
    "dk": "eu",
    "fi": "eu",
    "be": "eu",
    "at": "eu",
    "ch": "eu",
    "pt": "eu",
    "gr": "eu",
    "cz": "eu",
    "ro": "eu",
    "hu": "eu",
    "ua": "eu",
    "ru": "eu",
    "tr": "me",
    "mx": "latam",
    "br": "latam",
    "ar": "latam",
    "cl": "latam",
    "co": "latam",
    "pe": "latam",
}

# نام گروه‌ها (شبیه mehdi.m3u)
FA_TV = "فارسی - تلویزیون"
FA_MOV = "فارسی - فیلم و سریال"
FA_MUS = "فارسی - موزیک"
FA_NEWS = "اخبار - فارسی"

EN_NEWS = "اخبار - انگلیسی"
SPORT = "ورزشی"

MUS_EN = "موزیک - انگلیسی‌زبان"
MUS_EU = "موزیک - اروپا"
MUS_LATAM = "موزیک - آمریکای لاتین"
MUS_ME = "موزیک - خاورمیانه"
MUS_ROCK = "موزیک - راک"
MUS_MTV = "موزیک - MTV"
MUS_HIP = "موزیک - هیپ‌هاپ و رپ"
MUS_OTHER = "موزیک - سایر"

MOV_EN = "فیلم - انگلیسی‌زبان"
MOV_EU = "فیلم - اروپا"
MOV_LATAM = "فیلم - آمریکای لاتین"
MOV_ME = "فیلم - خاورمیانه"
MOV_ACT = "فیلم اکشن"
MOV_HOR = "فیلم ترسناک"
MOV_CLASS = "کلاسیک (فیلم و سریال قدیمی)"
SERIES = "سریال و کمدی"

ENT_EN = "سرگرمی - انگلیسی‌زبان"
ENT_EU = "سرگرمی - اروپا"
ENT_LATAM = "سرگرمی - آمریکای لاتین"
ENT_ME = "سرگرمی - خاورمیانه"

KIDS_EN = "کودک و کارتون - انگلیسی‌زبان"
KIDS_EU = "کودک و کارتون - اروپا"
KIDS_LATAM = "کودک و کارتون - آمریکای لاتین"
KIDS_ME = "کودک و کارتون - خاورمیانه"

DOC = "مستند و علمی - انگلیسی"
LIFE = "سبک زندگی، آشپزی و سفر"
ANIMALS = "حیوانات و طبیعت"
OTHER = "سایر"

ORDER = [
    FA_TV,
    FA_MOV,
    FA_MUS,
    FA_NEWS,
    EN_NEWS,
    SPORT,
    MUS_EN,
    MUS_EU,
    MUS_LATAM,
    MUS_ME,
    MUS_ROCK,
    MUS_MTV,
    MUS_HIP,
    MUS_OTHER,
    MOV_EN,
    MOV_EU,
    MOV_LATAM,
    MOV_ME,
    MOV_ACT,
    MOV_HOR,
    MOV_CLASS,
    SERIES,
    ENT_EN,
    ENT_EU,
    ENT_LATAM,
    ENT_ME,
    KIDS_EN,
    KIDS_EU,
    KIDS_LATAM,
    KIDS_ME,
    DOC,
    LIFE,
    ANIMALS,
    OTHER,
]

SPLIT = {
    "persian.m3u": {FA_TV, FA_MOV, FA_MUS, FA_NEWS},
    "news.m3u": {FA_NEWS, EN_NEWS},
    "music.m3u": {FA_MUS, MUS_EN, MUS_EU, MUS_LATAM, MUS_ME, MUS_ROCK, MUS_MTV, MUS_HIP, MUS_OTHER},
    "sports.m3u": {SPORT},
    "movies.m3u": {FA_MOV, MOV_EN, MOV_EU, MOV_LATAM, MOV_ME, MOV_ACT, MOV_HOR, MOV_CLASS, SERIES},
    "kids.m3u": {KIDS_EN, KIDS_EU, KIDS_LATAM, KIDS_ME},
}


# Language-neutral groups for non-English news and documentaries.
WORLD_NEWS = "اخبار - سایر زبان‌ها"
DOC_OTHER = "مستند و علمی - سایر زبان‌ها"
ORDER.insert(ORDER.index(EN_NEWS) + 1, WORLD_NEWS)
ORDER.insert(ORDER.index(DOC) + 1, DOC_OTHER)
SPLIT["news.m3u"].add(WORLD_NEWS)
