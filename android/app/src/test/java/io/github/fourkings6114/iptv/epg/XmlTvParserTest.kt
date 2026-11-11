package io.github.fourkings6114.iptv.epg

import android.app.Application
import java.io.ByteArrayInputStream
import java.io.FilterInputStream
import java.io.IOException
import kotlinx.coroutines.CancellationException
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class XmlTvParserTest {
    private fun parse(text: String, limits: EpgLimits = EpgLimits()) =
        XmlTvParser.parse(text.byteInputStream(), setOf("Exact.ID"), GUIDE_NOW, limits)
    private fun reason(expected: EpgException.Reason, text: String, limits: EpgLimits = EpgLimits()) {
        assertEquals(expected, assertThrows(EpgException::class.java) { parse(text, limits) }.reason)
    }

    @Test fun streamingXmltvSelectsPersianTitlesAndInfersOnlyTheNextDistinctStop() {
        val current = """<programme channel="Exact.ID" start="${xmlTime(GUIDE_NOW - 60_000)}"><title lang="en">News</title><title lang="fa">خبر &amp; تازه‌ام</title><desc>Not retained or fetched</desc><icon src="https://example.invalid/never.png"/></programme>"""
        val guide = parse("<tv>${programme(start = GUIDE_NOW + 60_000, stop = null)}$current$current</tv>")
        assertEquals(2, guide.programmes.size)
        assertEquals("خبر & تازه‌ام", guide.at("Exact.ID", GUIDE_NOW).current!!.title)
        assertEquals(GUIDE_NOW + 60_000, guide.at("Exact.ID", GUIDE_NOW).current!!.stop)
        assertNull(guide.at("Exact.ID", GUIDE_NOW).next!!.stop)
        assertNull(guide.at("Exact.ID", GUIDE_NOW + 60_000).current)
    }

    @Test fun programmeIdsMustMatchExactlyWithoutDisplayNameOrCaseGuessing() {
        reason(EpgException.Reason.NO_MATCH, "<tv><channel id=\"exact.id\"><display-name>Exact.ID</display-name></channel>${programme("exact.id")}</tv>")
    }

    @Test fun emptyIdsEmptyGuidesAndUnmatchedGuidesHaveDistinctFixedErrors() {
        assertEquals(EpgException.Reason.NO_IDS, assertThrows(EpgException::class.java) {
            XmlTvParser.parse(guideXml().byteInputStream(), emptySet(), GUIDE_NOW)
        }.reason)
        reason(EpgException.Reason.NO_PROGRAMMES, "<tv><channel id=\"Exact.ID\"/></tv>")
        reason(EpgException.Reason.NO_MATCH, "<tv>${programme("Unknown")}</tv>")
    }

    @Test fun xmltvSupportsPlainGzipHttpDoubleGzipAndUtf16WithoutEncodingGuesses() {
        val plain = guideXml(title = "برنامهٔ آزمایشی").toByteArray()
        val expected = XmlTvParser.parse(plain.inputStream(), setOf("Exact.ID"), GUIDE_NOW)
        val documents = listOf(plain, gzip(plain), gzip(gzip(plain)), byteArrayOf(0xef.toByte(), 0xbb.toByte(), 0xbf.toByte()) + plain,
            ("\uFEFF" + guideXml(title = "برنامهٔ آزمایشی")).toByteArray(Charsets.UTF_16LE),
            ("\uFEFF" + guideXml(title = "برنامهٔ آزمایشی")).toByteArray(Charsets.UTF_16BE))
        documents.forEachIndexed { index, bytes ->
            val actual = try { XmlTvParser.parse(bytes.inputStream(), setOf("Exact.ID"), GUIDE_NOW)
            } catch (failure: IOException) { throw AssertionError("Supported XMLTV encoding fixture $index failed", failure) }
            assertEquals("Encoding fixture $index", expected.programmes, actual.programmes)
        }
    }

    @Test fun encodingSniffingSurvivesSingleByteNetworkChunks() {
        val bytes = ("\uFEFF" + guideXml()).toByteArray(Charsets.UTF_16LE)
        val tinyChunks = object : FilterInputStream(ByteArrayInputStream(bytes)) {
            override fun read(buffer: ByteArray, offset: Int, length: Int) = super.read(buffer, offset, minOf(length, 1))
        }
        assertEquals(2, XmlTvParser.parse(tinyChunks, setOf("Exact.ID"), GUIDE_NOW).programmes.size)
    }

    @Test fun xmltvExternalDoctypeIsIgnoredAndNeverFetched() {
        MockWebServer().use { server ->
            server.start()
            val guide = parse("<!DOCTYPE tv SYSTEM \"${server.url("/secret.dtd")}\">" + guideXml())
            assertEquals(2, guide.programmes.size)
            assertEquals(0, server.requestCount)
        }
    }

    @Test fun xmltvRejectsInternalDtdEntitiesAndUtf16XxeBeforeExpansion() {
        val xxe = "<!DOCTYPE tv [<!ENTITY stolen SYSTEM \"file:///private.txt\">]><tv>${programme(title = "&stolen;")}</tv>"
        reason(EpgException.Reason.INVALID_XML, xxe)
        val error = assertThrows(EpgException::class.java) {
            XmlTvParser.parse(("\uFEFF" + xxe).toByteArray(Charsets.UTF_16LE).inputStream(), setOf("Exact.ID"), GUIDE_NOW)
        }
        assertEquals(EpgException.Reason.INVALID_XML, error.reason)
        reason(EpgException.Reason.INVALID_XML, "<!DOCTYPE tv [<!ENTITY a 'a'><!ENTITY b '&a;&a;'>]><tv>${programme(title = "&b;")}</tv>")
        reason(EpgException.Reason.INVALID_XML, "<tv>${programme(title = "&undefined;")}</tv>")
    }

    @Test fun commentsCdataAndPredefinedEntitiesAreNotMisreadAsDtds() {
        val guide = parse("<!-- don't fetch <!DOCTYPE and don't interpret quotes ' or > -->" +
            "<tv>${programme(title = "<![CDATA[Don't <guess> \"titles\"]]> &amp; خبر")}</tv>")
        assertEquals("Don't <guess> \"titles\" & خبر", guide.programmes.single().title)
    }

    @Test fun malformedWrongRootAndNamespacedDocumentsCannotReplaceAGuide() {
        listOf("<html>not XMLTV</html>", "<tv>", "<tv></tv><tv></tv>", "<tv xmlns=\"urn:other\">${programme()}</tv>",
            "<tv>${programme().replace("</title>", "</wrong>")}</tv>").forEachIndexed { index, text ->
            assertEquals("Malformed XML fixture $index", EpgException.Reason.INVALID_XML,
                assertThrows(EpgException::class.java) { parse(text) }.reason)
        }
    }

    @Test fun invalidStopTimesExpiredEntriesAndFarFutureDataAreNotInvented() {
        listOf(programme(stop = GUIDE_NOW - 120_000), programme().replace("stop=\"${xmlTime(GUIDE_NOW + 60_000)}\"", "stop=\"bad\""),
            programme(start = GUIDE_NOW - 86_400_000, stop = GUIDE_NOW - 80_000_000),
            programme(start = GUIDE_NOW + 8 * 86_400_000L, stop = GUIDE_NOW + 8 * 86_400_000L + 60_000)).forEach {
            reason(EpgException.Reason.NO_MATCH, "<tv>$it</tv>")
        }
    }

    @Test fun compressedAndExpandedByteLimitsAreEnforcedIndependently() {
        val text = guideXml(title = "x".repeat(1000))
        reason(EpgException.Reason.LIMIT, text, EpgLimits(downloadBytes = 64))
        val compressed = gzip(text.toByteArray())
        assertEquals(EpgException.Reason.LIMIT, assertThrows(EpgException::class.java) {
            XmlTvParser.parse(compressed.inputStream(), setOf("Exact.ID"), GUIDE_NOW, EpgLimits(downloadBytes = 4096, expandedBytes = 512))
        }.reason)
        assertThrows(IOException::class.java) { XmlTvParser.parse(compressed.dropLast(5).toByteArray().inputStream(), setOf("Exact.ID"), GUIDE_NOW) }
    }

    @Test fun parsedSavedAndCharacterBudgetsAreIndependent() {
        reason(EpgException.Reason.LIMIT, "<tv>${programme("Other")}${programme()}${programme(start = GUIDE_NOW + 1000)}</tv>", EpgLimits(programmes = 2))
        reason(EpgException.Reason.LIMIT, guideXml(), EpgLimits(savedProgrammes = 1))
        reason(EpgException.Reason.LIMIT, guideXml(), EpgLimits(savedCharacters = 32))
    }

    @Test fun giantTagsAttributesTextAndDeepNestingHitBoundedLimits() {
        val documents = listOf("<tv><extra " + (1..65).joinToString(" ") { "a$it=\"v\"" } + "/></tv>",
            "<tv><extra value=\"${"a".repeat(33_000)}\"/></tv>",
            "<tv><!--${"a".repeat(33_000)}--></tv>",
            "<tv>${programme(title = "a".repeat(33_000))}</tv>",
            "<tv>" + "<extra>".repeat(33) + "</extra>".repeat(33) + "</tv>")
        documents.forEach { reason(EpgException.Reason.LIMIT, it) }
    }

    @Test fun parserCancellationIsCheckedEvenWhenEverythingWasAlreadyBuffered() {
        var checks = 0
        assertThrows(CancellationException::class.java) {
            XmlTvParser.parse(("<tv>" + programme().repeat(100) + "</tv>").byteInputStream(), setOf("Exact.ID"), GUIDE_NOW,
                checkCancelled = { checks++; throw CancellationException() })
        }
        assertTrue(checks > 0)
    }
}
