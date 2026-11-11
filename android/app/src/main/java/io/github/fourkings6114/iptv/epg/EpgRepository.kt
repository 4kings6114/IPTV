package io.github.fourkings6114.iptv.epg

import io.github.fourkings6114.iptv.core.sha256
import io.github.fourkings6114.iptv.data.ChannelDao
import io.github.fourkings6114.iptv.data.SettingsStore
import io.github.fourkings6114.iptv.data.SourceMissingException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.mapLatest
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient

/** No polling/Worker/service: network work is only the caller's explicit refresh.
 * Short IO commits are serialized; a slow download never holds the cache lock.
 * Generation + persisted configuration/catalog checks prevent a late request
 * from resurrecting a deleted/reconfigured guide, including URL A -> B -> A.
 */
@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class EpgRepository(
    private val cache: EpgCache,
    private val settings: SettingsStore,
    private val dao: ChannelDao,
    private val client: OkHttpClient,
    private val channelIds: EpgChannelIds,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val gate = Mutex()
    private val generations = mutableMapOf<String, Long>()
    private val changes = MutableStateFlow(0L)
    private fun advance(id: String): Long = ((generations[id] ?: 0L) + 1).also { generations[id] = it }

    fun observe(id: String, url: String) = combine(changes, channelIds.revisions,
        dao.observeSourceInfo(id), dao.observeSource(id)) { _, _, source, rows -> source to rows }
        .mapLatest { (source, rows) ->
            if (source == null || url.isBlank()) EpgSnapshot(id, sha256(url))
            else withContext(Dispatchers.IO) {
                EpgSnapshot(id, sha256(url), cached(id, url), channelIds.forCatalog(source, rows.map { it.channel() }))
            }
        }

    suspend fun cached(id: String, url: String): EpgGuide? = withContext(Dispatchers.IO) {
        gate.withLock { cache.read(id, url, clock()) }
    }

    suspend fun configure(id: String, rawUrl: String) {
        val url = if (rawUrl.isBlank()) "" else normalizeEpgUrl(rawUrl)
        withContext(Dispatchers.IO) {
            gate.withLock {
                if (dao.source(id) == null) throw SourceMissingException()
                advance(id)
                val old = settings.epgUrlFor(id)
                settings.epgUrl(id, url)
                if (old != url || url.isBlank()) cache.remove(id)
                changes.value++
            }
        }
    }

    /** Also usable after Room deletion; never touches channel/favorite/history records. */
    suspend fun remove(id: String) = withContext(Dispatchers.IO) {
        gate.withLock {
            advance(id)
            settings.epgUrl(id, "")
            cache.remove(id)
            changes.value++
        }
    }

    private data class Fetch(val url: String, val generation: Long, val ids: Set<String>, val catalogRevision: Long)

    suspend fun refresh(id: String): EpgGuide {
        val request = withContext(Dispatchers.IO) {
            gate.withLock {
                val source = dao.source(id) ?: throw SourceMissingException()
                val url = settings.epgUrlFor(id)
                if (url.isBlank()) throw EpgException(EpgException.Reason.NOT_CONFIGURED)
                val rows = dao.observeSource(id).first().map { it.channel() }
                val ids = channelIds.forCatalog(source, rows).values.filter { it.isNotBlank() }.toSet()
                if (ids.isEmpty()) throw EpgException(EpgException.Reason.NO_IDS)
                Fetch(url, advance(id), ids, source.updatedAt)
            }
        }
        val guide = client.downloadEpg(request.url, request.ids, clock())
        withContext(Dispatchers.IO) {
            gate.withLock {
                val context = currentCoroutineContext()
                context.ensureActive()
                if (generations[id] != request.generation || settings.epgUrlFor(id) != request.url ||
                    dao.source(id)?.updatedAt != request.catalogRevision)
                    throw EpgException(EpgException.Reason.SOURCE_CHANGED)
                cache.write(id, request.url, guide) { context.ensureActive() }
                changes.value++
            }
        }
        return guide
    }
}
