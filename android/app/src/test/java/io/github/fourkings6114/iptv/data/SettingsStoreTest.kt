package io.github.fourkings6114.iptv.data

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class SettingsStoreTest {
    @Test fun graphiteThemeDefaultsAndThemeChoicePersists() = runBlocking {
        assertEquals(DEFAULT_THEME_STYLE, AppSettings().themeStyle)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        try {
            val store = SettingsStore(ApplicationProvider.getApplicationContext<Application>(), scope)
            withTimeout(20_000) {
                store.state.first { it.loaded }
                assertEquals(DEFAULT_THEME_STYLE, store.state.value.themeStyle)
                store.themeStyle("plum")
                assertEquals("plum", store.state.first { it.themeStyle == "plum" }.themeStyle)
                store.themeStyle("not-a-real-theme")
                assertEquals("plum", store.state.first().themeStyle)
                assertEquals("plum", SettingsStore(ApplicationProvider.getApplicationContext<Application>(), scope).state.first { it.loaded }.themeStyle)
            }
        } finally { scope.cancel() }
    }

    @Test fun playbackPreferencesPersistAndRejectUnknownValues() = runBlocking {
        assertEquals("normal", AppSettings().bufferProfile)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        try {
            val context = ApplicationProvider.getApplicationContext<Application>()
            val store = SettingsStore(context, scope)
            withTimeout(20_000) {
                store.state.first { it.loaded }
                store.bufferProfile("very_large")
                store.playerOrientation("landscape")
                store.sleepTimerMinutes(45)
                store.rewindSeconds(30)
                store.fullScreenResizeMode("zoom")
                store.windowedResizeMode("fill")
                store.controllerTimeoutMs(10_000)
                store.showPlayerClock(true)
                store.audioDecoderFallback(false)
                store.textScale(1.2f)
                store.screenScale(0.9f)
                store.bufferProfile("unknown")
                store.playerOrientation("upside_down")
                store.sleepTimerMinutes(-1)
                store.rewindSeconds(7)
                store.fullScreenResizeMode("unknown")
                val saved = SettingsStore(context, scope).state.first {
                    it.loaded && it.bufferProfile == "very_large" && it.playerOrientation == "landscape" &&
                        it.sleepTimerMinutes == 45 && it.rewindSeconds == 30 && it.showPlayerClock && !it.audioDecoderFallback
                }
                assertEquals("zoom", saved.fullScreenResizeMode)
                assertEquals("fill", saved.windowedResizeMode)
                assertEquals(10_000, saved.controllerTimeoutMs)
                assertEquals(1.2f, saved.textScale)
                assertEquals(0.9f, saved.screenScale)
                store.bufferProfile("auto")
                assertEquals("auto", store.state.first { it.bufferProfile == "auto" }.bufferProfile)
            }
        } finally { scope.cancel() }
    }

    @Test fun parentalPinPersistsAsVerifierAndControlsFilter() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        try {
            val store = SettingsStore(ApplicationProvider.getApplicationContext<Application>(), scope)
            withTimeout(20_000) {
                store.state.first { it.loaded }
                store.setParentalPin("4815")
                val secured = store.state.first { it.parentalControlEnabled && it.parentalPinHash.isNotBlank() }
                assertNotEquals("4815", secured.parentalPinHash)
                assertTrue(store.verifyParentalPin("4815"))
                assertFalse(store.verifyParentalPin("5814"))
                store.parentalControl(false)
                assertFalse(store.state.first { !it.parentalControlEnabled }.parentalControlEnabled)
            }
        } finally { scope.cancel() }
    }

    @Test fun automaticPictureInPictureDefaultsOffAndPersistsIndependently() = runBlocking {
        assertFalse(AppSettings().autoPictureInPicture)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        try {
            val context = ApplicationProvider.getApplicationContext<Application>()
            val store = SettingsStore(context, scope)
            withTimeout(20_000) {
                val before = store.state.first { it.loaded }
                store.automaticPictureInPicture(true)
                val enabled = store.state.first { it.autoPictureInPicture }
                assertEquals(before.allowHttp, enabled.allowHttp)
                assertEquals(before.showLogos, enabled.showLogos)
                assertEquals(before.sourceId, enabled.sourceId)
                val reopened = SettingsStore(context, scope).state.first { it.loaded }
                assertTrue(reopened.autoPictureInPicture)
                store.automaticPictureInPicture(false)
                assertFalse(store.state.first { !it.autoPictureInPicture }.autoPictureInPicture)
            }
        } finally { scope.cancel() }
    }

    @Test fun xmltvUrlsAreIndependentPerCatalogAndForgetDoesNotResetPipOrOtherUrls() = runBlocking {
        assertEquals("", AppSettings().epgUrl)
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        try {
            val context = ApplicationProvider.getApplicationContext<Application>()
            val store = SettingsStore(context, scope)
            withTimeout(20_000) {
                store.state.first { it.loaded }
                store.automaticPictureInPicture(true)
                store.epgUrl("first", "https://example.invalid/first.xml?token=local-only")
                store.epgUrl("second", "https://example.invalid/second.xml.gz")
                store.selectSource("first")
                assertEquals("https://example.invalid/first.xml?token=local-only", store.state.first { it.sourceId == "first" && it.epgUrl.isNotBlank() }.epgUrl)
                store.selectSource("second")
                assertEquals("https://example.invalid/second.xml.gz", store.state.first { it.sourceId == "second" && it.epgUrl.isNotBlank() }.epgUrl)
                store.forgetSource("first")
                assertEquals("", store.epgUrlFor("first"))
                assertEquals("https://example.invalid/second.xml.gz", store.epgUrlFor("second"))
                val reopened = SettingsStore(context, scope).state.first { it.loaded }
                assertEquals("second", reopened.sourceId)
                assertEquals("https://example.invalid/second.xml.gz", reopened.epgUrl)
                assertTrue(reopened.autoPictureInPicture)
                store.forgetSource("second")
                assertEquals("", store.state.first { it.sourceId == SAMPLE_SOURCE_ID }.epgUrl)
                assertTrue(store.state.value.autoPictureInPicture)
            }
        } finally { scope.cancel() }
    }

    @Test fun backupRestoreResetsDeviceConsentsAndPinButReplacesSafePreferences() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        try {
            val context = ApplicationProvider.getApplicationContext<Application>()
            val store = SettingsStore(context, scope)
            withTimeout(20_000) {
                store.state.first { it.loaded }
                store.allowHttp(true)
                store.automaticPictureInPicture(true)
                store.setParentalPin("4815")
                store.epgUrl("old-source", "https://example.invalid/old.xml")
                store.selectSource("old-source")
                store.state.first { it.sourceId == "old-source" && it.allowHttp && it.autoPictureInPicture && it.parentalControlEnabled }

                val backup = BackupPreferences(
                    sourceId = "restored-source", showLogos = false, themeStyle = "plum", bufferProfile = "large",
                    playerOrientation = "landscape", hideSourceSelector = true, sleepTimerMinutes = 30, rewindSeconds = 15,
                    fullScreenResizeMode = "zoom", windowedResizeMode = "fill", controllerTimeoutMs = 10_000,
                    showPlayerClock = true, audioDecoderFallback = false, textScale = 1.2f, screenScale = 0.9f,
                    epgUrls = mapOf("restored-source" to "https://example.invalid/guide.xml"),
                )
                store.restoreFromBackup(backup)

                val restored = store.state.first { it.sourceId == "restored-source" && it.themeStyle == "plum" }
                assertFalse(restored.allowHttp)
                assertFalse(restored.autoPictureInPicture)
                assertFalse(restored.parentalControlEnabled)
                assertTrue(restored.parentalPinHash.isEmpty())
                assertTrue(store.verifyParentalPin("4815").not())
                assertFalse(restored.showLogos)
                assertEquals("large", restored.bufferProfile)
                assertEquals("landscape", restored.playerOrientation)
                assertEquals(30, restored.sleepTimerMinutes)
                assertEquals(15, restored.rewindSeconds)
                assertEquals("zoom", restored.fullScreenResizeMode)
                assertEquals("fill", restored.windowedResizeMode)
                assertEquals(10_000, restored.controllerTimeoutMs)
                assertTrue(restored.showPlayerClock)
                assertFalse(restored.audioDecoderFallback)
                assertEquals(1.2f, restored.textScale)
                assertEquals(0.9f, restored.screenScale)
                assertEquals("https://example.invalid/guide.xml", restored.epgUrl)
                assertEquals("", store.epgUrlFor("old-source"))
            }
        } finally { scope.cancel() }
    }

    @Test fun forgettingOnlyTheSelectedSourcePreservesOtherSelectionsAndPreferences() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        try {
            val store = SettingsStore(ApplicationProvider.getApplicationContext(), scope)
            withTimeout(20_000) {
                store.state.first { it.loaded }
                store.allowHttp(true)
                store.showLogos(false)
                store.automaticPictureInPicture(true)
                store.selectSource("custom") // Existing 0.1.2 setting is still accepted unchanged.
                store.state.first { it.sourceId == "custom" && it.allowHttp && !it.showLogos && it.autoPictureInPicture }
                store.forgetSource("unselected-file")
                assertEquals("custom", store.state.first().sourceId)
                store.selectSource("file")
                store.state.first { it.sourceId == "file" }
                store.forgetSource("custom")
                assertEquals("file", store.state.first().sourceId)
                store.forgetSource("file")
                val fallback = store.state.first { it.sourceId == SAMPLE_SOURCE_ID }
                assertTrue(fallback.allowHttp)
                assertFalse(fallback.showLogos)
                assertTrue(fallback.autoPictureInPicture)
                store.automaticPictureInPicture(false)
            }
        } finally { scope.cancel() }
    }
}
