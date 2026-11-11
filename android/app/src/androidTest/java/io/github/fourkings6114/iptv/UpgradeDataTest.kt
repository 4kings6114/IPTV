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
        assertEquals(4L, app.packageManager.getPackageInfo(app.packageName, 0).longVersionCode)
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
            assertEquals("written by versionCode 3 before package update", File(app.filesDir, UPGRADE_SENTINEL).readText())
            assertNull(app.crashReports.read())
        }
    }
}
