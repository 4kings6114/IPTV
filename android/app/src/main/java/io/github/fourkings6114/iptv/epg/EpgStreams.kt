package io.github.fourkings6114.iptv.epg

import java.io.FilterReader
import java.io.InputStream
import java.io.InputStreamReader
import java.io.Reader
import java.nio.charset.CodingErrorAction
import java.util.zip.GZIPInputStream

/** Count actual bytes, including chunked bodies; Content-Length alone is not a limit. */
internal class EpgBoundedStream(private val input: InputStream, private val limit: Long) : InputStream() {
    private var count = 0L
    private fun consumed(size: Int): Int {
        if (size > 0) { count += size; if (count > limit) throw EpgException(EpgException.Reason.LIMIT) }
        return size
    }
    override fun read(): Int = input.read().also { if (it >= 0) consumed(1) }
    override fun read(buffer: ByteArray, offset: Int, length: Int): Int = consumed(input.read(buffer, offset, length))
    override fun close() = input.close()
}

internal fun epgReader(input: InputStream, limits: EpgLimits): Reader {
    var decoded = EpgBoundedStream(input, limits.downloadBytes.toLong()).buffered()
    // A .gz file may additionally have HTTP gzip encoding; at most two layers,
    // with a fresh expanded-byte cap at EACH layer, never an unbounded readBytes().
    repeat(2) {
        decoded.mark(2)
        val first = decoded.read()
        val second = decoded.read()
        decoded.reset()
        if (first == 0x1f && second == 0x8b) {
            decoded = EpgBoundedStream(GZIPInputStream(decoded), limits.expandedBytes.toLong()).buffered()
        }
    }
    decoded = EpgBoundedStream(decoded, limits.expandedBytes.toLong()).buffered()
    decoded.mark(4)
    val bytes = ByteArray(4)
    var size = 0
    while (size < bytes.size) {
        val value = decoded.read()
        if (value < 0) break
        bytes[size++] = value.toByte()
    }
    decoded.reset()
    fun byte(index: Int) = if (index < size) bytes[index].toInt() and 0xff else -1
    val encoding = when {
        byte(0) == 0xff && byte(1) == 0xfe -> "UTF-16LE"
        byte(0) == 0xfe && byte(1) == 0xff -> "UTF-16BE"
        byte(0) == 0 && byte(1) == 0x3c -> "UTF-16BE"
        byte(0) == 0x3c && byte(1) == 0 -> "UTF-16LE"
        else -> "UTF-8"
    }
    // Reader-based XmlPullParser input does not consistently consume a BOM.
    // Strip only an actual leading BOM, after sniffing and before decoding.
    val bomBytes = when {
        (byte(0) == 0xff && byte(1) == 0xfe) || (byte(0) == 0xfe && byte(1) == 0xff) -> 2
        byte(0) == 0xef && byte(1) == 0xbb && byte(2) == 0xbf -> 3
        else -> 0
    }
    repeat(bomBytes) { decoded.read() }
    val decoder = java.nio.charset.Charset.forName(encoding).newDecoder()
        .onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT)
    return EpgXmlReader(InputStreamReader(decoded, decoder))
}

/** Reject giant tags/attribute lists before the XML parser can allocate them.
 * This conservative lexical budget also bounds long text runs/comments; it does
 * not interpret XML, expand entities or claim that the document is well formed.
 */
private class EpgXmlReader(reader: Reader) : FilterReader(reader) {
    private var mode = 0 // text, normal tag, comment, CDATA
    private var quote: Char? = null
    private var run = 0
    private val prefix = StringBuilder(9)
    private var doctype = false
    private var previous = ' '
    private var beforePrevious = ' '
    private fun inspect(character: Char) {
        if (mode == 0 && character == '<') {
            mode = 1; quote = null; run = 0; prefix.setLength(0); doctype = false
        }
        run++
        if (run > 32_768) throw EpgException(EpgException.Reason.LIMIT)
        if (mode == 1) {
            if (prefix.length < 9) {
                prefix.append(character)
                if (prefix.length == 4 && prefix.toString() == "<!--") { mode = 2; quote = null }
                if (prefix.length == 9 && prefix.toString() == "<![CDATA[") { mode = 3; quote = null }
                if (prefix.length == 9 && prefix.toString() == "<!DOCTYPE") doctype = true
            }
            if (doctype && quote == null && character == '[') throw EpgException(EpgException.Reason.INVALID_XML)
            if (mode == 1) when {
                character == quote -> quote = null
                quote == null && character in "\"'" -> quote = character
                quote == null && character == '>' -> { mode = 0; run = 0 }
            }
        } else if ((mode == 2 && beforePrevious == '-' && previous == '-' && character == '>') ||
            (mode == 3 && beforePrevious == ']' && previous == ']' && character == '>')) {
            mode = 0; run = 0
        }
        beforePrevious = previous
        previous = character
    }
    override fun read(): Int = super.read().also { if (it >= 0) inspect(it.toChar()) }
    override fun read(buffer: CharArray, offset: Int, length: Int): Int {
        val size = super.read(buffer, offset, length)
        if (size > 0) for (index in offset until offset + size) inspect(buffer[index])
        return size
    }
}
