package io.github.fourkings6114.iptv.player

import androidx.media3.common.PlaybackException
import androidx.media3.datasource.HttpDataSource
import io.github.fourkings6114.iptv.network.CleartextDisabledException
import javax.net.ssl.SSLException

data class PlaybackFailure(val message: String, val code: String)

@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
fun describePlaybackError(error: PlaybackException): PlaybackFailure {
    val causes = generateSequence(error as Throwable?) { it.cause }.take(16).toList()
    val status = causes.filterIsInstance<HttpDataSource.InvalidResponseCodeException>().firstOrNull()?.responseCode
    val message = when {
        causes.any { it is CleartextDisabledException } -> "یکی از درخواست‌های این استریم HTTP است. برای منبع مورداعتماد، اجازهٔ HTTP را در تنظیمات فعال کن."
        causes.any { it is SSLException } -> "اتصال امن این منبع تأیید نشد. تاریخ گوشی و گواهی سرور را بررسی کن؛ امنیت HTTPS غیرفعال نمی‌شود."
        status == 401 || status == 403 -> "منبع اجازهٔ پخش نداد ($status). اعتبار لینک، هدرها، مجوز یا محدودیت منطقه‌ای باید بررسی شود؛ تغییر موتور لزوماً کمکی نمی‌کند."
        status == 404 -> "آدرس رسانه در سرور پیدا نشد (404). فهرست را به‌روز کن یا کانال دیگری امتحان کن."
        status == 429 -> "منبع درخواست‌های زیاد را محدود کرده است (429). کمی بعد دوباره امتحان کن."
        status != null -> "سرور رسانه خطای HTTP $status داد. ممکن است مشکل موقت یا مربوط به خود منبع باشد."
        error.errorCode in 6000..6999 -> "این منبع به DRM یا مجوز معتبر نیاز دارد. این نسخه راهی برای دورزدن آن ندارد."
        error.errorCode in setOf(
            PlaybackException.ERROR_CODE_DECODER_INIT_FAILED,
            PlaybackException.ERROR_CODE_DECODING_FAILED,
            PlaybackException.ERROR_CODE_DECODING_FORMAT_UNSUPPORTED,
            PlaybackException.ERROR_CODE_DECODING_FORMAT_EXCEEDS_CAPABILITIES,
        ) -> "رمزگشایی این فرمت روی دستگاه انجام نشد. کد خطا را بفرست تا نیاز به کُدک صوتی یا موتور جایگزین بررسی شود."
        error.errorCode in setOf(
            PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_FAILED,
            PlaybackException.ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT,
        ) -> "اتصال به رسانه برقرار نشد یا طول کشید. اینترنت، DNS و دسترسی به منبع را بررسی کن."
        else -> "این رسانه پخش نشد. ممکن است قالب، لینک یا پاسخ منبع ناسازگار باشد. یک کانال دیگر را هم امتحان کن."
    }
    return PlaybackFailure(message, error.errorCodeName)
}
