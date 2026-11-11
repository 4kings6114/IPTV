package io.github.fourkings6114.iptv.epg

import android.util.Xml
import java.io.InputStream
import org.xmlpull.v1.XmlPullParser
import org.xmlpull.v1.XmlPullParserException

/** Streaming UTF-8/UTF-16 XMLTV, filtered to the selected catalog's exact IDs.
 * DTD fetching/validation is OFF. A harmless external XMLTV DOCTYPE is ignored;
 * internal subsets/entities are rejected, including UTF-16 documents.
 */
object XmlTvParser {
    fun parse(input: InputStream, channelIds: Set<String>, now: Long,
              limits: EpgLimits = EpgLimits(), checkCancelled: () -> Unit = {}): EpgGuide {
        if (channelIds.isEmpty()) throw EpgException(EpgException.Reason.NO_IDS)
        try {
            epgReader(input, limits).use { reader ->
                val parser = Xml.newPullParser()
                parser.setFeature(XmlPullParser.FEATURE_PROCESS_NAMESPACES, true)
                parser.setFeature(XmlPullParser.FEATURE_PROCESS_DOCDECL, false)
                parser.setInput(reader)
                val cursor = Cursor(parser, checkCancelled)
                val kept = mutableListOf<EpgProgramme>()
                var seenRoot = false
                var closedRoot = false
                var seenProgrammes = 0
                var savedCharacters = 0L
                while (cursor.next() != XmlPullParser.END_DOCUMENT) {
                    if (parser.eventType == XmlPullParser.END_TAG && parser.depth == 1) closedRoot = true
                    if (parser.eventType == XmlPullParser.TEXT && parser.depth == 0 && !parser.isWhitespace) invalid()
                    if (parser.eventType != XmlPullParser.START_TAG) continue
                    if (parser.depth == 1) {
                        if (seenRoot || parser.name != "tv" || !parser.namespace.isNullOrEmpty()) invalid()
                        seenRoot = true
                    } else if (parser.depth == 2 && parser.name == "programme" && parser.namespace.isNullOrEmpty()) {
                        if (++seenProgrammes > limits.programmes) tooLarge()
                        val channel = parser.getAttributeValue(null, "channel").orEmpty().trim()
                        val start = parseXmlTvTime(parser.getAttributeValue(null, "start").orEmpty())
                        val rawStop = parser.getAttributeValue(null, "stop").orEmpty()
                        val stop = rawStop.takeIf { it.isNotBlank() }?.let(::parseXmlTvTime)
                        val valid = channel.length in 1..MAX_EPG_ID && channel in channelIds && start != null &&
                            (rawStop.isBlank() || stop != null && stop > start && stop - start <= 86_400_000L) &&
                            start <= now + 7L * 86_400_000 && (stop ?: start) > now - 6L * 3_600_000
                        if (!valid) { cursor.skipElement(); continue }
                        val title = cursor.programmeTitle()
                        if (title.isBlank()) continue
                        if (kept.size >= limits.savedProgrammes) tooLarge()
                        savedCharacters += channel.length + title.length + 64L
                        if (savedCharacters > limits.savedCharacters) tooLarge()
                        kept += EpgProgramme(channel, title, start!!, stop)
                    }
                }
                if (!seenRoot || !closedRoot) invalid()
                if (seenProgrammes == 0) throw EpgException(EpgException.Reason.NO_PROGRAMMES)
                if (kept.isEmpty()) throw EpgException(EpgException.Reason.NO_MATCH)
                // Missing stop can be inferred only from the next distinct start on
                // this same channel. Never label the last unbounded entry 'on now'.
                val normalized = kept.distinct().groupBy { it.channelId }.values.flatMap { entries ->
                    val ordered = entries.sortedBy { it.start }
                    var lastStart: Long? = null
                    var followingStart: Long? = null
                    ordered.asReversed().map { programme ->
                        if (lastStart != null && lastStart != programme.start) followingStart = lastStart
                        lastStart = programme.start
                        val nextStart = followingStart
                        if (programme.stop == null && nextStart != null && nextStart - programme.start <= 86_400_000L)
                            programme.copy(stop = nextStart) else programme
                    }.asReversed()
                }
                checkCancelled()
                return EpgGuide(now, normalized)
            }
        } catch (invalid: XmlPullParserException) {
            throw EpgException(EpgException.Reason.INVALID_XML) // omit untrusted parser text/location
        }
    }

    private class Cursor(private val parser: XmlPullParser, private val checkCancelled: () -> Unit) {
        private var tokens = 0
        fun next(): Int {
            if (++tokens % 64 == 0) checkCancelled()
            val event = parser.nextToken()
            if (parser.depth > 32 || event == XmlPullParser.START_TAG && parser.attributeCount > 64) tooLarge()
            if (event == XmlPullParser.DOCDECL) {
                val declaration = parser.text.orEmpty()
                if ('[' in declaration || '%' in declaration || declaration.contains("ENTITY", ignoreCase = true)) invalid()
            }
            if (event == XmlPullParser.ENTITY_REF && parser.text == null) invalid()
            return event
        }

        fun skipElement() {
            val depth = parser.depth
            while (next() != XmlPullParser.END_DOCUMENT) {
                if (parser.eventType == XmlPullParser.END_TAG && parser.depth == depth) return
            }
            invalid()
        }

        fun programmeTitle(): String {
            val depth = parser.depth
            var first = ""
            var persian = ""
            while (next() != XmlPullParser.END_DOCUMENT) {
                if (parser.eventType == XmlPullParser.END_TAG && parser.depth == depth) return persian.ifBlank { first }
                if (parser.eventType == XmlPullParser.START_TAG && parser.depth == depth + 1) {
                    if (parser.name == "title" && parser.namespace.isNullOrEmpty()) {
                        val language = parser.getAttributeValue(null, "lang").orEmpty()
                        val title = cleanEpgTitle(text())
                        if (first.isBlank()) first = title
                        if (persian.isBlank() && (language.startsWith("fa", true) || language.equals("per", true))) persian = title
                    } else skipElement() // descriptions, logos and links are not stored or fetched
                }
            }
            invalid()
        }

        private fun text(): String {
            val depth = parser.depth
            val result = StringBuilder()
            while (next() != XmlPullParser.END_DOCUMENT) {
                when (parser.eventType) {
                    XmlPullParser.END_TAG -> if (parser.depth == depth) return result.toString()
                    XmlPullParser.TEXT, XmlPullParser.CDSECT, XmlPullParser.ENTITY_REF, XmlPullParser.IGNORABLE_WHITESPACE ->
                        if (result.length < MAX_EPG_TITLE * 4) result.append(parser.text.orEmpty().take(MAX_EPG_TITLE * 4 - result.length))
                }
            }
            invalid()
        }
    }

    private fun invalid(): Nothing = throw EpgException(EpgException.Reason.INVALID_XML)
    private fun tooLarge(): Nothing = throw EpgException(EpgException.Reason.LIMIT)
}
