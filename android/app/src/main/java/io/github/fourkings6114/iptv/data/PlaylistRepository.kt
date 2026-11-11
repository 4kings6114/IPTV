package io.github.fourkings6114.iptv.data

import android.content.Context
import android.net.Uri
import io.github.fourkings6114.iptv.core.M3uParser
import io.github.fourkings6114.iptv.epg.EpgChannelIds
import io.github.fourkings6114.iptv.network.downloadPlaylist
import io.github.fourkings6114.iptv.network.readPlaylist
import java.io.IOException
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import kotlin.coroutines.coroutineContext

class PlaylistRepository(
    private val context: Context,
    val dao: ChannelDao,
    private val client: OkHttpClient,
    private val epgIds: EpgChannelIds? = null,
) {
    // Serializes refresh/rename/delete: an in-flight refresh must not resurrect a deleted source.
    private val sourceLock = Mutex()
    private suspend fun <T> edit(action: suspend () -> T): T = sourceLock.withLock {
        withContext(Dispatchers.IO) { action() }
    }

    suspend fun ensureSource(id: String) = edit {
        if (dao.source(id) == null) refreshLocked(id)
    }

    suspend fun refresh(id: String) = edit { refreshLocked(id) }

    private suspend fun refreshLocked(id: String) {
        if (id == SAMPLE_SOURCE_ID) {
            val text = context.assets.open("trial.m3u").use { it.readPlaylist() }
            save(id, SOURCE_PRESETS.first().title, "", text)
        } else {
            val preset = SOURCE_PRESETS.find { it.id == id }
            val stored = dao.source(id)
            if (preset == null && stored == null) throw SourceMissingException()
            val url = preset?.url ?: stored!!.url.takeIf { it.isNotBlank() }
                ?: throw FileSourceSelectionRequiredException()
            val downloaded = client.downloadPlaylist(url)
            save(id, preset?.title ?: stored!!.title, url, downloaded.text, downloaded.finalUrl)
        }
    }

    /** Each successful add gets a new ID, including repeated imports of the same URL/file. */
    suspend fun importUrl(raw: String, title: String = DEFAULT_LINK_SOURCE_TITLE): String = edit {
        val name = sourceTitle(title)
        val url = M3uParser.httpUrl(raw.trim()) ?: throw IllegalArgumentException("Invalid HTTP(S) playlist URL")
        val downloaded = client.downloadPlaylist(url)
        val id = newSourceId()
        save(id, name, url, downloaded.text, downloaded.finalUrl)
        id
    }

    suspend fun importFile(uri: Uri, title: String = DEFAULT_FILE_SOURCE_TITLE): String = edit {
        val name = sourceTitle(title)
        val text = readFile(uri)
        val id = newSourceId()
        save(id, name, "", text)
        id
    }

    /** SAF is requested again explicitly; never persist an unreliable/broad file permission. */
    suspend fun replaceFile(id: String, uri: Uri): String = edit {
        val stored = personalSource(id)
        if (!stored.isFileSource) throw NotAFileSourceException()
        save(id, stored.title, "", readFile(uri))
        id
    }

    suspend fun renameSource(id: String, rawTitle: String) = edit {
        personalSource(id)
        val name = sourceTitle(rawTitle)
        if (dao.renameSourceRecord(id, name) != 1) throw SourceMissingException()
    }

    suspend fun deleteSource(id: String) = edit {
        personalSource(id)
        dao.removeSource(id)
        // Derived guide aliases do not own any favorite/history records.
        try { epgIds?.remove(id) } catch (_: IOException) { /* derived metadata only */ }
    }

    private suspend fun personalSource(id: String): SourceEntity {
        if (isBuiltInSource(id)) throw ProtectedSourceException()
        return dao.source(id) ?: throw SourceMissingException()
    }

    private fun readFile(uri: Uri): String = context.contentResolver.openInputStream(uri)?.use { it.readPlaylist() }
        ?: throw IOException("Unable to open the selected file")

    private fun newSourceId() = "user-${UUID.randomUUID()}"

    private suspend fun save(id: String, title: String, url: String, text: String, baseUrl: String? = null) {
        val parsed = M3uParser.parse(text, baseUrl)
        coroutineContext.ensureActive()
        // A monotonic per-source revision prevents a failed/stale sidecar from
        // matching a later catalog, even when refreshes occur in one millisecond.
        val revision = maxOf(System.currentTimeMillis(), (dao.source(id)?.updatedAt ?: 0L) + 1L)
        val source = SourceEntity(id, title, url, revision, parsed.channels.size, parsed.skipped)
        dao.replaceSource(source, parsed.channels)
        // Best-effort derived metadata cannot turn a committed catalog import into
        // a reported failure. On disk errors the original Room TVG IDs still work;
        // a later explicit catalog refresh can recover rewritten guide aliases.
        try { epgIds?.save(source, parsed.channels, parsed.xmltvIds) } catch (_: IOException) { /* recover on explicit refresh */ }
    }
}
