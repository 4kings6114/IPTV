package io.github.fourkings6114.iptv.epg

import android.util.AtomicFile
import io.github.fourkings6114.iptv.core.Channel
import io.github.fourkings6114.iptv.core.M3uParser
import io.github.fourkings6114.iptv.core.sha256
import io.github.fourkings6114.iptv.data.SourceEntity
import java.io.File
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.longOrNull

/** Small, derived per-catalog alias sidecar. Room v1 and Channel.id/tvgId remain
 * byte-for-byte compatible with installed versions. A rewritten M3U tvg-id is
 * used for XMLTV, while x-original-tvg-id still owns the saved channel identity.
 * A sidecar is accepted only for the exact committed catalog timestamp.
 * Call on IO; the monitor also protects AtomicFile's read/write recovery.
 */
class EpgChannelIds(private val directory: File) {
    private val changes = MutableStateFlow(0L)
    val revisions = changes.asStateFlow()
    private fun atomic(id: String) = AtomicFile(File(directory, sha256(id) + ".json"))

    @Synchronized
    fun save(source: SourceEntity, channels: List<Channel>, xmltvIds: Map<String, String>) {
        // Only overrides need a sidecar; ordinary TVG IDs already live in Room.
        val aliases = channels.mapNotNull { channel ->
            val actual = xmltvIds[channel.id] ?: channel.tvgId.trim()
            if (actual == channel.tvgId.trim()) null else channel.id to JsonPrimitive(actual)
        }.toMap()
        try {
            if (aliases.isEmpty()) { atomic(source.id).delete(); return }
            val bytes = JsonObject(mapOf("catalog" to JsonPrimitive(source.updatedAt), "aliases" to JsonObject(aliases)))
                .toString().toByteArray(Charsets.UTF_8)
            if (bytes.size > MAX_BYTES) throw EpgException(EpgException.Reason.LIMIT)
            if (!directory.isDirectory && !directory.mkdirs()) throw EpgException(EpgException.Reason.CACHE_IO)
            val target = atomic(source.id)
            val output = target.startWrite()
            try { output.write(bytes); target.finishWrite(output)
            } catch (failure: Exception) { target.failWrite(output); throw failure }
        } finally { changes.value++ }
    }

    @Synchronized
    fun forCatalog(source: SourceEntity, channels: List<Channel>): Map<String, String> {
        val aliases = try {
            val bytes = atomic(source.id).openRead().use { EpgBoundedStream(it, MAX_BYTES).use { bounded -> bounded.readBytes() } }
            val root = Json.parseToJsonElement(bytes.toString(Charsets.UTF_8)) as? JsonObject
            if ((root?.get("catalog") as? JsonPrimitive)?.longOrNull == source.updatedAt)
                (root?.get("aliases") as? JsonObject)?.takeIf { it.size <= M3uParser.MAX_CHANNELS } else null
        } catch (_: Exception) { null }
        return channels.associate { channel ->
            val actual = (aliases?.get(channel.id) as? JsonPrimitive)?.content ?: channel.tvgId.trim()
            channel.id to actual.takeIf { it.length <= MAX_EPG_ID }.orEmpty()
        }
    }

    @Synchronized
    fun remove(id: String) { atomic(id).delete(); changes.value++ }

    companion object { private const val MAX_BYTES = 16L * 1024 * 1024 }
}
