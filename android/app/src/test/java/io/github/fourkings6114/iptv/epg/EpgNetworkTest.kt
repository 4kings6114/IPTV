package io.github.fourkings6114.iptv.epg

import android.app.Application
import io.github.fourkings6114.iptv.network.CleartextDisabledException
import io.github.fourkings6114.iptv.network.NetworkClients
import io.github.fourkings6114.iptv.network.SourceHttpException
import io.github.fourkings6114.iptv.ui.epgFailureMessage
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.SSLException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.Call
import okhttp3.EventListener
import okhttp3.OkHttpClient
import okhttp3.Response
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import okio.Buffer
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33], application = Application::class)
class EpgNetworkTest {
    private val allowHttp = AtomicBoolean(true)
    private val server = MockWebServer()
    private lateinit var client: OkHttpClient

    @Before fun prepare() { client = NetworkClients.create({ allowHttp.get() }); server.start() }
    @After fun close() {
        server.shutdown()
        client.connectionPool.evictAll()
        client.dispatcher.executorService.shutdown()
    }

    @Test fun guideRequestsUseNoStreamCredentialsAndDecodeHttpAndFileGzipExplicitly() = runBlocking {
        val plain = guideXml().toByteArray()
        listOf(gzip(plain), gzip(gzip(plain))).forEach { bytes ->
            server.enqueue(MockResponse().setHeader("Content-Encoding", "gzip").setBody(Buffer().write(bytes)))
            val guide = client.downloadEpg(server.url("/guide.xml.gz?token=synthetic").toString(), setOf("Exact.ID"), GUIDE_NOW)
            assertEquals(2, guide.programmes.size)
            val request = server.takeRequest(5, TimeUnit.SECONDS)!!
            assertEquals("identity", request.getHeader("Accept-Encoding"))
            assertNull(request.getHeader("Authorization"))
            assertNull(request.getHeader("Cookie"))
            assertNull(request.getHeader("Referer"))
            assertNull(request.getHeader("Origin"))
        }
    }

    @Test fun disabledHttpGuideNeverReachesTheServer() = runBlocking {
        allowHttp.set(false)
        expectEpgFailure<CleartextDisabledException> { client.downloadEpg(server.url("/guide").toString(), setOf("Exact.ID"), GUIDE_NOW) }
        assertEquals(0, server.requestCount)
    }

    @Test fun guideHttpsValidationIsNotBypassedAndDowngradeRedirectsStillObeyHttpPolicy() = runBlocking {
        MockWebServer().use { tls ->
            val certificate = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
            val serverCertificates = HandshakeCertificates.Builder().heldCertificate(certificate).build()
            tls.useHttps(serverCertificates.sslSocketFactory(), false)
            tls.start()
            expectEpgFailure<SSLException> { client.downloadEpg(tls.url("/guide").toString(), setOf("Exact.ID"), GUIDE_NOW) }
            assertEquals(0, tls.requestCount)
            val testTrust = HandshakeCertificates.Builder().addTrustedCertificate(certificate.certificate).build()
            val trustedTestClient = client.newBuilder().sslSocketFactory(testTrust.sslSocketFactory(), testTrust.trustManager).build()
            allowHttp.set(false)
            tls.enqueue(MockResponse().setResponseCode(302).setHeader("Location", server.url("/must-not-request")))
            expectEpgFailure<CleartextDisabledException> { trustedTestClient.downloadEpg(tls.url("/guide").toString(), setOf("Exact.ID"), GUIDE_NOW) }
            assertEquals(0, server.requestCount)
        }
    }

    @Test fun guideByteLimitsApplyToChunkedAndDeclaredLargeBodies() = runBlocking {
        server.enqueue(MockResponse().setChunkedBody(Buffer().write(gzip(guideXml().toByteArray())), 7))
        assertEquals(EpgException.Reason.LIMIT, expectEpgFailure<EpgException> {
            client.downloadEpg(server.url("/chunked").toString(), setOf("Exact.ID"), GUIDE_NOW, EpgLimits(downloadBytes = 32))
        }.reason)
        server.enqueue(MockResponse().setBody("small").setHeader("Content-Length", 9 * 1024 * 1024))
        assertEquals(EpgException.Reason.LIMIT, expectEpgFailure<EpgException> {
            client.downloadEpg(server.url("/declared-large").toString(), setOf("Exact.ID"), GUIDE_NOW)
        }.reason)
    }

    @Test fun guideCancellationStillWorksAfterResponseHeadersWereReceived() = runBlocking {
        val headers = CountDownLatch(1)
        val observing = client.newBuilder().eventListener(object : EventListener() {
            override fun responseHeadersEnd(call: Call, response: Response) { headers.countDown() }
        }).build()
        server.enqueue(MockResponse().setBody(guideXml()).setBodyDelay(2, TimeUnit.SECONDS))
        val download = async(Dispatchers.IO) { observing.downloadEpg(server.url("/slow").toString(), setOf("Exact.ID"), GUIDE_NOW) }
        assertTrue(headers.await(5, TimeUnit.SECONDS))
        withTimeout(5000) { download.cancelAndJoin() }
        assertTrue(download.isCancelled)
    }

    @Test fun guideErrorsContainOnlyFixedMessagesNeverUrlsTokensOrParserText() = runBlocking {
        server.enqueue(MockResponse().setResponseCode(401).setBody("PRIVATE_TOKEN https://private.invalid"))
        val failure = expectEpgFailure<SourceHttpException> {
            client.downloadEpg(server.url("/guide?token=PRIVATE_TOKEN").toString(), setOf("Exact.ID"), GUIDE_NOW)
        }
        assertEquals(401, failure.status)
        val exceptions = EpgException.Reason.entries.map { EpgException(it) } + listOf(failure,
            IOException("https://private.invalid?token=PRIVATE_TOKEN"), IllegalArgumentException("PRIVATE_TOKEN"),
            SSLException("PRIVATE_TOKEN"), java.util.zip.ZipException("PRIVATE_TOKEN"))
        exceptions.forEach {
            val message = epgFailureMessage(it)
            assertFalse(message.contains("PRIVATE_TOKEN"))
            assertFalse(message.contains("private.invalid"))
            assertTrue(message.isNotBlank())
        }
    }
}
