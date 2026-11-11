package io.github.fourkings6114.iptv.epg

import android.app.Application
import android.net.Uri
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import io.github.fourkings6114.iptv.core.M3uParser
import io.github.fourkings6114.iptv.data.AppDatabase
import io.github.fourkings6114.iptv.data.ChannelDao
import io.github.fourkings6114.iptv.data.PlaylistRepository
import io.github.fourkings6114.iptv.data.SettingsStore
import io.github.fourkings6114.iptv.data.SourceEntity
import io.github.fourkings6114.iptv.data.SourceMissingException
import io.github.fourkings6114.iptv.network.NetworkClients
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class EpgRepositoryTest {
    @get:Rule val temporary = TemporaryFolder()
    private val context get() = ApplicationProvider.getApplicationContext<Application>()
    private lateinit var database: AppDatabase
    private lateinit var dao: ChannelDao
    private lateinit var scope: CoroutineScope
    private lateinit var settings: SettingsStore
    private lateinit var cache: EpgCache
    private lateinit var ids: EpgChannelIds
    private lateinit var client: OkHttpClient
    private lateinit var repository: EpgRepository
    private val server = MockWebServer()
    private val url get() = server.url("/guide.xml?token=LOCAL_ONLY").toString()
    private val text = "#EXTM3U\n#EXTINF:-1 tvg-id=Exact.ID x-original-tvg-id=Legacy.ID,Shared\n#EXTHTTP:{\"Authorization\":\"Bearer MEDIA_ONLY\",\"Cookie\":\"MEDIA_ONLY=1\"}\nhttps://example.invalid/live\n"

    @Before fun prepare() = runBlocking {
        database = Room.inMemoryDatabaseBuilder(context, AppDatabase::class.java).build()
        dao = database.channels()
        scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        settings = SettingsStore(context, scope)
        settings.state.first { it.loaded }
        cache = EpgCache(temporary.newFolder())
        ids = EpgChannelIds(temporary.newFolder())
        client = NetworkClients.create({ true }) // Synthetic loopback sources only.
        repository = EpgRepository(cache, settings, dao, client, ids, clock = { GUIDE_NOW })
        server.start()
    }

    @After fun close() {
        scope.cancel()
        server.shutdown()
        client.connectionPool.evictAll()
        client.dispatcher.executorService.shutdown()
        database.close()
    }

    private suspend fun seed(id: String = "one", body: String = text) {
        val parsed = M3uParser.parse(body)
        val source = SourceEntity(id, "Source $id", updatedAt = 1234, count = parsed.channels.size)
        dao.replaceSource(source, parsed.channels)
        ids.save(source, parsed.channels, parsed.xmltvIds)
    }

    private suspend fun prime(): EpgGuide {
        seed()
        repository.configure("one", url)
        server.enqueue(MockResponse().setBody(guideXml()))
        return repository.refresh("one").also { server.takeRequest(5, TimeUnit.SECONDS) }
    }

    @Test fun configuringPerCatalogGuideUrlsIsPrivatePersistentAndNeverAutoDownloads() = runBlocking {
        seed(); seed("two")
        settings.automaticPictureInPicture(true)
        settings.showLogos(false)
        settings.allowHttp(false)
        repository.configure("one", url)
        repository.configure("two", url + "two")
        settings.selectSource("one")
        assertEquals(url, settings.state.first { it.sourceId == "one" && it.epgUrl == url }.epgUrl)
        settings.selectSource("two")
        assertEquals(url + "two", settings.state.first { it.sourceId == "two" && it.epgUrl == url + "two" }.epgUrl)
        val reopened = SettingsStore(context, scope).state.first { it.loaded }
        assertTrue(reopened.autoPictureInPicture)
        assertFalse(reopened.showLogos)
        assertFalse(reopened.allowHttp)
        assertNull(repository.observe("two", reopened.epgUrl).first().guide)
        assertEquals(0, server.requestCount)
    }

    @Test fun failedGuideRefreshPreservesGoodCacheCatalogFavoritesHistoryAndPreferences() = runBlocking {
        val previous = prime()
        val channel = dao.observeSource("one").first().single()
        dao.toggleFavorite(channel.id); dao.recordPlayback(channel.id)
        val source = dao.source("one")
        val responses = listOf(MockResponse().setResponseCode(404), MockResponse().setBody("<tv><private>broken"),
            MockResponse().setBody(guideXml("Wrong.ID")), MockResponse().setBody("not XML"))
        for (response in responses) {
            server.enqueue(response)
            expectEpgFailure<IOException> { repository.refresh("one") }
            assertEquals(previous.programmes, repository.cached("one", url)!!.programmes)
            assertEquals(source, dao.source("one"))
            assertTrue(dao.isFavorite(channel.id))
            assertEquals(channel.id, dao.observeHistory().first().single().id)
            assertEquals(url, settings.epgUrlFor("one"))
        }
    }

    @Test fun cancelledGuideBodyCannotReplaceTheLastKnownGoodCache() = runBlocking {
        val previous = prime()
        server.enqueue(MockResponse().setBody(guideXml(title = "Must not replace")).setBodyDelay(2, TimeUnit.SECONDS))
        val download = async(Dispatchers.IO) { repository.refresh("one") }
        assertNotNull(server.takeRequest(5, TimeUnit.SECONDS))
        withTimeout(5000) { download.cancelAndJoin() }
        assertEquals(previous.programmes, repository.cached("one", url)!!.programmes)
    }

    private suspend fun blockedRefresh(action: suspend () -> Unit): Result<EpgGuide> = coroutineScope {
        val received = CountDownLatch(1)
        val release = CountDownLatch(1)
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                received.countDown()
                if (!release.await(10, TimeUnit.SECONDS)) return MockResponse().setResponseCode(500)
                return MockResponse().setBody(guideXml(title = "Must not replace"))
            }
        }
        val pending = async(Dispatchers.IO) { runCatching { repository.refresh("one") } }
        try { assertTrue(received.await(5, TimeUnit.SECONDS)); action() } finally { release.countDown() }
        withTimeout(10_000) { pending.await() }
    }

    @Test fun lateGuideResponseCannotRestoreAnOldUrlAfterAnAToBToAConfigurationChange() = runBlocking {
        prime()
        val result = blockedRefresh { repository.configure("one", url + "other"); repository.configure("one", url) }
        assertEquals(EpgException.Reason.SOURCE_CHANGED, (result.exceptionOrNull() as EpgException).reason)
        assertNull(repository.cached("one", url)) // Changing feeds deliberately invalidates old cache.
        assertEquals(url, settings.epgUrlFor("one"))
    }

    @Test fun lateGuideResponseCannotResurrectADeletedCatalog() = runBlocking {
        prime()
        val saved = dao.observeSource("one").first().single()
        dao.toggleFavorite(saved.id); dao.recordPlayback(saved.id)
        val result = blockedRefresh { dao.removeSource("one"); repository.remove("one") }
        assertEquals(EpgException.Reason.SOURCE_CHANGED, (result.exceptionOrNull() as EpgException).reason)
        assertNull(dao.source("one"))
        assertNull(repository.cached("one", url))
        assertEquals("", settings.epgUrlFor("one"))
        assertTrue(dao.isFavorite(saved.id))
        assertEquals(saved.id, dao.observeHistory().first().single().id)
    }

    @Test fun catalogRefreshDuringGuideDownloadDiscardsTheOldCatalogResult() = runBlocking {
        val previous = prime()
        val result = blockedRefresh {
            val source = dao.source("one")!!
            dao.replaceSource(source.copy(updatedAt = source.updatedAt + 1), M3uParser.parse(text).channels)
        }
        assertEquals(EpgException.Reason.SOURCE_CHANGED, (result.exceptionOrNull() as EpgException).reason)
        assertEquals(previous.programmes, repository.cached("one", url)!!.programmes)
    }

    @Test fun rewrittenM3uGuideIdsPersistWithoutChangingFavoritesOrLeakingMediaHeaders() = runBlocking {
        val file = temporary.newFile("source.m3u").also { it.writeText(text) }
        val playlistRepository = PlaylistRepository(context, dao, client, ids)
        val sourceId = playlistRepository.importFile(Uri.fromFile(file), "Aliased catalog")
        val channel = dao.observeSource(sourceId).first().single().channel()
        assertEquals("Legacy.ID", channel.tvgId)
        dao.toggleFavorite(channel.id); dao.recordPlayback(channel.id)
        repository.configure(sourceId, url)
        server.enqueue(MockResponse().setBody(guideXml()))
        repository.refresh(sourceId)
        val request = server.takeRequest(5, TimeUnit.SECONDS)!!
        assertNull(request.getHeader("Authorization"))
        assertNull(request.getHeader("Cookie"))
        val snapshot = withTimeout(5000) { repository.observe(sourceId, url).first { it.guide != null } }
        assertEquals("Exact.ID", snapshot.idsByChannel[channel.id])
        assertNotNull(snapshot.guide!!.at(snapshot.idsByChannel[channel.id]!!, GUIDE_NOW).current)
        assertEquals(channel, dao.channel(channel.id)!!.channel())
        assertTrue(dao.isFavorite(channel.id))
        assertEquals(channel.id, dao.observeHistory().first().single().id)
        assertEquals(1, server.requestCount)
    }

    @Test fun sameSharedChannelCanUseIndependentXmltvIdsInTwoCatalogs() = runBlocking {
        seed(); seed("two", text.replace("Exact.ID", "Second.ID"))
        repository.configure("one", url); repository.configure("two", url + "two")
        server.enqueue(MockResponse().setBody(guideXml()))
        server.enqueue(MockResponse().setBody(guideXml("Second.ID", "Second current")))
        repository.refresh("one"); repository.refresh("two")
        val first = withTimeout(5000) { repository.observe("one", url).first { it.guide != null } }
        val second = withTimeout(5000) { repository.observe("two", url + "two").first { it.guide != null } }
        assertEquals(first.idsByChannel.keys, second.idsByChannel.keys)
        assertEquals(setOf("Exact.ID"), first.idsByChannel.values.toSet())
        assertEquals(setOf("Second.ID"), second.idsByChannel.values.toSet())
        assertEquals("Current programme", first.guide!!.programmes.first().title)
        assertEquals("Second current", second.guide!!.programmes.first().title)
        repository.configure("one", "")
        assertNull(repository.cached("one", url))
        assertNotNull(repository.cached("two", url + "two"))
    }

    @Test fun missingConfigIdsOrDeletedSourceNeverStartAnUnusableDownload() = runBlocking {
        seed()
        assertEquals(EpgException.Reason.NOT_CONFIGURED, expectEpgFailure<EpgException> { repository.refresh("one") }.reason)
        expectEpgFailure<IllegalArgumentException> { repository.configure("one", "https://user:password@example.invalid/guide") }
        expectEpgFailure<SourceMissingException> { repository.configure("missing", url) }
        seed("empty", "#EXTM3U\n#EXTINF:-1,No ID\nhttps://example.invalid/no-id\n")
        repository.configure("empty", url)
        assertEquals(EpgException.Reason.NO_IDS, expectEpgFailure<EpgException> { repository.refresh("empty") }.reason)
        assertEquals(0, server.requestCount)
    }
}
