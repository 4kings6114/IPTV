package io.github.fourkings6114.iptv.epg

import android.util.AtomicFile
import io.github.fourkings6114.iptv.core.sha256
import java.io.File
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull

/** Derived, evictable private cache, separate from Room v1 and user preferences.
 * Caller serializes access on IO. No raw guide URL/token is written to these files.
 */
class EpgCache(private val directory: File) {
    companion object {
        const val MAX_BYTES = 8L * 1024 * 1024
        private const val MAX_TOTAL_BYTES = 24L * 1024 * 1024
        private const val MAX_FILES = 8
    }
    private fun atomic(id: String) = AtomicFile(File(directory, sha256(id) + ".json"))

    fun read(id: String, url: String, now: Long): EpgGuide? {
        if (url.isBlank()) return null
        return try {
            val bytes = atomic(id).openRead().use { EpgBoundedStream(it, MAX_BYTES).use { bounded -> bounded.readBytes() } }
            val root = Json.parseToJsonElement(bytes.toString(Charsets.UTF_8)) as? JsonObject ?: return null
            if ((root["format"] as? JsonPrimitive)?.intOrNull != 1 ||
                (root["feed"] as? JsonPrimitive)?.content != sha256(normalizeEpgUrl(url))) return null
            val fetched = (root["fetched"] as? JsonPrimitive)?.longOrNull ?: return null
            val rows = root["programmes"] as? JsonArray ?: return null
            if (rows.isEmpty() || rows.size > EpgLimits().savedProgrammes) return null
            var characters = 0L
            val programmes = rows.map { item ->
                val row = item as? JsonArray ?: return null
                if (row.size != 4) return null
                val channel = (row[0] as? JsonPrimitive)?.content ?: return null
                val title = (row[1] as? JsonPrimitive)?.content ?: return null
                val start = (row[2] as? JsonPrimitive)?.longOrNull ?: return null
                val stop = if (row[3] == JsonNull) null else { (row[3] as? JsonPrimitive)?.longOrNull ?: return null }
                if (channel.length !in 1..MAX_EPG_ID || channel != channel.trim() || title.isBlank() ||
                    title.length > MAX_EPG_TITLE || title != cleanEpgTitle(title) || start < 0 ||
                    start > 253_402_300_799_000L || stop != null && (stop <= start || stop - start > 86_400_000L)) return null
                characters += channel.length + title.length + 64L
                if (characters > EpgLimits().savedCharacters) return null
                EpgProgramme(channel, title, start, stop)
            }
            EpgGuide(fetched, programmes).takeIf { it.usable(now) }
        } catch (_: Exception) { null } // Corrupt/expired derived data must never crash browsing.
    }

    fun write(id: String, url: String, guide: EpgGuide, checkCancelled: () -> Unit = {}) {
        val limits = EpgLimits()
        if (guide.programmes.isEmpty() || guide.programmes.size > limits.savedProgrammes ||
            guide.programmes.sumOf { it.channelId.length + it.title.length + 64L } > limits.savedCharacters)
            throw EpgException(EpgException.Reason.LIMIT)
        val rows = JsonArray(guide.programmes.map { programme ->
            JsonArray(listOf(JsonPrimitive(programme.channelId), JsonPrimitive(programme.title),
                JsonPrimitive(programme.start), programme.stop?.let(::JsonPrimitive) ?: JsonNull))
        })
        val bytes = JsonObject(mapOf("format" to JsonPrimitive(1), "feed" to JsonPrimitive(sha256(normalizeEpgUrl(url))),
            "fetched" to JsonPrimitive(guide.fetchedAt), "programmes" to rows)).toString().toByteArray(Charsets.UTF_8)
        if (bytes.size > MAX_BYTES) throw EpgException(EpgException.Reason.LIMIT)
        if (!directory.isDirectory && !directory.mkdirs()) throw EpgException(EpgException.Reason.CACHE_IO)
        checkCancelled()
        val target = atomic(id)
        val output = target.startWrite()
        try {
            output.write(bytes)
            checkCancelled()
            target.finishWrite(output)
        } catch (failure: Exception) {
            target.failWrite(output)
            throw failure
        }
        prune(sha256(id))
    }

    fun remove(id: String) { atomic(id).delete() }

    private fun prune(retain: String) {
        val files = directory.listFiles { file -> file.name.matches(Regex("[0-9a-f]{64}\\.json(?:\\.(new|bak))?")) }
            .orEmpty().sortedBy { it.lastModified() }.toMutableList()
        var bytes = files.sumOf { it.length() }
        var count = files.size
        for (file in files) {
            if (bytes <= MAX_TOTAL_BYTES && count <= MAX_FILES) break
            val size = file.length()
            if (!file.name.startsWith(retain) && file.delete()) { bytes -= size; count-- }
        }
    }
}
