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
