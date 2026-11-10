package io.github.fourkings6114.iptv.player

import java.io.IOException
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import javax.net.SocketFactory
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import okhttp3.Call
import okhttp3.Callback
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
import org.junit.Assert.*
import org.junit.Test

class PlaybackClientCleanupTest {
    private fun fetch(client: OkHttpClient, server: MockWebServer) {
        client.newCall(Request.Builder().url(server.url("/media")).build()).execute().use {
            assertEquals("fixture", it.body!!.string())
        }
    }

    @Test fun releasesIdleConnectionsAndExecutorIdempotently() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        try {
            MockWebServer().use { server ->
                server.enqueue(MockResponse().setBody("fixture"))
                val client = OkHttpClient()
                fetch(client, server)
                assertEquals(1, client.connectionPool.idleConnectionCount())
                val cleanup = PlaybackClientCleanup(scope)
                withTimeout(10_000) { cleanup.release(client).join(); cleanup.release(client).join() }
                assertEquals(0, client.connectionPool.connectionCount())
                assertTrue(client.dispatcher.executorService.isShutdown)
            }
        } finally { scope.cancel() }
    }

    @Test fun socketCloseRunsOffTheCallingThread() = runBlocking {
        val closedOn = AtomicReference<Thread?>()
        val socketFactory = object : SocketFactory() {
            override fun createSocket(): Socket = object : Socket() {
                override fun close() { closedOn.set(Thread.currentThread()); super.close() }
            }
            private fun connected(host: InetAddress, port: Int, local: InetAddress? = null, localPort: Int = 0): Socket = createSocket().apply {
                if (local != null) bind(InetSocketAddress(local, localPort))
                connect(InetSocketAddress(host, port))
            }
            override fun createSocket(host: String, port: Int) = connected(InetAddress.getByName(host), port)
            override fun createSocket(host: InetAddress, port: Int) = connected(host, port)
            override fun createSocket(host: String, port: Int, local: InetAddress, localPort: Int) = connected(InetAddress.getByName(host), port, local, localPort)
            override fun createSocket(host: InetAddress, port: Int, local: InetAddress, localPort: Int) = connected(host, port, local, localPort)
        }
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        try {
            MockWebServer().use { server ->
                server.enqueue(MockResponse().setBody("fixture"))
                val client = OkHttpClient.Builder().socketFactory(socketFactory).build()
                fetch(client, server)
                closedOn.set(null)
                val caller = Thread.currentThread()
                withTimeout(10_000) { PlaybackClientCleanup(scope).release(client).join() }
                assertNotNull(closedOn.get())
                assertNotSame(caller, closedOn.get())
                assertTrue(client.dispatcher.executorService.isShutdown)
            }
        } finally { scope.cancel() }
    }

    @Test fun cancelsAnInFlightRequestAndStillShutsDownTheExecutor() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        try {
            MockWebServer().use { server ->
                server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
                val client = OkHttpClient()
                val finished = CountDownLatch(1)
                val call = client.newCall(Request.Builder().url(server.url("/waiting")).build())
                call.enqueue(object : Callback {
                    override fun onFailure(call: Call, e: IOException) { finished.countDown() }
                    override fun onResponse(call: Call, response: Response) { response.close(); finished.countDown() }
                })
                assertNotNull(server.takeRequest(5, TimeUnit.SECONDS))
                withTimeout(10_000) { PlaybackClientCleanup(scope).release(client).join() }
                assertTrue(call.isCanceled())
                assertTrue(finished.await(5, TimeUnit.SECONDS))
                assertTrue(client.dispatcher.executorService.isShutdown)
            }
        } finally { scope.cancel() }
    }

    @Test fun outgoingCleanupDoesNotCancelOrEvictTheNextIndependentClient() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        try {
            MockWebServer().use { server ->
                repeat(3) { server.enqueue(MockResponse().setBody("fixture")) }
                val outgoing = OkHttpClient()
                val incoming = OkHttpClient()
                val cleanup = PlaybackClientCleanup(scope)
                fetch(outgoing, server); fetch(incoming, server)
                withTimeout(10_000) { cleanup.release(outgoing).join() }
                assertEquals(0, outgoing.connectionPool.connectionCount())
                assertEquals(1, incoming.connectionPool.idleConnectionCount())
                assertFalse(incoming.dispatcher.executorService.isShutdown)
                fetch(incoming, server)
                withTimeout(10_000) { cleanup.release(incoming).join() }
            }
        } finally { scope.cancel() }
    }

    @Test fun queuedCleanupOutlivesCancellationOfTheActivityOwner() = runBlocking {
        val application = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val activity = CoroutineScope(Job() + Dispatchers.Default)
        val executor = Executors.newSingleThreadExecutor()
        val dispatcher = executor.asCoroutineDispatcher()
        val gate = CountDownLatch(1)
        val workerStarted = CountDownLatch(1)
        executor.execute { workerStarted.countDown(); gate.await(10, TimeUnit.SECONDS) }
        try {
            assertTrue(workerStarted.await(5, TimeUnit.SECONDS))
            MockWebServer().use { server ->
                server.enqueue(MockResponse().setBody("fixture"))
                val client = OkHttpClient()
                fetch(client, server)
                val cleanup = PlaybackClientCleanup(application, dispatcher)
                val scheduled = CompletableDeferred<Job>()
                activity.launch { scheduled.complete(cleanup.release(client)) }.join()
                val job = scheduled.await()
                activity.cancel() // model onDestroy before the IO worker can run
                assertFalse(job.isCancelled)
                assertEquals(1, client.connectionPool.idleConnectionCount())
                gate.countDown()
                withTimeout(10_000) { job.join() }
                assertEquals(0, client.connectionPool.connectionCount())
                assertTrue(client.dispatcher.executorService.isShutdown)
            }
        } finally {
            gate.countDown(); dispatcher.close(); activity.cancel(); application.cancel()
        }
    }
}
