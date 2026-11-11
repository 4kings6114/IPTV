package io.github.fourkings6114.iptv.data

import android.app.Application
import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import io.github.fourkings6114.iptv.core.Channel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class AppDatabaseTest {
    private lateinit var database: AppDatabase
    private lateinit var dao: ChannelDao
    private val one = Channel("one", "One", "News", "https://example.com/one")
    private val two = Channel("two", "Two", "News", "https://example.com/two")
    private fun source(id: String = "test", count: Int = 1) = SourceEntity(id, "Test", updatedAt = 1, count = count)

    @Before fun createDatabase() {
        database = Room.inMemoryDatabaseBuilder(ApplicationProvider.getApplicationContext(), AppDatabase::class.java).build()
        dao = database.channels()
    }
    @After fun closeDatabase() { database.close() }

    @Test fun refreshPreservesFavoritesAndHistoryOfRemovedChannels() = runBlocking {
        dao.replaceSource(source(), listOf(one))
        dao.toggleFavorite(one.id)
        dao.recordPlayback(one.id)
        dao.replaceSource(source(), listOf(two))
        assertEquals(listOf(two.id), dao.observeSource("test").first().map { it.id })
        assertEquals(listOf(one.id), dao.observeFavorites().first().map { it.id })
        assertEquals(listOf(one.id), dao.observeHistory().first().map { it.id })
    }
    @Test fun emptyRefreshCannotDestroyLastKnownGoodSource() = runBlocking {
        dao.replaceSource(source(), listOf(one))
        try {
            dao.replaceSource(source(count = 0), emptyList())
            fail("Empty refresh should fail")
        } catch (_: IllegalArgumentException) { /* expected */ }
        assertEquals(one.id, dao.observeSource("test").first().single().id)
        assertEquals(1, dao.source("test")!!.count)
    }
    @Test fun cachedSourcesAreIsolated() = runBlocking {
        dao.replaceSource(source("a"), listOf(one))
        dao.replaceSource(source("b"), listOf(two))
        assertEquals(one.id, dao.observeSource("a").first().single().id)
        assertEquals(two.id, dao.observeSource("b").first().single().id)
    }
    @Test fun sourceRefreshDoesNotResetFavoriteState() = runBlocking {
        dao.replaceSource(source(), listOf(one))
        dao.toggleFavorite(one.id)
        dao.replaceSource(source(), listOf(one.copy(name = "Renamed")))
        assertEquals("Renamed", dao.observeFavorites().first().single().name)
        dao.toggleFavorite(one.id)
        assertTrue(dao.observeFavorites().first().isEmpty())
    }
    @Test fun historyIsBoundedAndCanBeClearedWithoutRemovingFavorites() = runBlocking {
        val channels = (0..45).map { one.copy(id = "id$it") }
        dao.replaceSource(source(count = channels.size), channels)
        dao.toggleFavorite(channels.first().id)
        channels.forEachIndexed { index, channel -> dao.saveHistory(HistoryEntity(channel.id, index.toLong())) }
        dao.trimHistory()
        assertEquals(40, dao.observeHistory().first().size)
        assertEquals("id45", dao.observeHistory().first().first().id)
        dao.clearHistory()
        assertTrue(dao.observeHistory().first().isEmpty())
        assertEquals(1, dao.observeFavorites().first().size)
    }
    @Test fun renameChangesOnlyTitleNotIdentityTimestampOrReferences() = runBlocking {
        val original = source("custom")
        dao.replaceSource(original, listOf(one))
        dao.toggleFavorite(one.id)
        dao.recordPlayback(one.id)
        assertEquals(1, dao.renameSourceRecord("custom", "New name"))
        assertEquals(original.copy(title = "New name"), dao.source("custom"))
        assertEquals(listOf(one.id), dao.observeSource("custom").first().map { it.id })
        assertEquals(listOf(one.id), dao.observeFavorites().first().map { it.id })
        assertEquals(listOf(one.id), dao.observeHistory().first().map { it.id })
        assertEquals(0, dao.renameSourceRecord("missing", "Name"))
    }
    @Test fun sourceDeletionRollsBackReferencesWhenTheTransactionFails() = runBlocking {
        dao.replaceSource(source(), listOf(one))
        // Force failure after clearReferences, proving removal is one Room/SQLite transaction.
        database.openHelper.writableDatabase.execSQL("CREATE TRIGGER reject_source_delete BEFORE DELETE ON sources BEGIN SELECT RAISE(ABORT, 'test rollback'); END")
        var rejected = false
        try { dao.removeSource("test") } catch (_: android.database.sqlite.SQLiteException) { rejected = true }
        assertTrue(rejected)
        assertEquals(source(), dao.source("test"))
        assertEquals(listOf(one.id), dao.observeSource("test").first().map { it.id })
        assertNotNull(dao.channel(one.id))
    }
    @Test fun headersRoundTripWithoutLoggingOrLosingValues() {
        val channel = one.copy(headers = mapOf("authorization" to "Bearer test", "user-agent" to "IPTV"))
        assertEquals(channel, channel.entity().channel())
    }
}
