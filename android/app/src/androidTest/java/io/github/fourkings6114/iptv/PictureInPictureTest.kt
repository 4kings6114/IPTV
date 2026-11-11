package io.github.fourkings6114.iptv

import android.app.RemoteAction
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Rect
import android.os.ParcelFileDescriptor
import android.os.StrictMode
import android.os.SystemClock
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.Until
import io.github.fourkings6114.iptv.core.Channel
import io.github.fourkings6114.iptv.data.SourceEntity
import io.github.fourkings6114.iptv.player.PlaybackActivity
import java.util.regex.Pattern
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.Buffer
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/** Real system PiP windows/Home/menu, not injected lifecycle callbacks or provider streams. */
@RunWith(AndroidJUnit4::class)
@androidx.annotation.OptIn(UnstableApi::class)
class PictureInPictureTest {
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val app get() = ApplicationProvider.getApplicationContext<IPTVApplication>()
    private val device get() = UiDevice.getInstance(instrumentation)

    @Before fun prepare() {
        assertTrue("The phone emulator must advertise real PiP support", app.packageManager.hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE))
        app.crashReports.clear()
        setPipPermission("allow")
        preferences(automatic = false)
    }

    @Test fun manualPictureInPictureKeepsDecodedPlaybackAndRestoresFullscreen() = strictMain {
        withVideo { channels ->
            ActivityScenario.launch<PlaybackActivity>(intent(channels.first())).use { scenario ->
                lateinit var activity: PlaybackActivity
                scenario.onActivity { activity = it }
                waitForVideo(activity)
                val engine = main { view(activity)!!.player as ExoPlayer }
                clickManualPip(activity)
                waitFor("Native PiP did not open") { main { activity.isInPictureInPictureMode } }
                assertContinuousPip(activity, engine, 8_000)
                assertFalse(main { view(activity)!!.useController })
                assertFalse("Fullscreen toolbar must not overlay PiP", device.hasObject(By.desc("بازگشت به کانال‌ها")))
                clickSystemPipControl(activity, "expand_button")
                waitFor("PiP did not expand to fullscreen") { main { !activity.isInPictureInPictureMode && view(activity)!!.useController } }
                waitForVideo(activity)
                assertSame("Expansion must retain the existing decoder/client", engine, main { view(activity)!!.player })
                assertNotNull(device.wait(Until.findObject(By.desc("بازگشت به کانال‌ها")), 5_000))
                assertNull(app.crashReports.read())
            }
        }
    }

    @Test fun automaticPictureInPictureRequiresOptInAndDismissalReleasesThePlayer() = strictMain {
        withVideo { channels ->
            // Keep the pre-0.1.4 Home behavior until explicit opt-in.
            ActivityScenario.launch<PlaybackActivity>(intent(channels.first())).use { scenario ->
                lateinit var activity: PlaybackActivity
                scenario.onActivity { activity = it }
                waitForVideo(activity)
                val oldView = main { view(activity)!! }
                val oldClient = client(activity)
                assertTrue("HOME key injection failed", device.pressKeyCode(KeyEvent.KEYCODE_HOME))
                waitFor("Home without consent must stop, not minimize") { main { !activity.isInPictureInPictureMode && oldView.player == null } }
                waitForCleanup(oldClient)
            }
            preferences(automatic = true)
            ActivityScenario.launch<PlaybackActivity>(intent(channels.first())).use { scenario ->
                lateinit var activity: PlaybackActivity
                scenario.onActivity { activity = it }
                waitForVideo(activity)
                instrumentation.waitForIdleSync()
                val engine = main { view(activity)!!.player as ExoPlayer }
                val oldView = main { view(activity)!! }
                val oldClient = client(activity)
                assertTrue("HOME key injection failed", device.pressKeyCode(KeyEvent.KEYCODE_HOME))
                waitFor("Opted-in Home must enter native PiP") { main { activity.isInPictureInPictureMode } }
                assertContinuousPip(activity, engine, 6_000)
                clickSystemPipControl(activity, "dismiss")
                waitFor("Dismissed PiP must detach its player") { main { oldView.player == null } }
                waitForCleanup(oldClient)
                assertNull(app.crashReports.read())
            }
        }
    }

    @Test fun pictureInPictureActionsPauseResumeAndNewChannelReusesTheActivity() = strictMain {
        withVideo { channels ->
            ActivityScenario.launch<PlaybackActivity>(intent(channels.first())).use { scenario ->
                lateinit var activity: PlaybackActivity
                scenario.onActivity { activity = it }
                waitForVideo(activity)
                val original = main { view(activity)!!.player as ExoPlayer }
                val originalClient = client(activity)
                clickManualPip(activity)
                waitFor("PiP must open before system actions") { main { activity.isInPictureInPictureMode } }
                val pause = actionFor(activity, playing = true)
                assertTrue("PiP actions must be immutable", pause.actionIntent.isImmutable)
                assertEquals("مکث", pause.title.toString())
                clickSystemPipAction(activity, "مکث")
                waitFor("System pause action was not honored") { main { !original.playWhenReady && !original.isPlaying } }
                val position = main { original.currentPosition }
                SystemClock.sleep(1_000)
                assertTrue(main { original.currentPosition - position < 500 })
                val play = actionFor(activity, playing = false)
                assertEquals("ادامهٔ پخش", play.title.toString())
                clickSystemPipAction(activity, "ادامهٔ پخش")
                waitFor("System play action was not honored") { main { original.isPlaying } }
                assertContinuousPip(activity, original, 2_000)

                // Tap a different channel as the real application would. singleTask must
                // bring the SAME Activity out of PiP and safely replace only its player.
                main { app.startActivity(intent(channels.last())) }
                waitFor("Selecting another channel must expand/reuse the single player Activity") {
                    main { !activity.isInPictureInPictureMode && view(activity)?.player?.currentMediaItem?.mediaId == channels.last().id }
                }
                waitForVideo(activity)
                assertNotSame(original, main { view(activity)!!.player })
                waitForCleanup(originalClient)
                assertNull(app.crashReports.read())
            }
        }
    }

    @Test fun deniedPictureInPictureKeepsFullscreenUsableAndStopsOnHome() = strictMain {
        withVideo { channels ->
            preferences(automatic = true)
            setPipPermission("ignore")
            try {
                ActivityScenario.launch<PlaybackActivity>(intent(channels.first())).use { scenario ->
                    lateinit var activity: PlaybackActivity
                    scenario.onActivity { activity = it }
                    waitForVideo(activity)
                    val engine = main { view(activity)!!.player as ExoPlayer }
                    val oldView = main { view(activity)!! }
                    val oldClient = client(activity)
                    clickManualPip(activity)
                    SystemClock.sleep(1_000)
                    assertFalse(main { activity.isInPictureInPictureMode })
                    assertSame(engine, main { oldView.player })
                    assertTrue(main { engine.isPlaying && oldView.useController })
                    assertTrue("HOME key injection failed", device.pressKeyCode(KeyEvent.KEYCODE_HOME))
                    waitFor("A denied PiP must not continue invisible audio") { main { oldView.player == null } }
                    waitForCleanup(oldClient)
                    assertNull(app.crashReports.read())
                }
            } finally { setPipPermission("allow") }
        }
    }

    @Test fun explicitBackNeverAutoEntersPictureInPictureEvenWithHomeOptIn() = strictMain {
        withVideo { channels ->
            preferences(automatic = true)
            ActivityScenario.launch<PlaybackActivity>(intent(channels.first())).use { scenario ->
                lateinit var activity: PlaybackActivity
                scenario.onActivity { activity = it }
                waitForVideo(activity)
                val oldView = main { view(activity)!! }
                val oldClient = client(activity)
                assertTrue("BACK key injection failed", device.pressKeyCode(KeyEvent.KEYCODE_BACK))
                waitFor("Explicit Back must finish, not leave a floating or hidden player") {
                    main { activity.isFinishing && !activity.isInPictureInPictureMode && oldView.player == null }
                }
                waitForCleanup(oldClient)
                assertNull(app.crashReports.read())
            }
        }
    }

    private fun preferences(automatic: Boolean) = runBlocking {
        withTimeout(20_000) {
            app.settings.showLogos(false)
            app.settings.allowHttp(true) // synthetic loopback only, explicit test consent
            app.settings.automaticPictureInPicture(automatic)
            app.settings.state.first { it.loaded && !it.showLogos && it.allowHttp && it.autoPictureInPicture == automatic }
        }
    }

    private fun withVideo(test: (List<Channel>) -> Unit) {
        val bytes = instrumentation.context.assets.open("stability.mp4").use { it.readBytes() }
        try {
            MockWebServer().use { server ->
                server.dispatcher = object : Dispatcher() {
                    override fun dispatch(request: RecordedRequest): MockResponse = MockResponse()
                        .setHeader("Content-Type", "video/mp4").setBody(Buffer().write(bytes))
                }
                server.start()
                val channels = listOf(
                    Channel("pip-fixture-one", "Synthetic PiP one", "Local PiP", server.url("/one.mp4").toString()),
                    Channel("pip-fixture-two", "Synthetic PiP two", "Local PiP", server.url("/two.mp4").toString()),
                )
                runBlocking { app.database.channels().replaceSource(SourceEntity("pip-local-test", "Local PiP", updatedAt = 1, count = 2), channels) }
                test(channels)
            }
        } finally {
            setPipPermission("allow")
            runBlocking { app.settings.automaticPictureInPicture(false); app.settings.allowHttp(false) }
        }
    }

    // Generic key injection does not require the unrelated accessibility content-
    // changed event that pressHome()/pressBack() wait for. Native postconditions
    // below still have to prove the actual Home/Back behavior, with no fake callbacks.
    private fun intent(channel: Channel) = Intent(app, PlaybackActivity::class.java)
        .putExtra(PlaybackActivity.CHANNEL_ID, channel.id).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)

    private fun clickManualPip(activity: PlaybackActivity) {
        val button = device.wait(Until.findObject(By.descContains("پخش در پنجرهٔ شناور").enabled(true)), 10_000)
        if (button == null) {
            val state = main {
                val controller = PlaybackActivity::class.java.getDeclaredField("pip").apply { isAccessible = true }.get(activity)
                val available = controller.javaClass.getDeclaredMethod("getAvailable").invoke(controller)
                "entry=$available; lifecycle=${activity.lifecycle.currentState}; focus=${activity.hasWindowFocus()}; " +
                    "video=${view(activity)?.player?.videoSize}"
            }
            fail("An enabled, user-visible PiP button is required; $state; ${systemUiSummary()}")
        }
        button!!.click() // UI Automator, not Espresso synchronization on a paused PiP Activity
    }

    private fun actionFor(activity: PlaybackActivity, playing: Boolean): RemoteAction = main {
        // Check our own private PendingIntent factory, not API-33-only framework
        // getters on an API-31 device. Actual actions below are tapped in system UI.
        val controller = PlaybackActivity::class.java.getDeclaredField("pip").apply { isAccessible = true }.get(activity)
        controller.javaClass.getDeclaredMethod("playPauseAction", java.lang.Boolean.TYPE).apply { isAccessible = true }
            .invoke(controller, playing) as RemoteAction
    }

    private fun clickSystemPipAction(activity: PlaybackActivity, description: String) {
        val selector = By.desc(description)
        repeat(3) {
            device.findObject(selector)?.let { it.click(); return }
            val bounds = screenBounds(activity)
            device.click(bounds.centerX(), bounds.centerY())
            device.wait(Until.findObject(selector), 2_000)?.let { it.click(); return }
        }
        fail("Android's PiP menu did not expose the playback action; bounds=${screenBounds(activity)}; ${systemUiSummary()}")
    }

    private fun clickSystemPipControl(activity: PlaybackActivity, id: String) {
        val selector = By.res(Pattern.compile(".*:id/$id"))
        repeat(3) {
            device.findObject(selector)?.let { it.click(); return }
            val bounds = screenBounds(activity)
            assertFalse("PiP must have on-screen bounds", bounds.isEmpty)
            device.click(bounds.centerX(), bounds.centerY())
            device.wait(Until.findObject(selector), 2_000)?.let { it.click(); return }
        }
        fail("Android's PiP menu did not expose $id; bounds=${screenBounds(activity)}; ${systemUiSummary()}")
    }

    private fun screenBounds(activity: PlaybackActivity): Rect = main {
        val decor = activity.window.decorView
        val location = IntArray(2)
        decor.getLocationOnScreen(location)
        Rect(location[0], location[1], location[0] + decor.width, location[1] + decor.height)
    }

    private fun assertContinuousPip(activity: PlaybackActivity, engine: ExoPlayer, millis: Long) {
        val position = main { engine.currentPosition }
        val frames = main { engine.videoDecoderCounters?.also { it.ensureUpdated() }?.renderedOutputBufferCount ?: 0 }
        val until = SystemClock.elapsedRealtime() + millis
        while (SystemClock.elapsedRealtime() < until) {
            main {
                assertTrue(activity.isInPictureInPictureMode)
                assertSame(engine, view(activity)!!.player)
                assertNull(engine.playerError)
                assertTrue("Video/audio must keep playing while the Activity is paused in PiP", engine.isPlaying)
            }
            SystemClock.sleep(250)
        }
        assertTrue(main { engine.currentPosition > position + millis / 2 })
        assertTrue(main { (engine.videoDecoderCounters?.also { it.ensureUpdated() }?.renderedOutputBufferCount ?: 0) > frames })
    }

    private fun waitForVideo(activity: PlaybackActivity) {
        waitFor("Synthetic video/audio did not render", 25_000) {
            main {
                val engine = view(activity)?.player as? ExoPlayer
                assertNull(engine?.playerError)
                engine?.videoDecoderCounters?.ensureUpdated()
                engine?.audioDecoderCounters?.ensureUpdated()
                engine?.isPlaying == true && (engine.videoDecoderCounters?.renderedOutputBufferCount ?: 0) > 0 &&
                    (engine.audioDecoderCounters?.renderedOutputBufferCount ?: 0) > 0
            }
        }
        // A fresh emulator shows Android's one-time immersive-fullscreen tutorial
        // above the app. It steals focus and consumes Back; it is NOT a PiP or
        // runtime permission. Acknowledge only this known system education button.
        device.wait(Until.findObject(By.pkg("android").res("android", "ok").text("Got it")), 2_000)?.click()
        waitFor("Fullscreen player must own input focus before a real UI action", 5_000) {
            main { activity.hasWindowFocus() }
        }
    }

    private fun client(activity: PlaybackActivity): OkHttpClient = main {
        PlaybackActivity::class.java.getDeclaredField("playbackClient").apply { isAccessible = true }.get(activity) as OkHttpClient
    }

    private fun waitForCleanup(client: OkHttpClient) = waitFor("PiP client cleanup did not complete on IO", 15_000) {
        client.connectionPool.connectionCount() == 0 && client.dispatcher.executorService.isShutdown
    }

    private fun setPipPermission(mode: String) {
        require(mode in listOf("allow", "ignore"))
        val descriptor = instrumentation.uiAutomation.executeShellCommand("cmd appops set ${app.packageName} PICTURE_IN_PICTURE $mode")
        ParcelFileDescriptor.AutoCloseInputStream(descriptor).bufferedReader().use { stream ->
            val text = stream.readText()
            assertFalse("Cannot configure isolated emulator PiP permission", text.contains("Error", ignoreCase = true))
        }
    }

    private fun waitFor(message: String, timeout: Long = 15_000, condition: () -> Boolean) {
        val until = SystemClock.elapsedRealtime() + timeout
        while (SystemClock.elapsedRealtime() < until) {
            if (condition()) return
            SystemClock.sleep(100)
        }
        fail("$message; ${systemUiSummary()}")
    }

    private fun systemUiSummary(): String = "display=${device.displayWidth}x${device.displayHeight}; " +
        "foreground=${device.currentPackageName}; " +
        device.findObjects(By.clickable(true)).take(18)
            .joinToString { "${it.resourceName} desc=${it.contentDescription} text=${it.text} enabled=${it.isEnabled} ${it.visibleBounds}" }

    private fun strictMain(test: () -> Unit) {
        val previous = main { StrictMode.getThreadPolicy().also { StrictMode.setThreadPolicy(StrictMode.ThreadPolicy.Builder(it).detectNetwork().penaltyDeathOnNetwork().build()) } }
        try { test() } finally { main { StrictMode.setThreadPolicy(previous) } }
    }

    private fun <T> main(block: () -> T): T {
        var value: T? = null
        instrumentation.runOnMainSync { value = block() }
        @Suppress("UNCHECKED_CAST")
        return value as T
    }

    private fun view(activity: PlaybackActivity) = playerView(activity.window.decorView)
    private fun playerView(view: View): PlayerView? {
        if (view is PlayerView) return view
        if (view is ViewGroup) for (index in 0 until view.childCount) playerView(view.getChildAt(index))?.let { return it }
        return null
    }
}
