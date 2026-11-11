package io.github.fourkings6114.iptv

import android.net.Uri
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTextReplacement
import androidx.lifecycle.ViewModelProvider
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import io.github.fourkings6114.iptv.data.SAMPLE_SOURCE_ID
import io.github.fourkings6114.iptv.data.SourceEntity
import io.github.fourkings6114.iptv.ui.MainViewModel
import java.io.File
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Synthetic local catalogs; the Android document-picker result is supplied programmatically. */
@RunWith(AndroidJUnit4::class)
class SourceManagementTest {
    @get:Rule val ui = createEmptyComposeRule()
    private val app get() = ApplicationProvider.getApplicationContext<IPTVApplication>()
    private val dao get() = app.database.channels()
    private val added = mutableListOf<String>()
    private val files = mutableListOf<File>()

    @Before fun prepare() = runBlocking {
        withTimeout(20_000) {
            app.crashReports.clear()
            app.settings.showLogos(false)
            app.settings.allowHttp(true) // Local test server only; production default is unchanged.
            app.settings.selectSource(SAMPLE_SOURCE_ID)
            app.settings.state.first { it.loaded && it.sourceId == SAMPLE_SOURCE_ID && it.allowHttp && !it.showLogos }
            app.repository.ensureSource(SAMPLE_SOURCE_ID)
        }
    }

    @After fun restore() = runBlocking {
        withTimeout(20_000) {
            app.settings.selectSource(SAMPLE_SOURCE_ID)
            added.forEach { id -> if (dao.source(id) != null) app.repository.deleteSource(id) }
            app.settings.allowHttp(false)
            app.settings.state.first { it.sourceId == SAMPLE_SOURCE_ID && !it.allowHttp }
            files.forEach { it.delete() }
        }
    }

    @Test fun independentLinksCanBeNamedRefreshedRenamedDeletedAndReopened() {
        val shared = entry("Shared test channel", "shared-source-test")
        val first = "#EXTM3U\n$shared${entry("Only first", "only-first-source-test")}"
        val secondBody = AtomicReference("#EXTM3U\n$shared${entry("Only second", "only-second-source-test")}")
        MockWebServer().use { server ->
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                    "/first.m3u" -> MockResponse().setBody(first)
                    "/second.m3u" -> MockResponse().setBody(secondBody.get())
                    else -> MockResponse().setResponseCode(404)
                }
            }
            server.start()
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                awaitText("IRIB TV1")
                addLink("آزمون اول", server.url("/first.m3u").toString())
                val one = awaitSource("آزمون اول")
                added += one.id
                await { app.settings.state.value.sourceId == one.id }
                awaitIdle(scenario)
                addLink("آزمون دوم", server.url("/second.m3u").toString())
                val two = awaitSource("آزمون دوم")
                added += two.id
                assertNotEquals(one.id, two.id)
                await { app.settings.state.value.sourceId == two.id }
                awaitIdle(scenario)
                openManager()
                ui.onNodeWithContentDescription("انتخاب فهرست آزمون اول").performScrollTo().performClick()
                awaitText("Only first")
                await { app.settings.state.value.sourceId == one.id }
                val saved = runBlocking { dao.observeSource(one.id).first().single { it.name == "Only first" } }
                runBlocking { dao.toggleFavorite(saved.id); dao.recordPlayback(saved.id) }
                openManager()
                secondBody.set("#EXTM3U\n$shared${entry("Second refreshed", "second-refreshed-source-test")}")
                ui.onNodeWithContentDescription("به‌روزرسانی فهرست آزمون دوم").performScrollTo().performClick()
                await { dao.observeSource(two.id).first().any { it.name == "Second refreshed" } }
                awaitIdle(scenario)
                assertEquals(one.id, app.settings.state.value.sourceId)
                assertEquals(listOf("Shared test channel", "Only first"), runBlocking { dao.observeSource(one.id).first().map { it.name } })
                ui.onNodeWithContentDescription("تغییر نام فهرست آزمون دوم").performScrollTo().performClick()
                ui.onNodeWithText("نام فهرست").performTextReplacement("آزمون دوم تازه")
                ui.onNodeWithText("ذخیره نام").performClick()
                await { dao.source(two.id)?.title == "آزمون دوم تازه" }
                awaitIdle(scenario)
                ui.onNodeWithText("بستن").performClick()
                scenario.recreate()
                awaitText("Only first")
                assertEquals(one.id, app.settings.state.value.sourceId)
                openManager()
                ui.onNodeWithContentDescription("حذف فهرست آزمون اول").performScrollTo().performClick()
                ui.onNodeWithText("انصراف").performClick()
                assertNotNull(runBlocking { dao.source(one.id) })
                ui.onNodeWithContentDescription("حذف فهرست آزمون اول").performScrollTo().performClick()
                ui.onNodeWithText("حذف فهرست").performClick()
                await { dao.source(one.id) == null && app.settings.state.value.sourceId == SAMPLE_SOURCE_ID }
                awaitIdle(scenario)
                assertEquals(two.id, runBlocking { dao.source(two.id)!!.id })
                assertTrue(runBlocking { dao.isFavorite(saved.id) })
                assertTrue(runBlocking { dao.observeHistory().first().any { it.id == saved.id } })
                ui.onNodeWithText("بستن").performClick()
                ui.onNodeWithText("علاقه‌مندی‌ها").performClick()
                awaitText("Only first")
                ui.onNodeWithText("Only first").assertIsDisplayed()
                assertNull(app.crashReports.read())
            }
            // A new Activity/ViewModel reads the persisted source catalog and fallback selection.
            ActivityScenario.launch(MainActivity::class.java).use { scenario ->
                awaitText("IRIB TV1")
                assertEquals(SAMPLE_SOURCE_ID, app.settings.state.value.sourceId)
                openManager()
                ui.onNodeWithContentDescription("انتخاب فهرست آزمون دوم تازه").performScrollTo().performClick()
                awaitText("Second refreshed")
                awaitIdle(scenario)
            }
        }
    }

    @Test fun fileReimportKeepsItsTargetAcrossRecreationAndRejectsDeletedTargets() {
        val first = runBlocking { app.repository.importFile(file("First file", "first-file-source-test"), "فایل اول آزمون") }.also { added += it }
        val second = runBlocking { app.repository.importFile(file("Second file", "second-file-source-test"), "فایل دوم آزمون") }.also { added += it }
        val original = runBlocking { dao.observeSource(second).first().single() }
        runBlocking {
            app.repository.renameSource(second, "نام ثابت فایل دوم")
            dao.toggleFavorite(original.id)
            dao.recordPlayback(original.id)
            app.settings.selectSource(first)
            app.settings.state.first { it.sourceId == first }
        }
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            awaitText("First file")
            awaitIdle(scenario)
            scenario.onActivity {
                assertTrue(model(it).prepareFileImport("نام ثابت فایل دوم", second))
            }
            scenario.recreate()
            val replacement = file("Second replaced", "second-file-replaced-source-test")
            scenario.onActivity { model(it).fileChosen(replacement) }
            await { dao.observeSource(second).first().single().name == "Second replaced" }
            awaitIdle(scenario)
            assertEquals(first, app.settings.state.value.sourceId)
            assertEquals("First file", runBlocking { dao.observeSource(first).first().single().name })
            assertEquals("نام ثابت فایل دوم", runBlocking { dao.source(second)!!.title })
            assertTrue(runBlocking { dao.isFavorite(original.id) })
            assertTrue(runBlocking { dao.observeHistory().first().any { it.id == original.id } })
            val beforeCancel = runBlocking { dao.observeSources().first() }
            scenario.onActivity {
                assertTrue(model(it).prepareFileImport("لغو اضافه‌کردن"))
                model(it).fileChosen(null)
            }
            ui.waitForIdle()
            assertEquals(beforeCancel, runBlocking { dao.observeSources().first() })
            scenario.onActivity { assertTrue(model(it).prepareFileImport("نام ثابت فایل دوم", second)) }
            runBlocking { app.repository.deleteSource(second) }
            scenario.onActivity { model(it).fileChosen(replacement) }
            awaitText("این فهرست دیگر موجود نیست. از بخش فهرست‌ها یک منبع دیگر انتخاب کن.")
            awaitIdle(scenario)
            assertNull(runBlocking { dao.source(second) })
            assertEquals(first, app.settings.state.value.sourceId)
            // An actual add result creates another source and selects it, never overwrites first.
            scenario.onActivity {
                assertTrue(model(it).prepareFileImport("فایل تازه آزمون"))
                model(it).fileChosen(replacement)
            }
            val third = awaitSource("فایل تازه آزمون").also { added += it.id }
            await { app.settings.state.value.sourceId == third.id }
            awaitText("Second replaced")
            assertNotEquals(first, third.id)
            assertNotEquals(second, third.id)
            assertEquals("First file", runBlocking { dao.observeSource(first).first().single().name })
            assertNull(app.crashReports.read())
        }
    }

    @Test fun missingSelectionRecoversToOfflineSampleWithoutErasingSavedChannels() {
        val id = runBlocking { app.repository.importFile(file("Recovered favorite", "recovered-source-test"), "فهرست حذف‌شده آزمون") }.also { added += it }
        val saved = runBlocking { dao.observeSource(id).first().single() }
        runBlocking {
            dao.toggleFavorite(saved.id)
            dao.recordPlayback(saved.id)
            app.settings.selectSource(id)
            app.settings.state.first { it.sourceId == id }
            // Simulate process death after the Room transaction but before DataStore reset.
            app.repository.deleteSource(id)
        }
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            await { app.settings.state.value.sourceId == SAMPLE_SOURCE_ID }
            awaitText("IRIB TV1")
            awaitIdle(scenario)
            assertTrue(runBlocking { dao.isFavorite(saved.id) })
            assertTrue(runBlocking { dao.observeHistory().first().any { it.id == saved.id } })
            assertNull(runBlocking { dao.source(id) })
            assertNull(app.crashReports.read())
        }
    }

    private fun addLink(title: String, url: String) {
        ui.onNodeWithText("تنظیمات").performClick()
        ui.onNodeWithText("افزودن لینک").performScrollTo().performClick()
        ui.onNodeWithText("نام فهرست").performTextInput(title)
        ui.onNodeWithText("لینک HTTP / HTTPS").performTextInput(url)
        ui.onNodeWithText("افزودن فهرست").performScrollTo().performClick()
    }
    private fun openManager() {
        ui.onNodeWithText("تنظیمات").performClick()
        ui.onNodeWithText("مدیریت فهرست‌ها").performScrollTo().performClick()
    }
    private fun awaitSource(title: String): SourceEntity {
        await { dao.observeSources().first().any { it.title == title } }
        return runBlocking { dao.observeSources().first().single { it.title == title } }
    }
    private fun awaitText(text: String) = ui.waitUntil(20_000) { ui.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty() }
    private fun await(check: suspend () -> Boolean) = ui.waitUntil(20_000) { runBlocking { check() } }
    private fun awaitIdle(scenario: ActivityScenario<MainActivity>) {
        var busy = true
        ui.waitUntil(20_000) { scenario.onActivity { busy = model(it).busy.value }; !busy }
    }
    private fun model(activity: MainActivity) = ViewModelProvider(activity)[MainViewModel::class.java]
    private fun file(name: String, path: String): Uri {
        val file = File.createTempFile("source-management-", ".m3u", app.cacheDir)
        file.writeText("#EXTM3U\n${entry(name, path)}")
        files += file
        return Uri.fromFile(file)
    }
    private fun entry(name: String, path: String) = "#EXTINF:-1 group-title=\"Local test\",$name\nhttps://example.invalid/$path.m3u8\n"
}
