package io.github.fourkings6114.iptv.ui

import io.github.fourkings6114.iptv.data.SourceMissingException
import io.github.fourkings6114.iptv.epg.EpgException
import io.github.fourkings6114.iptv.network.CleartextDisabledException
import io.github.fourkings6114.iptv.network.SourceHttpException
import java.net.SocketTimeoutException
import java.util.zip.ZipException
import javax.net.ssl.SSLException

/** Fixed, URL/token/XML-free messages; never display Throwable.message. */
fun epgFailureMessage(failure: Exception): String = when (failure) {
    is EpgException -> when (failure.reason) {
        EpgException.Reason.INVALID_XML -> "فایل XMLTV معتبر نیست یا ساختار XML پشتیبانی‌نشده دارد؛ راهنمای سالم قبلی حفظ شد."
        EpgException.Reason.LIMIT -> "راهنما از سقف این نسخه بزرگ‌تر است: ۸ مگابایت دریافت، ۳۲ مگابایت پس از بازشدن و حداکثر ۲۰ هزار برنامهٔ ذخیره‌شده. راهنمای قبلی حفظ شد."
        EpgException.Reason.NO_PROGRAMMES -> "فایل XMLTV برنامه‌ای ندارد؛ راهنمای قبلی حفظ شد."
        EpgException.Reason.NO_MATCH -> "برای شناسه‌های این فهرست، برنامهٔ قابل‌استفاده در راهنما پیدا نشد. تطابق دقیق TVG ID و تاریخ فایل را بررسی کن؛ دادهٔ سالم قبلی حفظ شد."
        EpgException.Reason.NO_IDS -> "کانال‌های این فهرست شناسهٔ TVG ندارند؛ بدون شناسهٔ منطبق، راهنما نمایش داده نمی‌شود."
        EpgException.Reason.NOT_CONFIGURED -> "ابتدا لینک XMLTV را برای همین فهرست تنظیم کن."
        EpgException.Reason.SOURCE_CHANGED -> "فهرست یا تنظیم راهنما تغییر کرده است؛ نتیجهٔ دریافت قبلی کنار گذاشته شد."
        EpgException.Reason.CACHE_IO -> "ذخیرهٔ راهنما انجام نشد؛ فضای خالی گوشی را بررسی کن."
    }
    is CleartextDisabledException -> "HTTP برای راهنما هم خاموش است. فقط برای منبع مورداعتماد، اجازهٔ HTTP را در تنظیمات روشن کن."
    is SourceHttpException -> "دریافت راهنما با خطای HTTP ${failure.status} متوقف شد؛ اعتبار لینک و دسترسی منبع را بررسی کن."
    is SourceMissingException -> "این فهرست دیگر موجود نیست؛ تنظیم راهنما برای فهرست حذف‌شده ذخیره نمی‌شود."
    is SocketTimeoutException -> "دریافت راهنما طول کشید؛ اتصال را بررسی کن و دوباره تلاش کن."
    is SSLException -> "اتصال امن راهنما تأیید نشد؛ تاریخ گوشی و گواهی سرور را بررسی کن. بررسی HTTPS غیرفعال نمی‌شود."
    is ZipException -> "فایل فشردهٔ راهنما سالم نیست؛ نسخهٔ XML یا gzip معتبر را امتحان کن."
    is IllegalArgumentException -> "لینک معتبر HTTP یا HTTPS وارد کن؛ نام کاربری و رمز در بخش میزبان پذیرفته نمی‌شود."
    else -> "دریافت یا ذخیرهٔ راهنما انجام نشد؛ اتصال و فضای خالی را بررسی کن. راهنمای سالم قبلی حفظ شده است."
}
