package io.github.fourkings6114.iptv.player

/** User-facing network buffer choices. Manual profiles set ExoPlayer's byte target;
 * automatic delegates sizing and time thresholds to Media3's track-aware defaults.
 */
enum class BufferProfile(val id: String, val label: String, val targetBytes: Int?) {
    Automatic("auto", "خودکار", null),
    VerySmall("tiny", "بسیار کوچک · ۰٫۱ مگابایت", 100 * 1024),
    Small("small", "کوچک · ۰٫۲ مگابایت", 200 * 1024),
    Normal("normal", "عادی · ۰٫۵ مگابایت", 512 * 1024),
    Medium("medium", "متوسط · ۱ مگابایت", 1024 * 1024),
    Large("large", "بزرگ · ۲ مگابایت", 2 * 1024 * 1024),
    VeryLarge("very_large", "خیلی بزرگ · ۴ مگابایت", 4 * 1024 * 1024);

    companion object {
        // Matches the reference screen's selected value; Automatic remains available as an explicit choice.
        val default = Normal
        fun fromId(id: String): BufferProfile = entries.firstOrNull { it.id == id } ?: default
    }
}
