package io.github.fourkings6114.iptv.data

import android.app.Application
import android.net.Uri
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import io.github.fourkings6114.iptv.core.M3uParser
import io.github.fourkings6114.iptv.core.PlaylistParseException
import io.github.fourkings6114.iptv.network.CleartextDisabledException
import io.github.fourkings6114.iptv.network.NetworkClients
import io.github.fourkings6114.iptv.network.SourceHttpException
import java.io.File
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class PlaylistRepositoryTest {
    private val context get() = ApplicationProvider.getApplicationContext<Application>()
    private lateinit var database: AppDatabase
    private lateinit var dao: ChannelDao
    private lateinit var client: OkHttpClient
    private lateinit var repository: PlaylistRepository
    private val server = MockWebServer()
    private val allowHttp = AtomicBoolean(true)
    private val files = mutableListOf<File>()
    private val first = playlist("First", "first")
    private val second = playlist("Second", "second")

    @Before fun prepare() {
        database = Room.inMemoryDatabaseBuilder(context, AppDatabase::class.java).build()
        dao = database.channels()
        client = NetworkClients.create({ allowHttp.get() }) // Test-only loopback HTTP opt-in.
        repository = PlaylistRepository(context, dao, client)
        server.start()
    }

    @After fun close() {
        server.shutdown()
        client.connectionPool.evictAll()
        client.dispatcher.executorService.shutdown()
        database.close()
        files.forEach { it.delete() }
    }

    @Test fun repeatedUrlAddsAreIndependentAndDoNotOverwriteLegacyCustom() = runBlocking {
        val legacy = SourceEntity("custom", "Legacy link", server.url("/old").toString(), 123, 1)
        dao.replaceSource(legacy, M3uParser.parse(first).channels)
        server.enqueue(MockResponse().setBody(first))
        server.enqueue(MockResponse().setBody(second))
        val one = repository.importUrl(server.url("/same").toString(), " لینک اول ")
        val two = repository.importUrl(server.url("/same").toString(), "لینک دوم")
        assertNotEquals(one, two)
        assertTrue(one.startsWith("user-"))
        assertTrue(two.startsWith("user-"))
        assertEquals(legacy, dao.source("custom"))
        assertEquals("لینک اول", dao.source(one)!!.title)
        assertEquals("لینک دوم", dao.source(two)!!.title)
        assertEquals("First", dao.observeSource(one).first().single().name)
        assertEquals("Second", dao.observeSource(two).first().single().name)
        assertEquals(3, dao.observeSources().first().size)
    }

    @Test fun repeatedFileAddsAreIndependentAndDoNotOverwriteLegacyFile() = runBlocking {
        val legacy = SourceEntity("file", "Legacy file", updatedAt = 123, count = 1)
        dao.replaceSource(legacy, M3uParser.parse(first).channels)
        val file = file(first)
        val one = repository.importFile(file, "فایل اول")
        val two = repository.importFile(file, "فایل دوم")
        assertNotEquals(one, two)
        assertEquals(legacy, dao.source("file"))
        assertTrue(dao.source(one)!!.isFileSource)
        assertTrue(dao.source(two)!!.isFileSource)
        assertEquals(3, dao.observeSources().first().size)
    }

    @Test fun targetedFileRefreshRetainsIdentityNameAndSavedChannels() = runBlocking {
        val one = repository.importFile(file(first), "اول")
        val two = repository.importFile(file(second), "دوم")
        val original = dao.observeSource(one).first().single()
        dao.toggleFavorite(original.id)
        dao.recordPlayback(original.id)
        assertEquals(one, repository.replaceFile(one, file(playlist("Replacement", "replacement"))))
        assertEquals("اول", dao.source(one)!!.title)
        assertEquals("Replacement", dao.observeSource(one).first().single().name)
        assertEquals("Second", dao.observeSource(two).first().single().name)
        assertEquals(listOf(original.id), dao.observeFavorites().first().map { it.id })
        assertEquals(listOf(original.id), dao.observeHistory().first().map { it.id })
        assertEquals(2, dao.observeSources().first().size)
    }

    @Test fun legacyFileCanBeRenamedAndReimportedWithoutChangingItsId() = runBlocking {
        dao.replaceSource(SourceEntity("file", "Legacy file", updatedAt = 1, count = 1), M3uParser.parse(first).channels)
        repository.renameSource("file", "نام جدید")
        repository.replaceFile("file", file(second))
        assertEquals("نام جدید", dao.source("file")!!.title)
        assertEquals("Second", dao.observeSource("file").first().single().name)
        assertEquals(listOf("file"), dao.observeSources().first().map { it.id })
    }

    @Test fun fileRefreshRequiresExplicitReselectionRatherThanCreatingAnotherSource() = runBlocking {
        val id = repository.importFile(file(first), "فایل")
        val before = dao.source(id)
        expect<FileSourceSelectionRequiredException> { repository.refresh(id) }
        assertEquals(before, dao.source(id))
        assertEquals(1, dao.observeSources().first().size)
        assertEquals(0, server.requestCount)
    }

    @Test fun refreshUsesStoredUrlAndPreservesRenamedTitleForLegacyCustom() = runBlocking {
        val url = server.url("/legacy.m3u").toString()
        dao.replaceSource(SourceEntity("custom", "Old", url, 1, 1), M3uParser.parse(first).channels)
        repository.renameSource("custom", "Renamed")
        server.enqueue(MockResponse().setBody(second))
        repository.refresh("custom")
        assertEquals("/legacy.m3u", server.takeRequest(1, TimeUnit.SECONDS)!!.path)
        assertEquals("Renamed", dao.source("custom")!!.title)
        assertEquals(url, dao.source("custom")!!.url)
        assertEquals("Second", dao.observeSource("custom").first().single().name)
    }

    @Test fun failedUrlRefreshPreservesLastGoodMetadataReferencesFavoritesAndHistory() = runBlocking {
        server.enqueue(MockResponse().setBody(first))
        val id = repository.importUrl(server.url("/list").toString(), "Saved")
        val before = dao.source(id)
        val channel = dao.observeSource(id).first().single()
        dao.toggleFavorite(channel.id)
        dao.recordPlayback(channel.id)
        server.enqueue(MockResponse().setResponseCode(503))
        expect<SourceHttpException> { repository.refresh(id) }
        server.enqueue(MockResponse().setBody("#EXTM3U\n"))
        expect<PlaylistParseException> { repository.refresh(id) }
        assertEquals(before, dao.source(id))
        assertEquals(listOf(channel), dao.observeSource(id).first())
        assertEquals(listOf(channel), dao.observeFavorites().first())
        assertEquals(listOf(channel), dao.observeHistory().first())
    }

    @Test fun failedFileReplacementAndFailedAddsDoNotMutateTheCatalog() = runBlocking {
        val id = repository.importFile(file(first), "Saved file")
        val before = dao.source(id)
        expect<PlaylistParseException> { repository.replaceFile(id, file("#EXTM3U\n")) }
        expect<PlaylistParseException> { repository.importFile(file("#EXT-X-TARGETDURATION:4\n"), "Not a catalog") }
        server.enqueue(MockResponse().setResponseCode(403))
        expect<SourceHttpException> { repository.importUrl(server.url("/denied").toString(), "Denied") }
        assertEquals(before, dao.source(id))
        assertEquals(listOf(before), dao.observeSources().first())
        assertEquals("First", dao.observeSource(id).first().single().name)
    }

    @Test fun invalidNamesAndUrlsAreRejectedBeforeNetworkOrDatabaseWrites() = runBlocking {
        expect<InvalidSourceNameException> { repository.importUrl(server.url("/list").toString(), "  ") }
        expect<InvalidSourceNameException> { repository.importFile(file(first), "x".repeat(81)) }
        expect<IllegalArgumentException> { repository.importUrl("https://user:password@example.invalid/list", "Name") }
        assertTrue(dao.observeSources().first().isEmpty())
        assertEquals(0, server.requestCount)
    }

    @Test fun httpOptInStillAppliesToSourceImports() = runBlocking {
        allowHttp.set(false)
        expect<CleartextDisabledException> { repository.importUrl(server.url("/list").toString(), "Blocked") }
        assertTrue(dao.observeSources().first().isEmpty())
        assertEquals(0, server.requestCount)
    }

    @Test fun builtInAndMissingSourcesCannotBeRenamedDeletedOrReplacedByFiles() = runBlocking {
        val uri = file(first)
        expect<ProtectedSourceException> { repository.renameSource(SAMPLE_SOURCE_ID, "Name") }
        expect<ProtectedSourceException> { repository.deleteSource("persian") }
        expect<ProtectedSourceException> { repository.replaceFile(SAMPLE_SOURCE_ID, uri) }
        expect<SourceMissingException> { repository.renameSource("missing", "Name") }
        expect<SourceMissingException> { repository.deleteSource("missing") }
        expect<SourceMissingException> { repository.refresh("missing") }
        expect<SourceMissingException> { repository.replaceFile("missing", uri) }
        server.enqueue(MockResponse().setBody(first))
        val link = repository.importUrl(server.url("/list").toString(), "Link")
        expect<NotAFileSourceException> { repository.replaceFile(link, uri) }
        assertEquals("First", dao.observeSource(link).first().single().name)
    }

    @Test fun deletionPreservesOtherSourcesSharedChannelsFavoritesAndHistory() = runBlocking {
        val shared = M3uParser.parse(playlist("Shared", "shared")).channels.single()
        val favorite = M3uParser.parse(first).channels.single()
        val historic = M3uParser.parse(second).channels.single()
        val orphan = M3uParser.parse(playlist("Unused", "orphan")).channels.single()
        dao.replaceSource(SourceEntity("custom", "Legacy", "https://example.invalid/list", 1, 4), listOf(shared, favorite, historic, orphan))
        dao.replaceSource(SourceEntity("file", "Other", updatedAt = 2, count = 1), listOf(shared))
        dao.toggleFavorite(favorite.id)
        dao.recordPlayback(historic.id)
        repository.deleteSource("custom")
        assertNull(dao.source("custom"))
        assertTrue(dao.observeSource("custom").first().isEmpty())
        assertEquals(listOf(shared.id), dao.observeSource("file").first().map { it.id })
        assertEquals(listOf(favorite.id), dao.observeFavorites().first().map { it.id })
        assertEquals(listOf(historic.id), dao.observeHistory().first().map { it.id })
        assertNull(dao.channel(orphan.id))
    }

    @Test fun cancelledRefreshKeepsTheLastKnownGoodSource() = runBlocking {
        server.enqueue(MockResponse().setBody(first))
        val id = repository.importUrl(server.url("/list").toString(), "Saved")
        val before = dao.source(id)
        server.takeRequest(1, TimeUnit.SECONDS)
        server.enqueue(MockResponse().setBody(second).setHeadersDelay(2, TimeUnit.SECONDS))
        val refresh = async(Dispatchers.IO) { repository.refresh(id) }
        assertNotNull(server.takeRequest(5, TimeUnit.SECONDS))
        refresh.cancelAndJoin()
        assertEquals(before, dao.source(id))
        assertEquals("First", dao.observeSource(id).first().single().name)
    }

    @Test fun deletingTheLastPersonalSourceDoesNotDeleteSavedChannels() = runBlocking {
        val id = repository.importFile(file(first), "Only catalog")
        val saved = dao.observeSource(id).first().single()
        dao.toggleFavorite(saved.id)
        dao.recordPlayback(saved.id)
        repository.deleteSource(id)
        assertTrue(dao.observeSources().first().isEmpty())
        assertEquals(listOf(saved), dao.observeFavorites().first())
        assertEquals(listOf(saved), dao.observeHistory().first())
    }

    @Test fun refreshAndDeleteAreSerializedSoACompletedDeleteCannotBeResurrected() = runBlocking {
        server.enqueue(MockResponse().setBody(first))
        val id = repository.importUrl(server.url("/list").toString(), "Name")
        server.takeRequest(1, TimeUnit.SECONDS)
        server.enqueue(MockResponse().setBody(second).setBodyDelay(200, TimeUnit.MILLISECONDS))
        val refresh = async(Dispatchers.IO) { repository.refresh(id) }
        assertNotNull(server.takeRequest(5, TimeUnit.SECONDS))
        val delete = async(start = CoroutineStart.UNDISPATCHED) { repository.deleteSource(id) }
        refresh.await()
        delete.await()
        assertNull(dao.source(id))
        assertTrue(dao.observeSource(id).first().isEmpty())
        expect<SourceMissingException> { repository.refresh(id) }
    }

    private fun file(text: String): Uri {
        val file = File.createTempFile("catalog-", ".m3u", context.cacheDir).also { it.writeText(text); files += it }
        return Uri.fromFile(file)
    }

    private suspend inline fun <reified T : Exception> expect(action: () -> Unit) {
        try { action(); fail("Expected ${T::class.java.simpleName}") }
        catch (failure: Exception) { if (failure !is T) throw failure }
    }

    private fun playlist(name: String, path: String) = "#EXTM3U\n#EXTINF:-1 group-title=\"Test\",$name\nhttps://example.invalid/$path.m3u8\n"
}
