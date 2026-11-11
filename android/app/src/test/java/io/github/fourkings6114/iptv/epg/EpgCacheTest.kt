package io.github.fourkings6114.iptv.epg

import android.app.Application
import io.github.fourkings6114.iptv.core.M3uParser
import io.github.fourkings6114.iptv.core.sha256
import io.github.fourkings6114.iptv.data.SourceEntity
import java.io.File
import java.io.RandomAccessFile
import kotlinx.coroutines.CancellationException
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class EpgCacheTest {
    @get:Rule val temporary = TemporaryFolder()
    private val url = "https://example.invalid/guide.xml?token=DO_NOT_LOG_THIS"
    private fun guide(title: String = "Current programme", fetched: Long = GUIDE_NOW) =
        XmlTvParser.parse(guideXml(title = title).byteInputStream(), setOf("Exact.ID"), fetched)

    @Test fun privateCacheRoundTripsAndContainsNoGuideUrlTokenOrDescriptions() {
        val directory = temporary.newFolder()
        val original = guide()
        EpgCache(directory).write("source-one", url, original)
        val reopened = EpgCache(directory).read("source-one", url, GUIDE_NOW)!!
        assertEquals(original.programmes, reopened.programmes)
        assertEquals(original.fetchedAt, reopened.fetchedAt)
        val stored = directory.listFiles()!!.single()
        assertEquals(sha256("source-one") + ".json", stored.name)
        assertFalse(stored.readText().contains("DO_NOT_LOG_THIS"))
        assertFalse(stored.readText().contains("example.invalid"))
        assertNull(EpgCache(directory).read("other-source", url, GUIDE_NOW))
        assertNull(EpgCache(directory).read("source-one", url + "changed", GUIDE_NOW))
    }

    @Test fun staleGuidesRemainUsableButExpiredOrFutureDatedCachesAreHidden() {
        val cache = EpgCache(temporary.newFolder())
        cache.write("id", url, guide())
        assertTrue(cache.read("id", url, GUIDE_NOW + EPG_STALE_AFTER)!!.stale(GUIDE_NOW + EPG_STALE_AFTER))
        assertNull(cache.read("id", url, GUIDE_NOW + EPG_CACHE_MAX_AGE + 1))
        assertNull(cache.read("id", url, GUIDE_NOW - 300_001))
    }

    @Test fun cancelledAtomicCacheWritesPreserveTheLastKnownGoodGuide() {
        val cache = EpgCache(temporary.newFolder())
        val previous = guide()
        cache.write("id", url, previous)
        var checks = 0
        assertThrows(CancellationException::class.java) {
            cache.write("id", url, guide("Must not replace")) { if (++checks == 2) throw CancellationException() }
        }
        assertEquals(2, checks)
        assertEquals(previous.programmes, cache.read("id", url, GUIDE_NOW)!!.programmes)
    }

    @Test fun corruptOversizedAndWrongFormatCachesCannotCrashBrowsing() {
        val directory = temporary.newFolder()
        val file = File(directory, sha256("id") + ".json")
        val cache = EpgCache(directory)
        listOf("not json", "[]", "{\"format\":99}", "{\"format\":1,\"programmes\":null}").forEach {
            file.writeText(it)
            assertNull(cache.read("id", url, GUIDE_NOW))
        }
        cache.write("id", url, guide())
        file.writeText(file.readText().replace("Current programme", "\\u202eHidden control"))
        assertNull(cache.read("id", url, GUIDE_NOW))
        RandomAccessFile(file, "rw").use { it.setLength(EpgCache.MAX_BYTES + 1) }
        assertNull(cache.read("id", url, GUIDE_NOW))
    }

    @Test fun guideCacheEvictionIsBoundedAndNeverDeletesOtherAppFiles() {
        val directory = temporary.newFolder()
        val sentinel = File(directory, "not-a-guide.txt").also { it.writeText("keep") }
        val cache = EpgCache(directory)
        repeat(12) { cache.write("source-$it", url, guide()) }
        assertTrue(directory.listFiles()!!.count { it.name.endsWith(".json") } <= 8)
        assertNotNull(cache.read("source-11", url, GUIDE_NOW))
        assertEquals("keep", sentinel.readText())
        cache.remove("source-11")
        assertNull(cache.read("source-11", url, GUIDE_NOW))
    }

    @Test fun rewrittenGuideIdsAreSourceScopedAndNeverChangeSavedChannelIdentity() {
        val text = "#EXTM3U\n#EXTINF:-1 tvg-id=Guide.One x-original-tvg-id=Legacy.ID,Shared\nhttps://example.invalid/live\n"
        val one = M3uParser.parse(text)
        val two = M3uParser.parse(text.replace("Guide.One", "Guide.Two"))
        val channel = one.channels.single()
        assertEquals(channel.id, two.channels.single().id)
        assertEquals("Legacy.ID", channel.tvgId)
        val first = SourceEntity("one", "One", updatedAt = 101, count = 1)
        val second = first.copy(id = "two", title = "Two")
        val directory = temporary.newFolder()
        EpgChannelIds(directory).apply {
            save(first, one.channels, one.xmltvIds)
            save(second, two.channels, two.xmltvIds)
        }
        val reopened = EpgChannelIds(directory)
        assertEquals("Guide.One", reopened.forCatalog(first, one.channels)[channel.id])
        assertEquals("Guide.Two", reopened.forCatalog(second, one.channels)[channel.id])
        assertEquals("Legacy.ID", reopened.forCatalog(first.copy(updatedAt = 102), one.channels)[channel.id])
        reopened.remove(first.id)
        assertEquals("Legacy.ID", reopened.forCatalog(first, one.channels)[channel.id])
        assertEquals("Guide.Two", reopened.forCatalog(second, one.channels)[channel.id])
    }

    @Test fun unboundedLastProgrammeWithNoStopSurvivesCacheRoundTripWithoutBecomingCurrent() {
        val guide = EpgGuide(GUIDE_NOW, listOf(EpgProgramme("Exact.ID", "Unknown end", GUIDE_NOW - 60_000, null)))
        val cache = EpgCache(temporary.newFolder())
        cache.write("id", url, guide)
        assertNull(cache.read("id", url, GUIDE_NOW)!!.programmes.single().stop)
        assertEquals(NowNext(), cache.read("id", url, GUIDE_NOW)!!.at("Exact.ID", GUIDE_NOW))
    }
}
