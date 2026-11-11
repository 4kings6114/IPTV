package io.github.fourkings6114.iptv.epg

import java.time.Instant
import java.time.ZoneId
import java.util.TimeZone
import org.junit.Assert.*
import org.junit.Test

class EpgGuideTest {
    private val now = Instant.parse("2026-11-10T12:00:00Z").toEpochMilli()
    private fun row(title: String, start: Long, stop: Long?) = EpgProgramme("Exact.ID", title, now + start, stop?.let { now + it })

    @Test fun xmltvOffsetsAndMissingZoneDoNotDependOnThePhoneZone() {
        val before = TimeZone.getDefault()
        try {
            TimeZone.setDefault(TimeZone.getTimeZone("Pacific/Honolulu"))
            assertEquals(now, parseXmlTvTime("20261110120000"))
            assertEquals(now, parseXmlTvTime("202611101200 Z"))
            assertEquals(now, parseXmlTvTime("20261110153000 +0330"))
            assertEquals(now, parseXmlTvTime("202611100700 -0500"))
            assertEquals(now, parseXmlTvTime("20261110120000 UTC"))
            assertEquals(now, parseXmlTvTime("202611101200 GMT"))
        } finally { TimeZone.setDefault(before) }
    }

    @Test fun invalidAndAmbiguousXmltvTimesAreRejectedInsteadOfGuessed() {
        listOf("", "20261110", "20260230120000 +0000", "20261310120000", "20261110240000", "20261110126000",
            "19691110120000", "20261110120000 Europe/Berlin", "20261110120000 +1860", "20261110120000 +1900",
            "20261110120000 +03:30", "20261110120000 +0000 trailing", "2026111012000").forEach { assertNull(it, parseXmlTvTime(it)) }
    }

    @Test fun explicitDstOffsetsKeepRepeatedLocalHoursDistinct() {
        val summer = parseXmlTvTime("20261025023000 +0200")!!
        val winter = parseXmlTvTime("20261025023000 +0100")!!
        assertEquals(3_600_000L, winter - summer)
        assertEquals("۰۲:۳۰", epgClock(summer, summer, ZoneId.of("Europe/Berlin")))
        assertEquals("۰۲:۳۰", epgClock(winter, winter, ZoneId.of("Europe/Berlin")))
    }

    @Test fun nowNextUsesStartInclusiveStopExclusiveAndSortedFutureStarts() {
        val past = row("Past", -2000, 0)
        val current = row("Now", 0, 1000)
        val next = row("Next", 1000, 2000)
        assertEquals(NowNext(current, next), nowNext(listOf(next, past, current), now))
        assertEquals(NowNext(next, null), nowNext(listOf(past, current, next), now + 1000))
        assertEquals(NowNext(), nowNext(listOf(past, current, next), now + 2000))
    }

    @Test fun scheduleGapsAndUnboundedLastEntriesNeverInventAnOnAirProgramme() {
        val unknownEnd = row("No known stop", -1000, null)
        val future = row("After gap", 5000, 6000)
        assertEquals(NowNext(null, future), nowNext(listOf(unknownEnd, future), now))
        assertEquals(NowNext(), nowNext(listOf(unknownEnd), now))
    }

    @Test fun overlappingRowsPreferTheLatestActualStartDeterministically() {
        val old = row("Long entry", -5000, 5000)
        val newer = row("Newer entry", -1000, 1000)
        assertEquals(newer, nowNext(listOf(newer, old), now).current)
        assertEquals(old, nowNext(listOf(newer, old), now + 1000).current)
    }

    @Test fun exactCaseSensitiveIdsDoNotGuessFromNamesOrPrefixes() {
        val current = row("Now", -1000, 1000)
        val guide = EpgGuide(now, listOf(current))
        assertEquals(current, guide.at("Exact.ID", now).current)
        assertEquals(NowNext(), guide.at("exact.id", now))
        assertEquals(NowNext(), guide.at("Exact", now))
        assertEquals(NowNext(), guide.at("Now", now))
    }

    @Test fun cacheAgeAndFutureClockSkewHaveExplicitBounds() {
        val guide = EpgGuide(now, emptyList())
        assertTrue(guide.usable(now + EPG_CACHE_MAX_AGE))
        assertFalse(guide.usable(now + EPG_CACHE_MAX_AGE + 1))
        assertFalse(guide.stale(now + EPG_STALE_AFTER - 1))
        assertTrue(guide.stale(now + EPG_STALE_AFTER))
        assertTrue(guide.usable(now - 300_000))
        assertFalse(guide.usable(now - 300_001))
        assertFalse(EpgGuide(0, emptyList()).usable(now))
    }

    @Test fun localClockUsesTheDeviceZoneAndMarksADifferentDay() {
        assertEquals("۱۵:۳۰", epgClock(now, now, ZoneId.of("Asia/Tehran")))
        assertEquals("۱۳:۰۰", epgClock(now, now, ZoneId.of("Europe/Berlin")))
        assertEquals("۱۱/۱۱ ۱۳:۰۰", epgClock(now + 86_400_000, now, ZoneId.of("Europe/Berlin")))
    }

    @Test fun guideUrlsRejectUserInfoLocalSchemesAndControlCharacters() {
        listOf("file:///etc/passwd", "content://test", "https://user:pass@example.com/a", "https://example.com/a\n",
            "https://example.com:99999/a", "/relative.xml", "not a URL").forEach { raw ->
            assertThrows(raw, IllegalArgumentException::class.java) { normalizeEpgUrl(raw) }
        }
        assertEquals("https://example.com/guide.xml?token=private", normalizeEpgUrl(" https://example.com/guide.xml?token=private#part "))
    }

    @Test fun titlesAreBoundedAndCannotInjectControlsOrBidiOverrides() {
        assertEquals("برنامهٔ تازه‌ام & خبر", cleanEpgTitle("\u202eبرنامهٔ\n\tتازه‌ام & خبر\u2069\u0001"))
        assertEquals(MAX_EPG_TITLE, cleanEpgTitle("x".repeat(1000)).length)
        assertFalse(cleanEpgTitle("x".repeat(MAX_EPG_TITLE - 1) + "😀").last().isHighSurrogate())
    }
}
