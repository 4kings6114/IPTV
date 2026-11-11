package io.github.fourkings6114.iptv.data

import android.content.Context
import android.net.Uri
import io.github.fourkings6114.iptv.core.M3uParser
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
        dao.replaceSource(
            SourceEntity(id, title, url, System.currentTimeMillis(), parsed.channels.size, parsed.skipped),
            parsed.channels,
        )
    }
}
