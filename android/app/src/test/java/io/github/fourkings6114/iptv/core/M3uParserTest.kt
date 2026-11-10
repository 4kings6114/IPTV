package io.github.fourkings6114.iptv.core

import java.io.File
import org.junit.Assert.*
import org.junit.Test

class M3uParserTest {
    private fun parse(text: String) = M3uParser.parse(text.trimIndent()).channels

    @Test fun quotedCommasAreNotDisplaySeparators() {
        val channel = parse("""
            #EXTM3U
            #EXTINF:-1 tvg-id="one" group-title="News, World",Channel, One
            https://example.com/live.m3u8
        """).single()
        assertEquals("Channel, One", channel.name)
        assertEquals("News, World", channel.group)
    }
    @Test fun supportsBomCrLfAndSingleQuotedAttributes() {
        val channel = parse("\uFEFF#EXTM3U\r\n#EXTINF:-1 group-title='فارسی' tvg-logo='https://example.com/a.png',یک\r\nhttps://example.com/live\r\n").single()
        assertEquals("فارسی", channel.group)
        assertEquals("https://example.com/a.png", channel.logo)
    }
    @Test fun extractsHeadersWithUrlOptionsTakingPrecedence() {
        val channel = parse("""
            #EXTM3U
            #EXTINF:-1,One
            #EXTVLCOPT:http-user-agent=VLC
            #EXTVLCOPT:http-referrer=https://example.com/
            #KODIPROP:inputstream.adaptive.stream_headers=User-Agent=Kodi&X-Token=a%2Bb
            https://example.com/live|User-Agent=Mobile&Authorization=Bearer+test
        """).single()
        assertEquals("https://example.com/live", channel.url)
        assertEquals("Mobile", channel.headers["user-agent"])
        assertEquals("https://example.com/", channel.headers["referer"])
        assertEquals("a+b", channel.headers["x-token"])
        assertEquals("Bearer test", channel.headers["authorization"])
    }
    @Test fun rejectsHeaderInjectionAndTransportOverrides() {
        val channel = parse("""
            #EXTM3U
            #EXTINF:-1,One
            https://example.com/live|User-Agent=ok%0D%0AInjected%3Ayes&Host=attacker.test&Range=bytes%3D0-1&X-Token=good
        """).single()
        assertEquals(mapOf("x-token" to "good"), channel.headers)
    }
    @Test fun supportsJsonHeadersAndExtGrp() {
        val channel = parse("""
            #EXTM3U
            #EXTINF:-1,One
            #EXTGRP:اخبار
            #EXTHTTP:{"User-Agent":"Mobile","Cookie":"test=value"}
            https://example.com/live
        """).single()
        assertEquals("اخبار", channel.group)
        assertEquals("test=value", channel.headers["cookie"])
    }
    @Test fun resolvesRelativeUrlsAgainstPlaylistLocation() {
        val channel = M3uParser.parse("#EXTM3U\n#EXTINF:-1,One\n../live/index.m3u8", "https://example.com/lists/main.m3u").channels.single()
        assertEquals("https://example.com/live/index.m3u8", channel.url)
    }
    @Test fun rejectsLocalSchemesUserInfoAndRelativeFileUrls() {
        listOf("file:///etc/passwd", "content://settings/test", "data:example", "rtsp://example.com/a", "https://user:password@example.com/a", "../a.m3u8").forEach { url ->
            assertNull(url, M3uParser.httpUrl(url))
        }
    }
    @Test fun retainsHttpButDoesNotGrantPermissionToUseIt() {
        assertTrue(parse("#EXTM3U\n#EXTINF:-1,One\nhttp://example.com/live").single().isCleartext)
    }
    @Test fun rejectsHlsMediaManifest() {
        val error = assertThrows(PlaylistParseException::class.java) { parse("#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nhttps://example.com/segment.ts") }
        assertEquals(PlaylistParseException.Reason.HLS_MANIFEST, error.reason)
    }
    @Test fun rejectsHlsMasterManifest() {
        assertThrows(PlaylistParseException::class.java) { parse("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100000\nhttps://example.com/stream.m3u8") }
    }
    @Test fun rejectsHtmlAndEmptyLists() {
        listOf("<html>not a list</html>", "#EXTM3U\n", "").forEach { text ->
            assertThrows(PlaylistParseException::class.java) { parse(text) }
        }
    }
    @Test fun skipsMalformedEntriesWithoutBleedingHeaders() {
        val result = M3uParser.parse("""
            #EXTM3U
            #EXTINF:-1,Bad
            #EXTVLCOPT:http-user-agent=secret
            file:///bad
            #EXTINF:-1,Good
            https://example.com/good
        """.trimIndent())
        assertEquals(1, result.skipped)
        assertTrue(result.channels.single().headers.isEmpty())
    }
    @Test fun keepsStableKeysAcrossCosmeticRenaming() {
        val original = parse("#EXTM3U\n#EXTINF:-1 tvg-id=one group-title=Old,Name\nhttps://example.com/a").single()
        val renamed = parse("#EXTM3U\n#EXTINF:-1 tvg-id=one group-title=New,Renamed\nhttps://example.com/a").single()
        assertEquals(original.id, renamed.id)
        assertEquals(64, original.id.length)
    }
    @Test fun deduplicatesExactEntriesButKeepsUrlVariants() {
        val channels = parse("#EXTM3U\n#EXTINF:-1 tvg-id=one,One\nhttps://example.com/a\n#EXTINF:-1 tvg-id=one,One\nhttps://example.com/a\n#EXTINF:-1 tvg-id=one,One HD\nhttps://example.com/b")
        assertEquals(2, channels.size)
    }
    @Test fun originalTvgIdentitySurvivesGuideAliasChanges() {
        val channel = parse("#EXTM3U\n#EXTINF:-1 tvg-id=123 x-original-tvg-id=One.de,One\nhttps://example.com/a").single()
        assertEquals("One.de", channel.tvgId)
    }
    @Test fun supportsPersianArabicSearchNormalization() {
        assertEquals("شبکه یک", normalizeSearch("شَبكه‌يـك"))
        assertEquals("news world", normalizeSearch(" NEWS   WORLD "))
        assertEquals("۱۲۳", persianNumber(123))
    }
    @Test fun enforcesChannelLimit() {
        val text = buildString {
            append("#EXTM3U\n")
            repeat(M3uParser.MAX_CHANNELS + 1) { append("#EXTINF:-1,Channel $it\nhttps://example.com/$it\n") }
        }
        val error = assertThrows(PlaylistParseException::class.java) { parse(text) }
        assertEquals(PlaylistParseException.Reason.TOO_MANY_CHANNELS, error.reason)
    }
    @Test fun bundledTrialHasTwentyDistinctChannels() {
        val file = File("src/main/assets/trial.m3u")
        val channels = M3uParser.parse(file.readText()).channels
        assertEquals(20, channels.size)
        assertEquals(20, channels.map { it.id }.toSet().size)
        assertTrue(channels.any { it.url.endsWith(".mpd") })
        assertTrue(channels.any { it.isCleartext })
        assertTrue(channels.any { it.headers.isNotEmpty() })
    }
    @Test fun importsEveryEntryInCurrentProjectPlaylistOffline() {
        val root = generateSequence(File(System.getProperty("user.dir"))) { it.parentFile }
            .first { File(it, "playlist.m3u").isFile }
        val text = File(root, "playlist.m3u").readText()
        val expected = text.lineSequence().count { it.startsWith("#EXTINF:") }
        val result = M3uParser.parse(text)
        assertEquals(expected, result.channels.size)
        assertEquals(0, result.skipped)
    }
}
