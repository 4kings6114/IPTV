package io.github.fourkings6114.iptv.epg

import io.github.fourkings6114.iptv.core.M3uParser
import java.io.IOException
import java.time.Instant
import java.time.LocalDateTime
import java.time.ZoneId
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.time.format.ResolverStyle
import java.util.Locale
import okhttp3.HttpUrl.Companion.toHttpUrl

const val MAX_EPG_TITLE = 240
const val MAX_EPG_ID = 512
const val EPG_CACHE_MAX_AGE = 48L * 60 * 60 * 1000
const val EPG_STALE_AFTER = 24L * 60 * 60 * 1000

data class EpgLimits(
    val downloadBytes: Int = 8 * 1024 * 1024,
    val expandedBytes: Int = 32 * 1024 * 1024,
    val programmes: Int = 250_000,
    val savedProgrammes: Int = 20_000,
    val savedCharacters: Int = 2_000_000,
)

/** Codes only: never put a URL, XML contents, title or request header in an exception. */
class EpgException(val reason: Reason) : IOException(reason.name) {
    enum class Reason { INVALID_XML, LIMIT, NO_PROGRAMMES, NO_MATCH, NO_IDS, NOT_CONFIGURED, SOURCE_CHANGED, CACHE_IO }
}

data class EpgProgramme(val channelId: String, val title: String, val start: Long, val stop: Long?)
data class NowNext(val current: EpgProgramme? = null, val next: EpgProgramme? = null)

/** Only selected-catalog channel identities may receive this guide's programme data. */
data class EpgSnapshot(val sourceId: String = "", val feedKey: String = "", val guide: EpgGuide? = null,
                       val idsByChannel: Map<String, String> = emptyMap())

data class EpgGuide(val fetchedAt: Long, val programmes: List<EpgProgramme>) {
    val byChannel: Map<String, List<EpgProgramme>> = programmes.groupBy { it.channelId }
        .mapValues { (_, rows) -> rows.sortedBy { it.start } }

    fun at(channelId: String, now: Long): NowNext = nowNext(byChannel[channelId.trim()].orEmpty(), now)
    fun usable(now: Long): Boolean = fetchedAt > 0 && fetchedAt <= now + 300_000 && now - fetchedAt <= EPG_CACHE_MAX_AGE
    fun stale(now: Long): Boolean = now - fetchedAt >= EPG_STALE_AFTER
}

/** Exact, case-sensitive XMLTV IDs only. Do not guess by channel name or rewrite favorites' IDs. */
fun nowNext(rows: List<EpgProgramme>, now: Long): NowNext {
    val ordered = rows.sortedBy { it.start }
    val current = ordered.filter { it.start <= now && it.stop != null && now < it.stop }
        .maxWithOrNull(compareBy<EpgProgramme> { it.start }.thenBy { it.stop })
    return NowNext(current, ordered.firstOrNull { it.start > now })
}

fun normalizeEpgUrl(value: String): String {
    val checked = M3uParser.httpUrl(value) ?: throw IllegalArgumentException("HTTP(S) URL required")
    return checked.toHttpUrl().newBuilder().fragment(null).build().toString()
}

private val xmlTime = Regex("^(\\d{12}|\\d{14})(?:\\s+(Z|UTC|GMT|[+-]\\d{4}))?$")
private val minutes = DateTimeFormatter.ofPattern("uuuuMMddHHmm", Locale.ROOT).withResolverStyle(ResolverStyle.STRICT)
private val seconds = DateTimeFormatter.ofPattern("uuuuMMddHHmmss", Locale.ROOT).withResolverStyle(ResolverStyle.STRICT)

/** XMLTV's absent zone means UTC, NOT the device zone; never depend on DST defaults. */
fun parseXmlTvTime(raw: String): Long? = runCatching {
    val match = xmlTime.matchEntire(raw.trim()) ?: return null
    val digits = match.groupValues[1]
    val local = LocalDateTime.parse(digits, if (digits.length == 14) seconds else minutes)
    if (local.year < 1970) return null
    val zone = when (val supplied = match.groupValues[2]) {
        "", "Z", "UTC", "GMT" -> ZoneOffset.UTC
        else -> ZoneOffset.of(supplied)
    }
    local.toInstant(zone).toEpochMilli()
}.getOrNull()

fun epgClock(time: Long, now: Long, zone: ZoneId = ZoneId.systemDefault()): String {
    val sameDay = Instant.ofEpochMilli(time).atZone(zone).toLocalDate() == Instant.ofEpochMilli(now).atZone(zone).toLocalDate()
    val pattern = if (sameDay) "HH:mm" else "MM/dd HH:mm"
    return DateTimeFormatter.ofPattern(pattern, Locale.ROOT).withZone(zone).format(Instant.ofEpochMilli(time))
        .map { if (it in '0'..'9') '۰' + (it - '0') else it }.joinToString("")
}

private val titleWhitespace = Regex("\\s+")
internal fun cleanEpgTitle(value: String): String {
    val clean = buildString {
        value.forEach { character ->
            when {
                character in "\r\n\t" -> append(' ')
                character.code < 0x20 || character.code in 0x7f..0x9f ||
                    character.code in 0x202a..0x202e || character.code in 0x2066..0x2069 ||
                    character.code == 0x061c || character.code == 0x200e || character.code == 0x200f -> Unit
                else -> append(character)
            }
        }
    }.replace(titleWhitespace, " ").trim().take(MAX_EPG_TITLE)
    // Never split an emoji/supplementary character at the UTF-16 size boundary.
    return if (clean.lastOrNull()?.isHighSurrogate() == true) clean.dropLast(1) else clean
}
