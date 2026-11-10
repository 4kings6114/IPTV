package io.github.fourkings6114.iptv.network

import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

class CleartextDisabledException : IOException("HTTP is disabled by the user policy")
class PlaylistTooLargeException : IOException("Playlist exceeds the 8 MiB limit")
class SourceHttpException(val status: Int) : IOException("Source returned HTTP $status")

/** No trust-all TLS, global stream headers, cookies, analytics, or request logging. */
object NetworkClients {
    fun create(
        allowHttp: () -> Boolean,
        origin: HttpUrl? = null,
        headers: Map<String, String> = emptyMap(),
        playback: Boolean = false,
    ): OkHttpClient {
        val policy = Interceptor { chain ->
            val request = chain.request()
            if (!request.url.isHttps && !allowHttp()) throw CleartextDisabledException()
            chain.proceed(request)
        }
        return OkHttpClient.Builder()
            .connectTimeout(12, TimeUnit.SECONDS)
            .readTimeout(if (playback) 20 else 15, TimeUnit.SECONDS)
            .apply { if (!playback) callTimeout(30, TimeUnit.SECONDS) }
            .addInterceptor(policy)
            // Network interceptors also run for redirects and every HLS/DASH child request.
            .addNetworkInterceptor(policy)
            .addNetworkInterceptor(ScopedHeadersInterceptor(origin, headers))
            .build()
    }
}

/** Never forward playlist-provided headers to a different scheme/host/port. */
class ScopedHeadersInterceptor(
    private val origin: HttpUrl?,
    private val headers: Map<String, String>,
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val builder = request.newBuilder()
        // Remove inherited headers even when OkHttp follows a redirect.
        headers.keys.forEach(builder::removeHeader)
        if (origin != null && sameOrigin(origin, request.url)) {
            headers.forEach { (name, value) -> builder.header(name, value) }
        }
        return chain.proceed(builder.build())
    }
}

fun sameOrigin(first: HttpUrl, second: HttpUrl): Boolean =
    first.scheme == second.scheme && first.host == second.host && first.port == second.port

const val MAX_PLAYLIST_BYTES = 8 * 1024 * 1024

fun InputStream.readPlaylist(): String {
    val output = ByteArrayOutputStream()
    val buffer = ByteArray(8192)
    var total = 0
    while (true) {
        val read = read(buffer)
        if (read < 0) break
        total += read
        if (total > MAX_PLAYLIST_BYTES) throw PlaylistTooLargeException()
        output.write(buffer, 0, read)
    }
    return output.toString("UTF-8")
}

suspend fun Call.awaitResponse(): Response = suspendCancellableCoroutine { continuation ->
    continuation.invokeOnCancellation { cancel() }
    enqueue(object : Callback {
        override fun onFailure(call: Call, e: IOException) {
            if (continuation.isActive) continuation.resumeWithException(e)
        }
        override fun onResponse(call: Call, response: Response) {
            continuation.resume(response) { _, value, _ -> value.close() }
        }
    })
}

data class DownloadedPlaylist(val text: String, val finalUrl: String)

suspend fun OkHttpClient.downloadPlaylist(url: String): DownloadedPlaylist {
    val request = Request.Builder().url(url).header("User-Agent", "IPTVHamrah/0.1 (Android)").build()
    return newCall(request).awaitResponse().use { response ->
        if (!response.isSuccessful) throw SourceHttpException(response.code)
        val body = response.body ?: throw IOException("Empty playlist response")
        if (body.contentLength() > MAX_PLAYLIST_BYTES) throw PlaylistTooLargeException()
        DownloadedPlaylist(body.byteStream().use { it.readPlaylist() }, response.request.url.toString())
    }
}
