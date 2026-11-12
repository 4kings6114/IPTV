package io.github.fourkings6114.iptv.player

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient

/**
 * Owns no Activity. The application scope outlives onStop/onDestroy and manual retry.
 * TLS close/cancel can write close_notify: even idle-pool eviction is network I/O.
 * Only pass an outgoing, exclusively owned playback client, never the shared catalog client.
 */
internal class PlaybackClientCleanup(
    private val applicationScope: CoroutineScope,
    private val dispatcher: CoroutineDispatcher = Dispatchers.IO,
) {
    fun release(client: OkHttpClient): Job = applicationScope.launch(dispatcher) {
        try {
            try { client.dispatcher.cancelAll() } catch (_: Exception) { }

            // A call can still be unwinding when the first eviction runs. If it releases
            // its socket into the pool just afterward, that connection would otherwise
            // survive as idle forever. Keep evicting while cancelled calls drain so the
            // final transition to idle is caught as well. This is bounded and runs only
            // on the application-owned IO dispatcher, never on the Activity's main thread.
            val deadline = System.nanoTime() + CLEANUP_DRAIN_TIMEOUT_NANOS
            while (true) {
                try { client.connectionPool.evictAll() } catch (_: Exception) { }
                if (client.dispatcher.runningCallsCount() == 0 && client.dispatcher.queuedCallsCount() == 0 &&
                    client.connectionPool.connectionCount() == 0) break
                if (System.nanoTime() >= deadline) break
                delay(CLEANUP_POLL_INTERVAL_MILLIS)
            }
        } finally {
            // Activity destruction cannot cancel this last attempt; even an unexpected
            // coroutine cancellation must still try to empty the private pool and stop
            // the client's executor.
            withContext(NonCancellable) {
                try { client.connectionPool.evictAll() } catch (_: Exception) { }
                try { client.dispatcher.executorService.shutdown() } catch (_: Exception) { }
            }
        }
    }

    private companion object {
        const val CLEANUP_DRAIN_TIMEOUT_NANOS = 10_000_000_000L
        const val CLEANUP_POLL_INTERVAL_MILLIS = 50L
    }
}
