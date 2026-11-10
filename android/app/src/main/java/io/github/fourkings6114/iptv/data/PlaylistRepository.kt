package io.github.fourkings6114.iptv.data

import android.content.Context
import android.net.Uri
import io.github.fourkings6114.iptv.core.M3uParser
import io.github.fourkings6114.iptv.network.downloadPlaylist
import io.github.fourkings6114.iptv.network.readPlaylist
import java.io.IOException
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
    private val refreshLock = Mutex()

    suspend fun ensureSource(id: String) {
        if (dao.source(id) == null) refresh(id)
    }

    suspend fun refresh(id: String) = refreshLock.withLock {
        withContext(Dispatchers.IO) {
            if (id == SAMPLE_SOURCE_ID) {
                val text = context.assets.open("trial.m3u").use { it.readPlaylist() }
                save(id, SOURCE_PRESETS.first().title, "", text)
            } else {
                val preset = SOURCE_PRESETS.find { it.id == id }
                val stored = dao.source(id)
                val url = preset?.url ?: stored?.url?.takeIf { it.isNotBlank() }
                    ?: throw IOException("No refresh URL")
                val downloaded = client.downloadPlaylist(url)
                coroutineContext.ensureActive()
                save(id, preset?.title ?: stored!!.title, url, downloaded.text, downloaded.finalUrl)
            }
        }
    }

    suspend fun importUrl(raw: String) = refreshLock.withLock {
        withContext(Dispatchers.IO) {
            val url = M3uParser.httpUrl(raw.trim()) ?: throw IllegalArgumentException("Invalid HTTP(S) playlist URL")
            val downloaded = client.downloadPlaylist(url)
            coroutineContext.ensureActive()
            save("custom", "فهرست از لینک شخصی", url, downloaded.text, downloaded.finalUrl)
        }
    }

    suspend fun importFile(uri: Uri) = refreshLock.withLock {
        withContext(Dispatchers.IO) {
            val text = context.contentResolver.openInputStream(uri)?.use { it.readPlaylist() }
                ?: throw IOException("Unable to open the selected file")
            coroutineContext.ensureActive()
            save("file", "فایل واردشده", "", text)
        }
    }

    private suspend fun save(id: String, title: String, url: String, text: String, baseUrl: String? = null) {
        val parsed = M3uParser.parse(text, baseUrl)
        coroutineContext.ensureActive()
        dao.replaceSource(
            SourceEntity(id, title, url, System.currentTimeMillis(), parsed.channels.size, parsed.skipped),
            parsed.channels,
        )
    }
}
