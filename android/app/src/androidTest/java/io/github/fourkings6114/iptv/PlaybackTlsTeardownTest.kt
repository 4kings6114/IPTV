package io.github.fourkings6114.iptv

import android.content.Intent
import android.os.Build
import android.os.NetworkOnMainThreadException
import android.os.StrictMode
import android.os.SystemClock
import android.view.View
import android.view.ViewGroup
import androidx.lifecycle.Lifecycle
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.ui.PlayerView
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import io.github.fourkings6114.iptv.core.Channel
import io.github.fourkings6114.iptv.data.SourceEntity
import io.github.fourkings6114.iptv.network.NetworkClients
import io.github.fourkings6114.iptv.player.PlaybackActivity
import java.net.Socket
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.Call
import okhttp3.Connection
import okhttp3.ConnectionSpec
import okhttp3.EventListener
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Request
import okhttp3.TlsVersion
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import okio.Buffer
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/** Real Android Conscrypt sockets: the old HTTP-only smoke did not cover TLS close_notify. */
@RunWith(AndroidJUnit4::class)
@androidx.annotation.OptIn(UnstableApi::class)
class PlaybackTlsTeardownTest {
    private val app get() = ApplicationProvider.getApplicationContext<IPTVApplication>()
    private val instrumentation get() = InstrumentationRegistry.getInstrumentation()
    private val certificate = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
    private val serverCertificates = HandshakeCertificates.Builder().heldCertificate(certificate).build()
    private val testTrust = HandshakeCertificates.Builder().addTrustedCertificate(certificate.certificate).build()

    @Before fun prepare() {
        // A block body guarantees JVM void, as required by JUnit4; Flow.first returns Settings.
        runBlocking {
            assertTrue("Supported regression images are Android API 31 and 33", Build.VERSION.SDK_INT in listOf(31, 33))
            app.crashReports.clear()
            withTimeout(20_000) {
                app.settings.showLogos(false)
                app.settings.state.first { it.loaded && !it.showLogos }
            }
        }
    }

    private fun tlsProbe(base: OkHttpClient) = base.newBuilder()
        // Test-only, explicitly trusted localhost certificate, NOT trust-all or a global TLS override.
        .sslSocketFactory(testTrust.sslSocketFactory(), testTrust.trustManager)
        .protocols(listOf(Protocol.HTTP_1_1))
        .connectionSpecs(listOf(ConnectionSpec.Builder(ConnectionSpec.MODERN_TLS).tlsVersions(TlsVersion.TLS_1_2).build()))
        .build()

    @Test fun httpsPoolEvictionOnMainReproducesReportedCrash() {
        MockWebServer().use { server ->
            server.useHttps(serverCertificates.sslSocketFactory(), false)
            server.enqueue(MockResponse().setBody("TLS fixture"))
            server.start()
            val socket = AtomicReference<Socket?>()
            val client = tlsProbe(NetworkClients.create({ false })).newBuilder()
                .eventListener(object : EventListener() {
                    override fun connectionAcquired(call: Call, connection: Connection) { socket.set(connection.socket()) }
                }).build()
            try {
                client.newCall(Request.Builder().url(server.url("/probe")).build()).execute().use {
                    assertEquals("TLS fixture", it.body!!.string())
                }
                assertEquals(1, client.connectionPool.idleConnectionCount())
                var observed: Throwable? = null
                strictNetworkOnMain {
                    instrumentation.runOnMainSync {
                        // Negative control: deliberately run 0.1.1's faulty operation and catch it
                        // inside this test callback, so the instrumentation process is not killed.
                        try { client.connectionPool.evictAll() } catch (failure: Throwable) { observed = failure }
                    }
                }
                assertTrue("Old main-thread TLS eviction must reproduce the user's exception; got ${observed?.javaClass?.name}", observed is NetworkOnMainThreadException)
                assertNull(app.crashReports.read())
            } finally {
                runBlocking(Dispatchers.IO) {
                    try { socket.get()?.close() } finally {
                        client.connectionPool.evictAll()
                        client.dispatcher.executorService.shutdown()
                    }
                }
            }
        }
    }

    @Test fun httpsPooledSocketsSurvivePlayerStopResumeAndDestroyWithStrictMode() {
        val fixture = instrumentation.context.assets.open("stability.mp4").use { it.readBytes() }
        MockWebServer().use { media ->
            MockWebServer().use { tls ->
                media.dispatcher = object : Dispatcher() {
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
                media.start()
                tls.useHttps(serverCertificates.sslSocketFactory(), false)
                repeat(2) { tls.enqueue(MockResponse().setBody("TLS fixture")) }
                tls.start()
                val channel = Channel("tls-teardown-fixture", "Synthetic teardown test", "Local test", media.url("/stability.mp4").toString())
                runBlocking {
                    withTimeout(20_000) {
                        app.settings.automaticPictureInPicture(false)
                        app.settings.allowHttp(true) // loopback media only, not a production policy change
                        app.settings.state.first { it.allowHttp }
                        app.database.channels().replaceSource(SourceEntity("tls-test", "Local test", updatedAt = 1, count = 1), listOf(channel))
                    }
                }
                val intent = Intent(app, PlaybackActivity::class.java).putExtra(PlaybackActivity.CHANNEL_ID, channel.id)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                ActivityScenario.launch<PlaybackActivity>(intent).use { scenario ->
                    waitForVideo(scenario)
                    val first = playbackClient(scenario)
                    addIdleTlsConnection(first, tls)
                    strictNetworkOnMain {
                        scenario.moveToState(Lifecycle.State.CREATED) // actual onStop/releasePlayer call site
                        waitForCleanup(first)
                        scenario.moveToState(Lifecycle.State.RESUMED)
                        waitForVideo(scenario)
                        val second = playbackClient(scenario)
                        assertNotSame(first, second)
                        assertNotSame(first.connectionPool, second.connectionPool)
                        assertFalse(second.dispatcher.executorService.isShutdown)
                        addIdleTlsConnection(second, tls)
                        scenario.close() // cleanup must outlive onDestroy/lifecycleScope cancellation
                        waitForCleanup(second)
                    }
                    assertNull(app.crashReports.read())
                }
            }
        }
    }

    private fun addIdleTlsConnection(base: OkHttpClient, server: MockWebServer) {
        val probe = tlsProbe(base)
        assertSame(base.connectionPool, probe.connectionPool)
        assertSame(base.dispatcher, probe.dispatcher)
        // Seed an idle Conscrypt TLS socket into the ACTUAL player's pool. The test-only
        // clone is the only client trusting the localhost certificate; app TLS is untouched.
        probe.newCall(Request.Builder().url(server.url("/probe")).build()).execute().use {
            assertEquals("TLS fixture", it.body!!.string())
        }
        assertTrue(base.connectionPool.idleConnectionCount() >= 1)
    }

    private fun playbackClient(scenario: ActivityScenario<PlaybackActivity>): OkHttpClient {
        var client: OkHttpClient? = null
        scenario.onActivity {
            // White-box instrumentation only: do not add a TLS bypass/test hook to shipped code.
            val field = PlaybackActivity::class.java.getDeclaredField("playbackClient").apply { isAccessible = true }
            client = field.get(it) as OkHttpClient
        }
        return client!!
    }

    private fun strictNetworkOnMain(block: () -> Unit) {
        var original: StrictMode.ThreadPolicy? = null
        instrumentation.runOnMainSync {
            original = StrictMode.getThreadPolicy()
            StrictMode.setThreadPolicy(StrictMode.ThreadPolicy.Builder(original!!).detectNetwork().penaltyDeathOnNetwork().build())
        }
        try { block() } finally { instrumentation.runOnMainSync { StrictMode.setThreadPolicy(original!!) } }
    }

    private fun waitForCleanup(client: OkHttpClient) {
        val deadline = SystemClock.elapsedRealtime() + 15_000
        while (SystemClock.elapsedRealtime() < deadline) {
            if (client.connectionPool.connectionCount() == 0 && client.dispatcher.executorService.isShutdown) return
            SystemClock.sleep(50)
        }
        fail("Outgoing client's pool/executor did not finish background cleanup")
    }

    private fun waitForVideo(scenario: ActivityScenario<PlaybackActivity>) {
        val deadline = SystemClock.elapsedRealtime() + 25_000
        while (SystemClock.elapsedRealtime() < deadline) {
            var playing = false
            scenario.onActivity {
                val engine = playerView(it.window.decorView)?.player as? ExoPlayer
                assertNull(engine?.playerError)
                engine?.videoDecoderCounters?.ensureUpdated()
                playing = engine?.isPlaying == true && (engine.videoDecoderCounters?.renderedOutputBufferCount ?: 0) > 0
            }
            if (playing) return
            SystemClock.sleep(150)
        }
        fail("Synthetic video did not render")
    }

    private fun playerView(view: View): PlayerView? {
        if (view is PlayerView) return view
        if (view is ViewGroup) for (index in 0 until view.childCount) playerView(view.getChildAt(index))?.let { return it }
        return null
    }
}
