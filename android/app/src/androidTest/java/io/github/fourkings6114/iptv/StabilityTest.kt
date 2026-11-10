package io.github.fourkings6114.iptv

import android.content.Intent
import android.content.pm.ActivityInfo
import android.os.SystemClock
import android.view.View
import android.view.ViewGroup
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onFirst
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.lifecycle.Lifecycle
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import io.github.fourkings6114.iptv.core.Channel
import io.github.fourkings6114.iptv.data.SAMPLE_SOURCE_ID
import io.github.fourkings6114.iptv.data.SourceEntity
import io.github.fourkings6114.iptv.player.PlaybackActivity
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** Actual Android API-33 runtime, synthetic localhost media only; NOT a Poco/MIUI test. */
@RunWith(AndroidJUnit4::class)
@androidx.annotation.OptIn(UnstableApi::class)
class StabilityTest {
    @get:Rule val ui = createEmptyComposeRule()
    private val app get() = ApplicationProvider.getApplicationContext<IPTVApplication>()

    @Before fun preparePrivateTestData() = runBlocking {
        app.crashReports.clear()
        app.settings.showLogos(false) // no third-party network requests during this test
        app.settings.selectSource(SAMPLE_SOURCE_ID)
        app.settings.state.first { it.loaded && !it.showLogos && it.sourceId == SAMPLE_SOURCE_ID }
        app.repository.ensureSource(SAMPLE_SOURCE_ID)
    }

    @Test fun browsingFavoritesAndRecreationSurviveSeventySeconds() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            ui.waitUntil(20_000) { ui.onAllNodesWithText("IRIB TV1").fetchSemanticsNodes(atLeastOneRootRequired = false).isNotEmpty() }
            ui.onAllNodesWithContentDescription("افزودن به علاقه‌مندی‌ها").onFirst().performClick()
            ui.onNodeWithText("علاقه‌مندی‌ها").performClick()
            ui.onNodeWithText("IRIB TV1").assertIsDisplayed()
            ui.onNodeWithText("کانال‌ها").performClick()
            repeat(7) {
                ui.onNodeWithText("تنظیمات").performClick()
                ui.onNodeWithText("منبع و حریم خصوصی").assertIsDisplayed()
                ui.onNodeWithText("کانال‌ها").performClick()
                SystemClock.sleep(10_000)
            }
            scenario.recreate()
            ui.waitUntil(20_000) { ui.onAllNodesWithText("IRIB TV1").fetchSemanticsNodes(atLeastOneRootRequired = false).isNotEmpty() }
            ui.onNodeWithText("IRIB TV1").assertIsDisplayed()
            assertNull(app.crashReports.read())
        }
    }

    @Test fun actualAvcAacPlaybackSurvivesSeventyFiveSecondsRotationAndBackgroundResume() {
        val fixture = InstrumentationRegistry.getInstrumentation().context.assets.open("stability.mp4").use { it.readBytes() }
        assertTrue("CI must generate a bounded synthetic MP4 fixture", fixture.size in 1000..4_000_000)
        MockWebServer().use { server ->
            server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    if (request.path != "/stability.mp4") return MockResponse().setResponseCode(404)
                    val offset = request.getHeader("Range")?.let { Regex("bytes=(\\d+)-").find(it)?.groupValues?.get(1)?.toIntOrNull() } ?: 0
                    if (offset >= fixture.size) return MockResponse().setResponseCode(416)
                    return MockResponse().setResponseCode(if (offset == 0) 200 else 206)
                        .setHeader("Content-Type", "video/mp4").setHeader("Accept-Ranges", "bytes")
                        .apply { if (offset > 0) setHeader("Content-Range", "bytes $offset-${fixture.lastIndex}/${fixture.size}") }
                        .setBody(Buffer().write(fixture, offset, fixture.size - offset))
                }
            }
            server.start()
            val channel = Channel("local-stability-fixture", "Synthetic AVC AAC", "Local test", server.url("/stability.mp4").toString())
            runBlocking {
                app.settings.allowHttp(true) // test-only opt-in, not a production default change
                app.settings.state.first { it.allowHttp }
                app.database.channels().replaceSource(SourceEntity("local-test", "Local test", updatedAt = 1, count = 1), listOf(channel))
            }
            val intent = Intent(app, PlaybackActivity::class.java).putExtra(PlaybackActivity.CHANNEL_ID, channel.id)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            ActivityScenario.launch<PlaybackActivity>(intent).use { scenario ->
                waitForDecodedVideo(scenario)
                val start = SystemClock.elapsedRealtime()
                while (SystemClock.elapsedRealtime() - start < TimeUnit.SECONDS.toMillis(75)) {
                    SystemClock.sleep(2000)
                    scenario.onActivity { activity ->
                        val engine = playerView(activity.window.decorView)?.player
                        assertNotNull(engine)
                        assertNull(engine!!.playerError)
                        assertTrue("Synthetic local media must keep playing", engine.isPlaying)
                    }
                }
                scenario.onActivity { activity ->
                    assertTrue(playerView(activity.window.decorView)!!.player!!.currentPosition >= 60_000)
                    activity.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_LANDSCAPE
                }
                waitForDecodedVideo(scenario)
                lateinit var stoppedRoot: View
                scenario.onActivity { stoppedRoot = it.window.decorView }
                scenario.moveToState(Lifecycle.State.CREATED)
                InstrumentationRegistry.getInstrumentation().runOnMainSync { assertNull(playerView(stoppedRoot)?.player) }
                scenario.moveToState(Lifecycle.State.RESUMED)
                waitForDecodedVideo(scenario) // regression: old onStart guard could leave player null
                assertNull(app.crashReports.read())
            }
        }
    }

    private fun waitForDecodedVideo(scenario: ActivityScenario<PlaybackActivity>) {
        val deadline = SystemClock.elapsedRealtime() + 25_000
        while (SystemClock.elapsedRealtime() < deadline) {
            var decoded = false
            scenario.onActivity { activity ->
                val engine = playerView(activity.window.decorView)?.player as? ExoPlayer
                assertNull(engine?.playerError)
                decoded = engine?.isPlaying == true && (engine.videoDecoderCounters?.renderedOutputBufferCount ?: 0) > 0
            }
            if (decoded) return
            SystemClock.sleep(150)
        }
        fail("Player did not start/render a video frame within 25 seconds")
    }

    private fun playerView(view: View): PlayerView? {
        if (view is PlayerView) return view
        if (view is ViewGroup) for (index in 0 until view.childCount) playerView(view.getChildAt(index))?.let { return it }
        return null
    }

}
