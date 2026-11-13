package io.github.fourkings6114.iptv.ui

import android.app.Application
import android.net.Uri
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.viewModelScope
import io.github.fourkings6114.iptv.IPTVApplication
import io.github.fourkings6114.iptv.core.PlaylistParseException
import io.github.fourkings6114.iptv.core.persianNumber
import io.github.fourkings6114.iptv.core.sha256
import io.github.fourkings6114.iptv.epg.EpgSnapshot
import io.github.fourkings6114.iptv.data.BackupPreview
import io.github.fourkings6114.iptv.data.FileSourceSelectionRequiredException
import io.github.fourkings6114.iptv.data.InvalidSourceNameException
import io.github.fourkings6114.iptv.data.NotAFileSourceException
import io.github.fourkings6114.iptv.data.ProtectedSourceException
import io.github.fourkings6114.iptv.data.SAMPLE_SOURCE_ID
import io.github.fourkings6114.iptv.data.SourceMissingException
import io.github.fourkings6114.iptv.data.isBuiltInSource
import io.github.fourkings6114.iptv.data.sourceTitle
import io.github.fourkings6114.iptv.network.CleartextDisabledException
import io.github.fourkings6114.iptv.network.PlaylistTooLargeException
import io.github.fourkings6114.iptv.network.SourceHttpException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import javax.net.ssl.SSLException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.filter
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onStart
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class MainViewModel(application: Application, private val savedState: SavedStateHandle) : AndroidViewModel(application) {
    private val app = application as IPTVApplication
    private val repository = app.repository
    private val dao = repository.dao
    val settings = app.settings.state
    private val selectedSource = settings.filter { it.loaded }.map { it.sourceId }.distinctUntilChanged()
    val channels = selectedSource.flatMapLatest { dao.observeSource(it).onStart { emit(emptyList()) } }
        .map { rows -> rows.map { it.channel() } }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val epg = settings.filter { it.loaded }.map { it.sourceId to it.epgUrl }.distinctUntilChanged()
        .flatMapLatest { (id, url) -> app.epg.observe(id, url).onStart { emit(EpgSnapshot(id, sha256(url))) } }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), EpgSnapshot())
    val epgBusy = MutableStateFlow(false)
    val epgError = MutableStateFlow<String?>(null)
    private var epgJob: Job? = null
    private var epgSourceId: String? = null
    private var epgSequence = 0L
    val availableSources = dao.observeSources()
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val source = selectedSource.flatMapLatest { dao.observeSourceInfo(it).onStart { emit(null) } }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), null)
    val favorites = dao.observeFavorites().map { rows -> rows.map { it.channel() } }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val favoriteIds = dao.observeFavoriteIds().map { it.toSet() }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptySet())
    val history = dao.observeHistory().map { rows -> rows.map { it.channel() } }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), emptyList())
    val crashReport = MutableStateFlow<String?>(null)
    val busy = MutableStateFlow(false)
    val error = MutableStateFlow<String?>(null)
    val notice = MutableStateFlow<String?>(null)
    val backupBusy = MutableStateFlow(false)
    val backupPreview = MutableStateFlow<BackupPreview?>(null)

    init {
        viewModelScope.launch { crashReport.value = withContext(Dispatchers.IO) { app.crashReports.read() } }
        viewModelScope.launch {
            selectedSource.collect { id ->
                if (epgSourceId != null && epgSourceId != id) cancelEpgOperation()
                epgError.value = null
                try {
                    if (dao.source(id) == null) {
                        if (isBuiltInSource(id)) {
                            // A file result/import can arrive while preferences are first loading.
                            // Wait rather than losing the initial load because busy was true.
                            busy.first { !it }
                            if (settings.value.sourceId == id) runSourceOperation { repository.ensureSource(id) }
                        } else {
                            // Recover a process death between Room deletion and the DataStore update.
                            // Do not invent/re-download an absent personal source or erase saved channels.
                            app.epg.remove(id)
                            app.settings.forgetSource(id)
                            notice.value = "فهرست انتخاب‌شده موجود نیست؛ فهرست نمونه نمایش داده شد."
                        }
                    }
                } catch (cancelled: CancellationException) { throw cancelled
                } catch (failure: Exception) { error.value = sourceError(failure) }
            }
        }
    }

    fun createBackup(uri: Uri) {
        if (busy.value || epgBusy.value || backupBusy.value) {
            error.value = "پیش از پشتیبان‌گیری، عملیات جاری را تمام کن."
            return
        }
        backupBusy.value = true
        error.value = null
        viewModelScope.launch {
            try {
                val result = app.backupManager.create(uri)
                val skippedSources = result.preview.omittedSensitiveSources
                val skippedChannels = result.preview.omittedSensitiveChannels
                notice.value = if (skippedSources == 0 && skippedChannels == 0) "نسخهٔ پشتیبان ذخیره شد (${result.bytes / 1024} کیلوبایت)."
                else "نسخه ذخیره شد؛ ${persianNumber(skippedSources)} فهرست و ${persianNumber(skippedChannels)} کانال دارای نشانهٔ اطلاعات ورود صادر نشد."
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                error.value = "ساخت فایل پشتیبان انجام نشد؛ فضای ذخیره‌سازی و دسترسی فایل را بررسی کن."
            } finally {
                backupBusy.value = false
            }
        }
    }

    fun previewBackup(uri: Uri) {
        if (busy.value || epgBusy.value || backupBusy.value) {
            error.value = "پیش از انتخاب نسخهٔ پشتیبان، عملیات جاری را تمام کن."
            return
        }
        backupBusy.value = true
        error.value = null
        backupPreview.value = null
        viewModelScope.launch {
            try {
                backupPreview.value = app.backupManager.preview(uri)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                error.value = "فایل پشتیبان معتبر یا پشتیبانی‌شده نیست؛ داده‌های فعلی تغییری نکردند."
            } finally {
                backupBusy.value = false
            }
        }
    }

    fun dismissBackupPreview() { if (!backupBusy.value) backupPreview.value = null }

    fun restoreBackup() {
        val preview = backupPreview.value ?: return
        if (busy.value || epgBusy.value || backupBusy.value) {
            error.value = "پیش از بازیابی، عملیات جاری را تمام کن."
            return
        }
        busy.value = true
        backupBusy.value = true
        error.value = null
        viewModelScope.launch {
            try {
                app.backupManager.restore(preview.backup)
                backupPreview.value = null
                notice.value = "بازیابی کامل شد؛ فهرست‌ها، علاقه‌مندی‌ها، سابقه و تنظیمات جایگزین شدند."
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                error.value = "بازیابی انجام نشد؛ داده‌های قبلی حفظ شدند. فایل را بررسی کن و دوباره تلاش کن."
            } finally {
                busy.value = false
                backupBusy.value = false
            }
        }
    }

    fun backupPickerUnavailable() {
        error.value = "انتخاب‌گر فایل در این دستگاه در دسترس نیست."
    }

    /** Set busy before launch, so rapid clicks cannot start overlapping source mutations. */
    private fun runSourceOperation(action: suspend () -> Unit) {
        if (busy.value || backupBusy.value) return
        busy.value = true
        error.value = null
        viewModelScope.launch {
            try { action()
            } catch (cancelled: CancellationException) { throw cancelled
            } catch (failure: Exception) { error.value = sourceError(failure)
            } finally { busy.value = false }
        }
    }

    fun selectSource(id: String) {
        if (!busy.value && !backupBusy.value) viewModelScope.launch { error.value = null; app.settings.selectSource(id) }
    }
    fun refresh() = refreshSource(settings.value.sourceId)
    fun refreshSource(id: String) = runSourceOperation {
        if (epgSourceId == id) cancelEpgOperation()
        repository.refresh(id)
        notice.value = "فهرست به‌روز شد؛ این نتیجه، تأیید پخش کانال‌ها نیست."
    }
    fun importUrl(url: String, title: String) = runSourceOperation {
        val id = repository.importUrl(url, title)
        app.settings.selectSource(id)
        notice.value = "فهرست جدید جداگانه اضافه شد؛ فهرست‌های قبلی باقی ماندند."
    }
    fun renameSource(id: String, title: String) = runSourceOperation {
        repository.renameSource(id, title)
        notice.value = "نام فهرست تغییر کرد."
    }
    fun deleteSource(id: String) = runSourceOperation {
        // Asset-backed fallback needs no network. Prepare it before deleting the selected list.
        // stateIn may lag a committed selection; always prepare the cheap offline fallback.
        repository.ensureSource(SAMPLE_SOURCE_ID)
        if (epgSourceId == id) cancelEpgOperation()
        repository.deleteSource(id)
        app.epg.remove(id)
        app.settings.forgetSource(id)
        notice.value = "فهرست حذف شد؛ علاقه‌مندی‌ها و سابقهٔ پخش حفظ شدند."
    }

    fun prepareFileImport(title: String, sourceId: String? = null): Boolean {
        if (busy.value) return false
        val name = try { sourceTitle(title) } catch (failure: InvalidSourceNameException) {
            error.value = sourceError(failure); return false
        }
        // Only action metadata is saved, never a URI/credential. The system result supplies the URI.
        savedState["file_pick_pending"] = true
        savedState["file_pick_title"] = name
        savedState["file_pick_target"] = sourceId
        return true
    }

    fun fileChosen(uri: Uri?) {
        if (savedState.get<Boolean>("file_pick_pending") != true) return
        val title = savedState.get<String>("file_pick_title") ?: return
        val target = savedState.get<String>("file_pick_target")
        savedState["file_pick_pending"] = false
        savedState.remove<String>("file_pick_title")
        savedState.remove<String>("file_pick_target")
        if (uri == null) return // Cancelling SAF must not add, replace, or rename anything.
        viewModelScope.launch {
            settings.first { it.loaded }
            busy.first { !it }
            runSourceOperation {
                val id = if (target == null) repository.importFile(uri, title) else repository.replaceFile(target, uri)
                if (target == null) app.settings.selectSource(id)
                notice.value = if (target == null) "فایل به‌عنوان یک فهرست جدید اضافه شد." else "فهرست فایل به‌روز شد؛ نام و انتخاب‌های قبلی حفظ شدند."
            }
        }
    }

    fun filePickerUnavailable() {
        fileChosen(null)
        error.value = "انتخاب‌گر فایل باز نشد؛ دوباره از بخش فهرست‌ها امتحان کن."
    }

    private fun cancelEpgOperation() {
        epgSequence++
        epgJob?.cancel()
        epgJob = null
        epgSourceId = null
        epgBusy.value = false
    }

    fun cancelEpg() {
        if (epgBusy.value) { cancelEpgOperation(); notice.value = "دریافت راهنما لغو شد." }
    }

    private fun runEpgOperation(id: String, action: suspend () -> Unit) {
        if (epgBusy.value || busy.value || backupBusy.value || !settings.value.loaded) return
        val sequence = ++epgSequence
        epgSourceId = id
        epgBusy.value = true
        epgError.value = null
        epgJob = viewModelScope.launch {
            try { action()
            } catch (cancelled: CancellationException) { throw cancelled
            } catch (failure: Exception) {
                if (sequence == epgSequence && settings.value.sourceId == id) epgError.value = epgFailureMessage(failure)
            } finally {
                if (sequence == epgSequence) {
                    epgBusy.value = false
                    epgSourceId = null
                    epgJob = null
                }
            }
        }
    }

    /** Saving a URL never starts a request; download has its own explicit button. */
    fun configureEpg(id: String, url: String) = runEpgOperation(id) {
        app.epg.configure(id, url)
        notice.value = if (url.isBlank()) "لینک و راهنمای این فهرست حذف شد؛ کانال‌ها و انتخاب‌های تو باقی ماندند."
            else "لینک XMLTV ذخیره شد؛ حالا «دریافت راهنما» را بزن."
    }

    fun refreshEpg() {
        val id = settings.value.sourceId
        runEpgOperation(id) {
            val guide = app.epg.refresh(id)
            notice.value = "راهنما دریافت شد؛ برای ${persianNumber(guide.byChannel.size)} شناسهٔ منطبق برنامه ذخیره شد."
        }
    }

    fun toggleFavorite(id: String) { viewModelScope.launch { dao.toggleFavorite(id) } }
    fun clearHistory() {
        viewModelScope.launch { dao.clearHistory(); notice.value = "سابقهٔ پخش روی این گوشی پاک شد." }
    }
    fun showLogos(show: Boolean) { viewModelScope.launch { app.settings.showLogos(show) } }
    fun automaticPictureInPicture(allow: Boolean) { viewModelScope.launch { app.settings.automaticPictureInPicture(allow) } }
    fun themeStyle(style: String) { viewModelScope.launch { app.settings.themeStyle(style) } }
    fun bufferProfile(id: String) { viewModelScope.launch { app.settings.bufferProfile(id) } }
    fun playerOrientation(id: String) { viewModelScope.launch { app.settings.playerOrientation(id) } }
    fun hideSourceSelector(hide: Boolean) { viewModelScope.launch { app.settings.hideSourceSelector(hide) } }
    fun sleepTimerMinutes(minutes: Int) { viewModelScope.launch { app.settings.sleepTimerMinutes(minutes) } }
    fun rewindSeconds(seconds: Int) { viewModelScope.launch { app.settings.rewindSeconds(seconds) } }
    fun fullScreenResizeMode(id: String) { viewModelScope.launch { app.settings.fullScreenResizeMode(id) } }
    fun windowedResizeMode(id: String) { viewModelScope.launch { app.settings.windowedResizeMode(id) } }
    fun controllerTimeoutMs(timeout: Int) { viewModelScope.launch { app.settings.controllerTimeoutMs(timeout) } }
    fun showPlayerClock(show: Boolean) { viewModelScope.launch { app.settings.showPlayerClock(show) } }
    fun audioDecoderFallback(enabled: Boolean) { viewModelScope.launch { app.settings.audioDecoderFallback(enabled) } }
    fun textScale(scale: Float) { viewModelScope.launch { app.settings.textScale(scale) } }
    fun screenScale(scale: Float) { viewModelScope.launch { app.settings.screenScale(scale) } }
    fun setParentalPin(pin: String) { viewModelScope.launch { app.settings.setParentalPin(pin) } }
    fun verifyParentalPin(pin: String, result: (Boolean) -> Unit) {
        viewModelScope.launch { result(app.settings.verifyParentalPin(pin)) }
    }
    fun parentalControl(enabled: Boolean) { viewModelScope.launch { app.settings.parentalControl(enabled) } }
    fun allowHttp(allow: Boolean, onDone: () -> Unit = {}) {
        viewModelScope.launch {
            app.settings.allowHttp(allow)
            settings.first { it.allowHttp == allow }
            onDone()
        }
    }
    fun clearCrashReport() {
        viewModelScope.launch { withContext(Dispatchers.IO) { app.crashReports.clear() }; crashReport.value = null }
    }
    fun dismissNotice() { notice.value = null }
}

fun sourceError(error: Exception): String = when (error) {
    is InvalidSourceNameException -> "نام فهرست باید بین ۱ تا ۸۰ نویسه باشد."
    is SourceMissingException -> "این فهرست دیگر موجود نیست. از بخش فهرست‌ها یک منبع دیگر انتخاب کن."
    is ProtectedSourceException -> "فهرست‌های آمادهٔ پروژه قابل تغییر نام یا حذف نیستند. فهرست‌های شخصی را می‌توانی مدیریت کنی."
    is FileSourceSelectionRequiredException -> "برای به‌روزرسانی این فهرست، فایل را دوباره از انتخاب‌گر فایل انتخاب کن."
    is NotAFileSourceException -> "این منبع لینک است؛ برای تغییر محتوای آن از به‌روزرسانی فهرست استفاده کن."
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
    else -> "عملیات فهرست انجام نشد. اتصال و منبع را بررسی کن؛ اطلاعات ذخیره‌شده حفظ شده است."
}
