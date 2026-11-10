package io.github.fourkings6114.iptv.player

import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
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
        // Teardown is best-effort, but an error in one step must not skip the others.
        // Never log exception messages, since they can contain private stream URLs.
        try { client.dispatcher.cancelAll() } catch (_: Exception) { }
        try { client.connectionPool.evictAll() } catch (_: Exception) { }
        try { client.dispatcher.executorService.shutdown() } catch (_: Exception) { }
    }
}
