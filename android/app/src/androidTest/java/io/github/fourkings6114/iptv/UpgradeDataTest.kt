package io.github.fourkings6114.iptv

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import io.github.fourkings6114.iptv.core.M3uParser
import io.github.fourkings6114.iptv.data.SourceEntity
import io.github.fourkings6114.iptv.data.isFileSource
import java.io.File
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

private const val LEGACY_LINK = "#EXTM3U\n#EXTINF:-1,Legacy saved favorite\nhttps://example.invalid/upgrade-favorite.m3u8\n"
private const val LEGACY_FILE = "#EXTM3U\n#EXTINF:-1,Legacy saved history\nhttps://example.invalid/upgrade-history.m3u8\n"
private const val LEGACY_LINK_URL = "https://example.invalid/upgrade-list.m3u"
private const val UPGRADE_SENTINEL = "source-upgrade-test.txt"

/** Run ONLY against a source-rebuilt 0.1.2 app, before adb install -r of the new APK. */
@RunWith(AndroidJUnit4::class)
class UpgradeSeedTest {
    @Test fun seedLegacy012DataBeforeInPlaceUpdate() = runBlocking {
        val app = ApplicationProvider.getApplicationContext<IPTVApplication>()
        assertEquals(3L, app.packageManager.getPackageInfo(app.packageName, 0).longVersionCode)
        withTimeout(20_000) {
            val dao = app.database.channels()
            val link = M3uParser.parse(LEGACY_LINK).channels
            val file = M3uParser.parse(LEGACY_FILE).channels
            // These methods/classes existed unchanged in the actual 0.1.2 source.
            dao.replaceSource(SourceEntity("custom", "Legacy URL title", LEGACY_LINK_URL, 1234, 1), link)
            dao.replaceSource(SourceEntity("file", "Legacy file title", updatedAt = 5678, count = 1), file)
            dao.toggleFavorite(link.single().id)
            dao.recordPlayback(file.single().id)
            app.settings.allowHttp(false)
            app.settings.showLogos(false)
            app.settings.selectSource("custom")
            app.settings.state.first { it.loaded && it.sourceId == "custom" && !it.allowHttp && !it.showLogos }
            File(app.filesDir, UPGRADE_SENTINEL).writeText("written by versionCode 3 before package update")
        }
    }
}

/** Run in a fresh target process ONLY after adb install -r, without uninstall/clear-data. */
@RunWith(AndroidJUnit4::class)
class UpgradePreservationTest {
    @Test fun legacy012SourcesSelectionFavoritesAndHistorySurviveInPlaceUpdate() = runBlocking {
        val app = ApplicationProvider.getApplicationContext<IPTVApplication>()
        assertEquals(5L, app.packageManager.getPackageInfo(app.packageName, 0).longVersionCode)
        withTimeout(20_000) {
            val dao = app.database.channels()
            assertEquals(1, app.database.openHelper.writableDatabase.version)
            assertEquals(SourceEntity("custom", "Legacy URL title", LEGACY_LINK_URL, 1234, 1), dao.source("custom"))
            assertEquals(SourceEntity("file", "Legacy file title", updatedAt = 5678, count = 1), dao.source("file"))
            assertFalse(dao.source("custom")!!.isFileSource)
            assertTrue(dao.source("file")!!.isFileSource)
            assertEquals(M3uParser.parse(LEGACY_LINK).channels, dao.observeSource("custom").first().map { it.channel() })
            assertEquals(M3uParser.parse(LEGACY_FILE).channels, dao.observeSource("file").first().map { it.channel() })
            assertTrue(dao.isFavorite(M3uParser.parse(LEGACY_LINK).channels.single().id))
            assertEquals(M3uParser.parse(LEGACY_FILE).channels.single().id, dao.observeHistory().first().single().id)
            val settings = app.settings.state.first { it.loaded }
            assertEquals("custom", settings.sourceId)
            assertFalse(settings.allowHttp)
            assertFalse(settings.showLogos)
            assertFalse("Existing installations must not silently opt into Home-to-PiP", settings.autoPictureInPicture)
            assertEquals("written by versionCode 3 before package update", File(app.filesDir, UPGRADE_SENTINEL).readText())
            assertNull(app.crashReports.read())
        }
    }
}


private val NAMED_013_SOURCES = listOf(
    SourceEntity("user-11111111-1111-4111-8111-111111111111", "فهرست لینک اول", "https://example.invalid/one.m3u", 1001, 2),
    SourceEntity("user-22222222-2222-4222-8222-222222222222", "فهرست لینک دوم", "https://example.invalid/two.m3u", 1002, 2),
    SourceEntity("user-33333333-3333-4333-8333-333333333333", "فهرست فایل اول", updatedAt = 1003, count = 2),
    SourceEntity("user-44444444-4444-4444-8444-444444444444", "فهرست فایل دوم با نام تازه", updatedAt = 1004, count = 2),
)

private fun named013Channels(index: Int) = M3uParser.parse(
    "#EXTM3U\n#EXTINF:-1,Shared saved channel\nhttps://example.invalid/named-shared.mp4\n" +
        "#EXTINF:-1,Independent saved channel $index\nhttps://example.invalid/named-$index.mp4\n"
).channels

/** Separate mandatory phase against the immutable multi-catalog 0.1.3 source. */
@RunWith(AndroidJUnit4::class)
class CatalogUpgradeSeedTest {
    @Test fun seedNamed013CatalogsBeforeInPlaceUpdate() = runBlocking {
        val app = ApplicationProvider.getApplicationContext<IPTVApplication>()
        assertEquals(4L, app.packageManager.getPackageInfo(app.packageName, 0).longVersionCode)
        withTimeout(20_000) {
            val dao = app.database.channels()
            NAMED_013_SOURCES.forEachIndexed { index, source -> dao.replaceSource(source, named013Channels(index)) }
            dao.toggleFavorite(named013Channels(0).first().id)
            dao.toggleFavorite(named013Channels(3).last().id)
            dao.recordPlayback(named013Channels(1).last().id)
            app.settings.allowHttp(true)
            app.settings.showLogos(false)
            app.settings.selectSource(NAMED_013_SOURCES.last().id)
            app.settings.state.first { it.loaded && it.sourceId == NAMED_013_SOURCES.last().id && it.allowHttp && !it.showLogos }
            File(app.filesDir, UPGRADE_SENTINEL).writeText("written by versionCode 4 before package update")
        }
    }
}

@RunWith(AndroidJUnit4::class)
class CatalogUpgradePreservationTest {
    @Test fun named013CatalogsSelectionFavoritesHistoryAndSettingsSurviveInPlaceUpdate() = runBlocking {
        val app = ApplicationProvider.getApplicationContext<IPTVApplication>()
        assertEquals(5L, app.packageManager.getPackageInfo(app.packageName, 0).longVersionCode)
        withTimeout(20_000) {
            val dao = app.database.channels()
            assertEquals(1, app.database.openHelper.writableDatabase.version)
            NAMED_013_SOURCES.forEachIndexed { index, source ->
                assertEquals(source, dao.source(source.id))
                assertEquals(index >= 2, dao.source(source.id)!!.isFileSource)
                assertEquals(named013Channels(index), dao.observeSource(source.id).first().map { it.channel() })
            }
            assertEquals(NAMED_013_SOURCES.map { it.id }.toSet(), dao.observeSources().first().map { it.id }.toSet())
            assertTrue(dao.isFavorite(named013Channels(0).first().id))
            assertTrue(dao.isFavorite(named013Channels(3).last().id))
            assertEquals(named013Channels(1).last().id, dao.observeHistory().first().single().id)
            val settings = app.settings.state.first { it.loaded }
            assertEquals(NAMED_013_SOURCES.last().id, settings.sourceId)
            assertTrue(settings.allowHttp)
            assertFalse(settings.showLogos)
            assertFalse("The new Home behavior requires consent after an upgrade too", settings.autoPictureInPicture)
            assertEquals("written by versionCode 4 before package update", File(app.filesDir, UPGRADE_SENTINEL).readText())
            assertNull(app.crashReports.read())
        }
    }
}
