package io.github.fourkings6114.iptv.ui

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import io.github.fourkings6114.iptv.IPTVApplication
import io.github.fourkings6114.iptv.core.PlaylistParseException
import io.github.fourkings6114.iptv.network.CleartextDisabledException
import io.github.fourkings6114.iptv.network.PlaylistTooLargeException
import io.github.fourkings6114.iptv.network.SourceHttpException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class MainViewModel(application: Application) : AndroidViewModel(application) {
    private val app = application as IPTVApplication
    private val repository = app.repository
    private val dao = repository.dao
    val settings = app.settings.state
    private val selectedSource = settings.filter { it.loaded }.map { it.sourceId }.distinctUntilChanged()
    val channels = selectedSource.flatMapLatest { dao.observeSource(it) }.map { rows -> rows.map { it.channel() } }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val availableSources = dao.observeSources()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val source = selectedSource.flatMapLatest { dao.observeSourceInfo(it) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), null)
    val favorites = dao.observeFavorites().map { rows -> rows.map { it.channel() } }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val favoriteIds = dao.observeFavoriteIds().map { it.toSet() }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptySet())
    val history = dao.observeHistory().map { rows -> rows.map { it.channel() } }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val busy = MutableStateFlow(false)
    val error = MutableStateFlow<String?>(null)
    val notice = MutableStateFlow<String?>(null)
    private var activeLoad: Job? = null

    init {
        viewModelScope.launch { selectedSource.collect { load(it, force = false) } }
    }

    private fun load(id: String, force: Boolean) {
        activeLoad?.cancel()
        activeLoad = viewModelScope.launch {
            busy.value = true
            error.value = null
            try {
                if (force) repository.refresh(id) else repository.ensureSource(id)
                if (force) notice.value = "فهرست به‌روز شد؛ این نتیجه، تأیید پخش کانال‌ها نیست."
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (failure: Exception) {
                error.value = sourceError(failure)
            } finally { busy.value = false }
        }
    }

    fun selectSource(id: String) {
        if (!busy.value) viewModelScope.launch { app.settings.selectSource(id) }
    }
    fun refresh() { if (!busy.value) load(settings.value.sourceId, force = true) }
    fun toggleFavorite(id: String) { viewModelScope.launch { dao.toggleFavorite(id) } }
    fun clearHistory() {
        viewModelScope.launch { dao.clearHistory(); notice.value = "سابقهٔ پخش روی این گوشی پاک شد." }
    }
    fun showLogos(show: Boolean) { viewModelScope.launch { app.settings.showLogos(show) } }
    fun allowHttp(allow: Boolean, onDone: () -> Unit = {}) {
        viewModelScope.launch {
            app.settings.allowHttp(allow)
            settings.first { it.allowHttp == allow }
            onDone()
        }
    }
    fun dismissNotice() { notice.value = null }

    fun importUrl(url: String) = importSource("custom") { repository.importUrl(url) }
    fun importFile(uri: Uri) = importSource("file") { repository.importFile(uri) }

    private fun importSource(id: String, action: suspend () -> Unit) {
        if (busy.value) return
        activeLoad?.cancel()
        activeLoad = viewModelScope.launch {
            busy.value = true
            error.value = null
            try {
                action()
                notice.value = "فهرست وارد شد. اطلاعات و علاقه‌مندی‌ها فقط روی گوشی ذخیره می‌شوند."
                app.settings.selectSource(id)
            } catch (cancelled: CancellationException) { throw cancelled
            } catch (failure: Exception) { error.value = sourceError(failure)
            } finally { busy.value = false }
        }
    }
}

fun sourceError(error: Exception): String = when (error) {
    is CleartextDisabledException -> "این منبع از HTTP بدون رمزنگاری استفاده می‌کند. برای منابع مورداعتماد می‌توانی آن را در تنظیمات مجاز کنی."
    is SourceHttpException -> when (error.status) {
        401, 403 -> "سرور اجازهٔ دریافت فهرست را نداد (${error.status}). مجوز، اعتبار لینک و محدودیت منبع را بررسی کن."
        404 -> "فهرست در این آدرس پیدا نشد (404)."
        429 -> "درخواست‌ها بیش از حد بوده‌اند (429). کمی بعد دوباره امتحان کن."
        else -> "دریافت فهرست با خطای HTTP ${error.status} متوقف شد."
    }
    is PlaylistTooLargeException -> "فهرست از سقف ۸ مگابایت بزرگ‌تر است."
    is PlaylistParseException -> when (error.reason) {
        PlaylistParseException.Reason.HLS_MANIFEST -> "این فایل، استریم HLS است نه فهرست کانال‌های IPTV. لینک فهرست M3U را وارد کن."
        PlaylistParseException.Reason.EMPTY -> "کانال معتبر HTTP یا HTTPS در این فایل پیدا نشد. فهرست قبلی حفظ شده است."
        PlaylistParseException.Reason.TOO_MANY_CHANNELS -> "این نسخه حداکثر ۲۰ هزار کانال در هر فهرست می‌پذیرد."
    }
    is IllegalArgumentException -> "لینک معتبر HTTP یا HTTPS وارد کن؛ نام کاربری و رمز داخل بخش میزبان پذیرفته نمی‌شود."
    is SocketTimeoutException -> "دریافت فهرست طول کشید. اتصال را بررسی کن و دوباره تلاش کن."
    is UnknownHostException -> "نام سرور پیدا نشد. اینترنت، DNS و آدرس منبع را بررسی کن."
    is SSLException -> "اتصال امن منبع تأیید نشد. تاریخ گوشی و گواهی سرور را بررسی کن؛ بررسی گواهی غیرفعال نمی‌شود."
    else -> "دریافت فهرست انجام نشد. اتصال و منبع را بررسی کن؛ فهرست ذخیره‌شده حفظ شده است."
}
