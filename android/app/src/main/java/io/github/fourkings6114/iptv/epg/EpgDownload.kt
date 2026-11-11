package io.github.fourkings6114.iptv.epg

import io.github.fourkings6114.iptv.network.SourceHttpException
import java.io.IOException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.suspendCancellableCoroutine
import okhttp3.Call
import okhttp3.Callback
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

/** Parse on OkHttp's IO callback thread, with cancellation valid throughout body
 * reading AND parsing, not just until the response headers have been delivered.
 * This uses the app's normal TLS/redirect/HTTP policy, never media credentials.
 */
suspend fun OkHttpClient.downloadEpg(rawUrl: String, ids: Set<String>, now: Long,
                                   limits: EpgLimits = EpgLimits()): EpgGuide {
    val url = normalizeEpgUrl(rawUrl)
    if (ids.isEmpty()) throw EpgException(EpgException.Reason.NO_IDS)
    return suspendCancellableCoroutine { continuation ->
        val request = Request.Builder().url(url)
            .header("User-Agent", "IPTVHamrah/0.1 (Android)")
            // Own both compressed and expanded byte limits; don't let transparent
            // HTTP gzip decoding hide how many download bytes the response used.
            .header("Accept-Encoding", "identity")
            .header("Accept", "application/xml,text/xml,application/gzip")
            .build()
        val call = newCall(request)
        continuation.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (continuation.isActive) continuation.resumeWithException(e)
            }
            override fun onResponse(call: Call, response: Response) {
                try {
                    val guide = response.use {
                        if (!it.isSuccessful) throw SourceHttpException(it.code)
                        val body = it.body ?: throw EpgException(EpgException.Reason.INVALID_XML)
                        if (body.contentLength() > limits.downloadBytes) throw EpgException(EpgException.Reason.LIMIT)
                        XmlTvParser.parse(body.byteStream(), ids, now, limits) {
                            if (!continuation.isActive) throw CancellationException()
                        }
                    }
                    if (continuation.isActive) continuation.resume(guide)
                } catch (failure: Exception) {
                    if (continuation.isActive) continuation.resumeWithException(failure)
                }
            }
        })
    }
}
