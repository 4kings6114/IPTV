package io.github.fourkings6114.iptv

import android.net.Uri
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextReplacement
import androidx.lifecycle.ViewModelProvider
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import io.github.fourkings6114.iptv.data.SAMPLE_SOURCE_ID
import io.github.fourkings6114.iptv.epg.EpgCache
import io.github.fourkings6114.iptv.epg.epgClock
import io.github.fourkings6114.iptv.ui.MainViewModel
import java.io.ByteArrayOutputStream
import java.io.File
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import java.util.zip.GZIPOutputStream
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Actual API31/33 UI + loopback XMLTV only. No provider credentials or guide is used. */
@RunWith(AndroidJUnit4::class)
class EpgGuideTest {
    @get:Rule val ui = createEmptyComposeRule()
    private val app get() = ApplicationProvider.getApplicationContext<IPTVApplication>()
    private val dao get() = app.database.channels()
    private val added = mutableListOf<String>()
    private val files = mutableListOf<File>()
    private val saved = mutableListOf<String>()
    private val token = "EpgTokenDoNotCopy"
    private val start = System.currentTimeMillis() / 1000 * 1000 - 600_000
    private val stop = start + 3_600_000
    private val currentTitle = "برنامهٔ اکنون آزمون"
    private val nextTitle = "برنامهٔ بعدی آزمون"
    private fun currentText() = "اکنون · ${epgClock(start, System.currentTimeMillis())} · $currentTitle"
    private fun nextText() = "بعدی · ${epgClock(stop, System.currentTimeMillis())} · $nextTitle"

    @Before fun prepare() = runBlocking {
        withTimeout(20_000) {
            app.crashReports.clear()
            app.settings.showLogos(false)
            app.settings.allowHttp(true) // Explicit synthetic loopback opt-in only.
            app.settings.selectSource(SAMPLE_SOURCE_ID)
            app.repository.ensureSource(SAMPLE_SOURCE_ID)
            app.epg.remove(SAMPLE_SOURCE_ID)
            app.settings.state.first { it.loaded && it.sourceId == SAMPLE_SOURCE_ID && it.allowHttp && !it.showLogos }
        }
    }

    @After fun restore() = runBlocking {
        withTimeout(20_000) {
            app.settings.selectSource(SAMPLE_SOURCE_ID)
            added.forEach { id ->
                if (dao.source(id) != null) app.repository.deleteSource(id)
                app.epg.remove(id)
            }
            saved.forEach { dao.removeFavorite(it) }
            app.settings.allowHttp(false)
            app.settings.state.first { it.sourceId == SAMPLE_SOURCE_ID && !it.allowHttp }
            files.forEach { it.delete() }
        }
    }

    @Test fun xmltvSettingsShowExactNowNextTimesAndReopenOffline() {
        val requests = ConcurrentLinkedQueue<RecordedRequest>()
        val server = MockWebServer()
        var running = false
        try {
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    requests.add(request)
                    return if (request.requestUrl?.encodedPath == "/guide.xml") MockResponse().setBody(xml(server.url("/never-fetch.dtd").toString()))
                    else MockResponse().setResponseCode(404)
                }
            }
            server.start(); running = true
            val url = server.url("/guide.xml?token=$token").toString()
            val first = source("راهنمای آزمون اول")
            val second = source("راهنمای آزمون دوم", "Other.ID")
            val channelId = runBlocking { dao.observeSource(first).first().first().id }
            assertEquals(channelId, runBlocking { dao.observeSource(second).first().first().id })
            val pipBefore = app.settings.state.value.autoPictureInPicture
            select(first)
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                awaitText("EPG test channel")
                saveUrl(url)
                await { app.settings.state.value.epgUrl == url }
                awaitIdle(scenario)
                assertEquals("Saving/reopening settings must not make an automatic guide request", 0, requests.size)
                ui.onNodeWithText("دریافت راهنما").performScrollTo().performClick()
                await { app.epg.cached(first, url) != null }
                awaitIdle(scenario)
                ui.onNodeWithText("کانال‌ها").performClick()
                awaitText(currentText())
                ui.onNodeWithText(currentText()).assertIsDisplayed()
                ui.onNodeWithText(nextText()).assertIsDisplayed()
                assertEquals(1, ui.onAllNodesWithText("اکنون ·", substring = true).fetchSemanticsNodes().size)
                assertEquals(1, requests.size) // The external XMLTV DTD was NOT fetched.
                requests.forEach { assertNull(it.getHeader("Authorization")); assertNull(it.getHeader("Cookie")) }
                runBlocking { dao.toggleFavorite(channelId); dao.recordPlayback(channelId) }
                saved += channelId
                scenario.recreate()
                awaitText(currentText())
                assertEquals(1, requests.size)
                // Same shared channel identity, different selected catalog: no guide leakage.
                ui.onNodeWithText("راهنمای آزمون اول").performClick()
                ui.onNodeWithContentDescription("انتخاب فهرست راهنمای آزمون دوم").performScrollTo().performClick()
                await { app.settings.state.value.sourceId == second }
                awaitText("EPG test channel")
                ui.onNodeWithText(currentText()).assertDoesNotExist()
                ui.onNodeWithText("راهنمای آزمون دوم").performClick()
                ui.onNodeWithContentDescription("انتخاب فهرست راهنمای آزمون اول").performScrollTo().performClick()
                awaitText(currentText())
            }
            server.shutdown(); running = false
            // New Activity + ViewModel, actual server offline, cached guide read from disk.
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                awaitText(currentText())
                ui.onNodeWithText(nextText()).assertIsDisplayed()
                assertEquals(1, requests.size)
                val disk = runBlocking { EpgCache(File(app.cacheDir, "epg")).read(first, url, System.currentTimeMillis()) }!!
                assertEquals(currentTitle, disk.at("Guide.Exact", System.currentTimeMillis()).current!!.title)
                val bytes = File(app.cacheDir, "epg").listFiles().orEmpty().filter { it.extension == "json" }.joinToString { it.readText() }
                assertFalse(bytes.contains(token))
                assertFalse(bytes.contains("DO_NOT_STORE_DESCRIPTION"))
                ui.onNodeWithText("تنظیمات").performClick()
                ui.onNodeWithText("تنظیم لینک XMLTV").performScrollTo().performClick()
                ui.onNodeWithText("حذف لینک و راهنما").performScrollTo().performClick()
                await { app.settings.state.value.epgUrl.isBlank() && app.settings.epgUrlFor(first).isBlank() }
                awaitIdle(scenario)
                ui.onNodeWithText("کانال‌ها").performClick()
                ui.onNodeWithText(currentText()).assertDoesNotExist()
                assertTrue(runBlocking { dao.isFavorite(channelId) })
                assertTrue(runBlocking { dao.observeHistory().first().any { it.id == channelId } })
                assertEquals(pipBefore, app.settings.state.value.autoPictureInPicture)
                assertNull(app.crashReports.read())
            }
        } finally { if (running) server.shutdown() }
    }

    @Test fun failedAndCancelledGuideRefreshKeepGoodDataAndHidePrivateTokens() {
        val mode = AtomicReference("gzip")
        val requests = ConcurrentLinkedQueue<RecordedRequest>()
        MockWebServer().use { server ->
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    requests.add(request)
                    return when (mode.get()) {
                        "gzip" -> MockResponse().setHeader("Content-Encoding", "gzip").setBody(Buffer().write(gzip(xml())))
                        "invalid" -> MockResponse().setBody("<tv><private token='$token'>broken XML")
                        "slow" -> MockResponse().setBody(xml().replace(currentTitle, "Must not replace")).throttleBody(1, 1, TimeUnit.SECONDS)
                        else -> MockResponse().setResponseCode(401).setBody(token)
                    }
                }
            }
            server.start()
            val id = source("راهنمای خطا و لغو")
            val url = server.url("/guide.xml?token=$token").toString()
            select(id)
            runBlocking { app.epg.configure(id, url) }
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                awaitText("EPG test channel")
                ui.onNodeWithContentDescription("به‌روزرسانی راهنما").performClick()
                awaitText(currentText())
                awaitIdle(scenario)
                val previous = runBlocking { app.epg.cached(id, url) }!!
                mode.set("invalid")
                ui.onNodeWithContentDescription("به‌روزرسانی راهنما").performClick()
                awaitError(scenario, "XMLTV")
                ui.onNodeWithText(currentText()).assertIsDisplayed()
                assertEquals(previous.programmes, runBlocking { app.epg.cached(id, url) }!!.programmes)
                mode.set("denied")
                ui.onNodeWithContentDescription("به‌روزرسانی راهنما").performClick()
                awaitError(scenario, "401")
                val beforeHttp = requests.size
                runBlocking { app.settings.allowHttp(false); app.settings.state.first { !it.allowHttp } }
                ui.onNodeWithContentDescription("به‌روزرسانی راهنما").performClick()
                awaitError(scenario, "HTTP")
                assertEquals(beforeHttp, requests.size)
                runBlocking { app.settings.allowHttp(true); app.settings.state.first { it.allowHttp } }
                mode.set("slow")
                ui.onNodeWithText("تنظیمات").performClick()
                ui.onNodeWithText("دریافت راهنما").performScrollTo().performClick()
                await { requests.size > beforeHttp }
                ui.onNodeWithText("لغو دریافت راهنما").performScrollTo().performClick()
                awaitIdle(scenario)
                assertEquals(previous.programmes, runBlocking { app.epg.cached(id, url) }!!.programmes)
                ui.onNodeWithText("کانال‌ها").performClick()
                awaitText(currentText())
                assertEquals(url, runBlocking { app.settings.epgUrlFor(id) })
                assertEquals(id, app.settings.state.value.sourceId)
                assertNull(app.crashReports.read())
            }
        }
    }

    private fun saveUrl(url: String) {
        ui.onNodeWithText("تنظیمات").performClick()
        ui.onNodeWithText("تنظیم لینک XMLTV").performScrollTo().performClick()
        ui.onNodeWithText("لینک XMLTV (HTTP / HTTPS)").performTextReplacement(url)
        ui.onNodeWithText("ذخیره لینک").performClick()
        awaitText("دریافت راهنما")
    }
    private fun awaitError(scenario: ActivityScenario<MainActivity>, contains: String) {
        var error: String? = null
        ui.waitUntil(20_000) { scenario.onActivity { error = model(it).epgError.value }; error?.contains(contains) == true }
        assertFalse(error!!.contains(token))
        assertFalse(error!!.contains("localhost"))
        assertFalse(error!!.contains("broken XML"))
        awaitIdle(scenario)
    }
    private fun source(title: String, xmltvId: String = "Guide.Exact"): String {
        val file = File.createTempFile("epg-runtime-", ".m3u", app.cacheDir)
        file.writeText("#EXTM3U\n#EXTINF:-1 tvg-id=\"$xmltvId\" x-original-tvg-id=\"Legacy.Guide\",EPG test channel\n#EXTHTTP:{\"Authorization\":\"Bearer MEDIA_ONLY\",\"Cookie\":\"MEDIA_ONLY=1\"}\nhttps://example.invalid/epg-runtime.mp4\n#EXTINF:-1,No guide channel\nhttps://example.invalid/no-epg-runtime.mp4\n")
        files += file
        return runBlocking { app.repository.importFile(Uri.fromFile(file), title) }.also { added += it }
    }
    private fun select(id: String) = runBlocking {
        app.settings.selectSource(id)
        withTimeout(20_000) { app.settings.state.first { it.sourceId == id } }
    }
    private fun xml(doctype: String? = null): String {
        fun time(value: Long) = DateTimeFormatter.ofPattern("uuuuMMddHHmmss xx").withZone(ZoneOffset.UTC).format(Instant.ofEpochMilli(value))
        return (doctype?.let { "<!DOCTYPE tv SYSTEM \"$it\">" }.orEmpty()) + "<tv>" +
            "<programme channel=\"Guide.Exact\" start=\"${time(start)}\" stop=\"${time(stop)}\"><title lang=\"fa\">$currentTitle</title><desc>DO_NOT_STORE_DESCRIPTION</desc></programme>" +
            "<programme channel=\"Guide.Exact\" start=\"${time(stop)}\" stop=\"${time(stop + 3_600_000)}\"><title>$nextTitle</title></programme>" +
            "<programme channel=\"guide.exact\" start=\"${time(start)}\" stop=\"${time(stop)}\"><title>Wrong case must not match</title></programme></tv>"
    }
    private fun gzip(text: String) = ByteArrayOutputStream().also { bytes -> GZIPOutputStream(bytes).use { it.write(text.toByteArray()) } }.toByteArray()
    private fun model(activity: MainActivity) = ViewModelProvider(activity)[MainViewModel::class.java]
    private fun awaitText(text: String) = ui.waitUntil(20_000) { ui.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty() }
    private fun await(check: suspend () -> Boolean) = ui.waitUntil(20_000) { runBlocking { check() } }
    private fun awaitIdle(scenario: ActivityScenario<MainActivity>) {
        var busy = true
        ui.waitUntil(20_000) { scenario.onActivity { busy = model(it).epgBusy.value || model(it).busy.value }; !busy }
    }
}
