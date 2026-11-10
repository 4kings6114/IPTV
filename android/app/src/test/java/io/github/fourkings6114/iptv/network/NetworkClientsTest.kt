package io.github.fourkings6114.iptv.network

import java.io.ByteArrayInputStream
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.SSLHandshakeException
import okhttp3.Request
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.Assert.*
import org.junit.Test

class NetworkClientsTest {
    @Test fun httpIsBlockedBeforeSendingARequestByDefault() {
        MockWebServer().use { server ->
            server.start()
            val client = NetworkClients.create({ false })
            assertThrows(CleartextDisabledException::class.java) { client.newCall(Request.Builder().url(server.url("/playlist")).build()).execute() }
            assertNull(server.takeRequest(100, TimeUnit.MILLISECONDS))
        }
    }
    @Test fun optedInHttpWorksAndPolicyAppliesToLaterSegments() {
        MockWebServer().use { server ->
            server.enqueue(MockResponse().setBody("manifest"))
            val allow = AtomicBoolean(true)
            val client = NetworkClients.create({ allow.get() })
            client.newCall(Request.Builder().url(server.url("/manifest")).build()).execute().use { assertEquals(200, it.code) }
            allow.set(false)
            assertThrows(CleartextDisabledException::class.java) { client.newCall(Request.Builder().url(server.url("/segment.ts")).build()).execute() }
            assertEquals(1, server.requestCount)
        }
    }
    @Test fun credentialsAreNotForwardedToRedirectOrigin() {
        MockWebServer().use { first ->
            MockWebServer().use { second ->
                first.start(); second.start()
                first.enqueue(MockResponse().setResponseCode(302).addHeader("Location", second.url("/cdn")))
                second.enqueue(MockResponse().setBody("ok"))
                val client = NetworkClients.create({ true }, first.url("/manifest"), mapOf("Authorization" to "Bearer test", "Cookie" to "account=test", "Referer" to "https://source.test/"))
                client.newCall(Request.Builder().url(first.url("/manifest")).build()).execute().close()
                val sourceRequest = first.takeRequest(1, TimeUnit.SECONDS)!!
                val targetRequest = second.takeRequest(1, TimeUnit.SECONDS)!!
                assertEquals("Bearer test", sourceRequest.getHeader("Authorization"))
                assertEquals("account=test", sourceRequest.getHeader("Cookie"))
                assertNull(targetRequest.getHeader("Authorization"))
                assertNull(targetRequest.getHeader("Cookie"))
                assertNull(targetRequest.getHeader("Referer"))
            }
        }
    }
    @Test fun childResourcesUseHeadersOnlyAtTheSameOrigin() {
        MockWebServer().use { first ->
            MockWebServer().use { second ->
                first.enqueue(MockResponse().setBody("same origin"))
                second.enqueue(MockResponse().setBody("different origin"))
                val client = NetworkClients.create({ true }, first.url("/manifest"), mapOf("X-Token" to "test"))
                client.newCall(Request.Builder().url(first.url("/key")).build()).execute().close()
                client.newCall(Request.Builder().url(second.url("/segment")).build()).execute().close()
                assertEquals("test", first.takeRequest(1, TimeUnit.SECONDS)!!.getHeader("X-Token"))
                assertNull(second.takeRequest(1, TimeUnit.SECONDS)!!.getHeader("X-Token"))
            }
        }
    }
    @Test fun httpsRedirectCannotBypassTheHttpOptIn() {
        val certificate = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
        val serverCertificates = HandshakeCertificates.Builder().heldCertificate(certificate).build()
        val testTrust = HandshakeCertificates.Builder().addTrustedCertificate(certificate.certificate).build()
        MockWebServer().use { https ->
            MockWebServer().use { http ->
                https.useHttps(serverCertificates.sslSocketFactory(), false)
                https.start(); http.start()
                https.enqueue(MockResponse().setResponseCode(302).addHeader("Location", http.url("/cleartext")))
                // Only the test client trusts this locally generated test certificate.
                val client = NetworkClients.create({ false }).newBuilder()
                    .sslSocketFactory(testTrust.sslSocketFactory(), testTrust.trustManager).build()
                assertThrows(CleartextDisabledException::class.java) { client.newCall(Request.Builder().url(https.url("/manifest")).build()).execute() }
                assertEquals(1, https.requestCount)
                assertNull(http.takeRequest(100, TimeUnit.MILLISECONDS))
            }
        }
    }
    @Test fun productionClientDoesNotTrustSelfSignedCertificates() {
        val certificate = HeldCertificate.Builder().addSubjectAlternativeName("localhost").build()
        val serverCertificates = HandshakeCertificates.Builder().heldCertificate(certificate).build()
        MockWebServer().use { server ->
            server.useHttps(serverCertificates.sslSocketFactory(), false)
            server.start()
            val client = NetworkClients.create({ true })
            assertThrows(SSLHandshakeException::class.java) { client.newCall(Request.Builder().url(server.url("/manifest")).build()).execute() }
        }
    }
    @Test fun readsBoundedUtf8AndRejectsOversizedDecompressedData() {
        assertEquals("فارسی", ByteArrayInputStream("فارسی".toByteArray()).readPlaylist())
        assertThrows(PlaylistTooLargeException::class.java) { ByteArrayInputStream(ByteArray(MAX_PLAYLIST_BYTES + 1)).readPlaylist() }
    }
}
