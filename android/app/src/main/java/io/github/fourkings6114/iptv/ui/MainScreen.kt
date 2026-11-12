package io.github.fourkings6114.iptv.ui

import android.content.ClipData
import android.content.ClipboardManager

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.delay
import coil.compose.AsyncImage
import io.github.fourkings6114.iptv.BuildConfig
import io.github.fourkings6114.iptv.core.Channel
import io.github.fourkings6114.iptv.core.persianNumber
import io.github.fourkings6114.iptv.core.sha256
import io.github.fourkings6114.iptv.epg.NowNext
import io.github.fourkings6114.iptv.epg.epgClock
import io.github.fourkings6114.iptv.data.SAMPLE_SOURCE_ID
import io.github.fourkings6114.iptv.data.isFileSource
import io.github.fourkings6114.iptv.data.isBuiltInSource

private enum class Pane(val label: String, val icon: ImageVector) {
    Channels("کانال‌ها", Icons.AutoMirrored.Filled.List),
    Favorites("علاقه‌مندی‌ها", Icons.Default.Favorite),
    History("سابقه", Icons.Default.DateRange),
    Settings("تنظیمات", Icons.Default.Settings),
}

@Composable
fun MainScreen(model: MainViewModel, onImportFile: (FileImportRequest) -> Unit, onPlay: (Channel) -> Unit) {
    val crashReport by model.crashReport.collectAsStateWithLifecycle()
    var showCrashReport by rememberSaveable { mutableStateOf(true) }
    var crashCopied by rememberSaveable { mutableStateOf(false) }
    val settings by model.settings.collectAsStateWithLifecycle()
    val epg by model.epg.collectAsStateWithLifecycle()
    val epgBusy by model.epgBusy.collectAsStateWithLifecycle()
    val epgError by model.epgError.collectAsStateWithLifecycle()
    var editEpg by remember { mutableStateOf(false) }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(lifecycle) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) { now = System.currentTimeMillis(); delay(30_000) }
        }
    }
    val activeEpg = epg.takeIf { it.sourceId == settings.sourceId && it.feedKey == sha256(settings.epgUrl) }
    val guide = activeEpg?.guide?.takeIf { it.usable(now) }
    val guideChannels = remember(guide, activeEpg?.idsByChannel) {
        activeEpg?.idsByChannel?.values?.count { guide?.byChannel?.containsKey(it) == true } ?: 0
    }
    val channels by model.channels.collectAsStateWithLifecycle()
    val favorites by model.favorites.collectAsStateWithLifecycle()
    val favoriteIds by model.favoriteIds.collectAsStateWithLifecycle()
    val history by model.history.collectAsStateWithLifecycle()
    val source by model.source.collectAsStateWithLifecycle()
    val availableSources by model.availableSources.collectAsStateWithLifecycle()
    val busy by model.busy.collectAsStateWithLifecycle()
    val error by model.error.collectAsStateWithLifecycle()
    val notice by model.notice.collectAsStateWithLifecycle()
    var pane by rememberSaveable { mutableStateOf(Pane.Channels) }
    var group by remember(settings.sourceId) { mutableStateOf<String?>(null) }
    var chooseSource by rememberSaveable { mutableStateOf(false) }
    var sourceImportIsFile by rememberSaveable { mutableStateOf<Boolean?>(null) }
    var renameSourceId by rememberSaveable { mutableStateOf<String?>(null) }
    var deleteSourceId by rememberSaveable { mutableStateOf<String?>(null) }
    var httpConfirm by remember { mutableStateOf(false) }
    var pendingHttpChannel by remember { mutableStateOf<Channel?>(null) }
    var licenses by remember { mutableStateOf(false) }
    var clearHistory by remember { mutableStateOf(false) }
    var showEpgSettings by rememberSaveable { mutableStateOf(false) }
    var showAdditionalSettings by rememberSaveable { mutableStateOf(false) }
    var premiumInfo by remember { mutableStateOf(false) }
    var backupInfo by remember { mutableStateOf(false) }
    var languageInfo by remember { mutableStateOf(false) }
    var helpInfo by remember { mutableStateOf(false) }
    var deleteDataInfo by remember { mutableStateOf(false) }
    var themePicker by remember { mutableStateOf(false) }
    val snackbar = remember { SnackbarHostState() }
    val listState = rememberLazyListState()
    val context = LocalContext.current

    LaunchedEffect(notice) {
        notice?.let { snackbar.showSnackbar(it); model.dismissNotice() }
    }
    LaunchedEffect(group, pane, settings.sourceId) { listState.scrollToItem(0) }

    val original = when (pane) {
        Pane.Favorites -> favorites
        Pane.History -> history
        else -> channels
    }
    val visible = remember(original, group, pane) {
        original.filter { channel -> pane != Pane.Channels || group == null || channel.group == group }
    }
    val groups = remember(channels) { channels.groupingBy { it.group }.eachCount().toSortedMap() }
    val openChannel: (Channel) -> Unit = { channel ->
        if (channel.isCleartext && !settings.allowHttp) {
            pendingHttpChannel = channel
            httpConfirm = true
        } else onPlay(channel)
    }
    val refreshSelectedPlaylist: () -> Unit = {
        val current = source?.takeIf { it.id == settings.sourceId }
        if (current?.isFileSource == true) onImportFile(FileImportRequest(current.title, current.id)) else model.refresh()
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            // Reserve real layout space for notices. An overlaid Snackbar can
            // otherwise intercept a freshly scrolled settings button's touch,
            // especially immediately after saving an XMLTV URL on a small phone.
            Column {
                SnackbarHost(snackbar)
                NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                    Pane.entries.forEach { item ->
                        NavigationBarItem(
                            selected = pane == item, onClick = { pane = item },
                            icon = { Icon(item.icon, contentDescription = null) },
                            label = { Text(item.label, maxLines = 1) },
                        )
                    }
                }
            }
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            if (pane == Pane.Settings) {
                Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("تنظیمات", Modifier.weight(1f), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                    IconButton(onClick = { pane = Pane.Channels }) { Icon(Icons.Default.Close, "بستن تنظیمات") }
                }
            } else {
                Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(42.dp).clip(RoundedCornerShape(13.dp)).background(MaterialTheme.colorScheme.primaryContainer), contentAlignment = Alignment.Center) {
                        Icon(Icons.Default.PlayArrow, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(29.dp))
                    }
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        Text("IPTV همراه", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                        Text("نسخهٔ آزمایشی · پخش مستقیم از منبع", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    if (pane == Pane.Channels) {
                        IconButton(onClick = refreshSelectedPlaylist,
                            enabled = !busy && settings.loaded && (isBuiltInSource(settings.sourceId) || source?.id == settings.sourceId)) {
                            Icon(Icons.Default.Refresh, "به‌روزرسانی فهرست")
                        }
                    }
                }
            }
            if (busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            if (error != null) {
                Surface(color = MaterialTheme.colorScheme.errorContainer, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), shape = RoundedCornerShape(12.dp)) {
                    Text(error.orEmpty(), Modifier.padding(14.dp), style = MaterialTheme.typography.bodySmall)
                }
            }
            if (epgError != null && pane != Pane.Settings) {
                Surface(color = MaterialTheme.colorScheme.errorContainer, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), shape = RoundedCornerShape(12.dp)) {
                    Text(epgError.orEmpty(), Modifier.padding(14.dp), style = MaterialTheme.typography.bodySmall)
                }
            }
            if (pane == Pane.Settings) {
                Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
                    SettingsMenuRow("دریافت نسخهٔ پریمیوم", accent = Color(0xFFE6D05C), onClick = { premiumInfo = true })
                    SettingsMenuRow("لیست‌های پخش", enabled = !busy, onClick = { chooseSource = true })
                    SettingsMenuRow(
                        "راهنمای برنامه‌ها",
                        trailing = if (settings.epgUrl.isBlank()) "تنظیم نشده" else "تنظیم‌شده",
                        onClick = { showEpgSettings = !showEpgSettings },
                    )
                    SettingsMenuRow(
                        "به‌روزرسانی لیست پخش",
                        enabled = !busy && settings.loaded && (isBuiltInSource(settings.sourceId) || source?.id == settings.sourceId),
                        onClick = refreshSelectedPlaylist,
                    )
                    SettingsMenuRow(
                        "به‌روزرسانی داده‌های EPG",
                        enabled = settings.loaded && !busy && !epgBusy,
                        onClick = { if (settings.epgUrl.isBlank()) showEpgSettings = true else model.refreshEpg() },
                    )
                    SettingsMenuRow(
                        "تنظیمات اضافی",
                        trailing = if (showAdditionalSettings) "باز" else null,
                        onClick = { showAdditionalSettings = !showAdditionalSettings },
                    )
                    SettingsMenuRow("پشتیبان‌گیری و بازیابی", onClick = { backupInfo = true })
                    SettingsMenuRow("حذف داده‌ها", accent = MaterialTheme.colorScheme.error, onClick = { deleteDataInfo = true })
                    SettingsMenuRow(
                        "رنگ تم",
                        trailing = AppThemeStyle.fromId(settings.themeStyle).label,
                        onClick = { themePicker = true },
                    )
                    SettingsMenuRow("زبان", trailing = "فارسی", onClick = { languageInfo = true })
                    SettingsMenuRow("کمک و پشتیبانی", onClick = { helpInfo = true })

                    if (showEpgSettings) SettingsCard {
                        EpgSettingsContent(source?.takeIf { it.id == settings.sourceId }?.title ?: "در حال آماده‌سازی…",
                            settings.epgUrl.isNotBlank(), guide, guideChannels, now, epgBusy, epgError,
                            enabled = !busy && settings.loaded && source?.id == settings.sourceId,
                            onConfigure = { editEpg = true }, onRefresh = model::refreshEpg, onCancel = model::cancelEpg)
                    }
                    if (showAdditionalSettings) {
                        SettingsCard {
                            Text("اتصال و نمایش", style = MaterialTheme.typography.titleMedium)
                            SettingToggle("اجازهٔ HTTP بدون رمزنگاری", "پیش‌فرض خاموش؛ فقط برای منبع مورداعتماد روشنش کن.", settings.allowHttp) { allow ->
                                if (allow) httpConfirm = true else model.allowHttp(false)
                            }
                            HorizontalDivider()
                            SettingToggle("نمایش لوگوها", "دریافت تصویر از میزبان لوگوی هر کانال؛ ممکن است ترافیک مصرف کند.", settings.showLogos, model::showLogos)
                        }
                        SettingsCard {
                            Text("پنجرهٔ شناور پخش", style = MaterialTheme.typography.titleMedium)
                            if (context.packageManager.hasSystemFeature(android.content.pm.PackageManager.FEATURE_PICTURE_IN_PICTURE)) {
                                SettingToggle("شناور شدن با دکمهٔ Home", "پیش‌فرض خاموش؛ فقط پس از شروع تصویر و هنگام پخش.", settings.autoPictureInPicture, model::automaticPictureInPicture)
                                Text("پنجره با کنترل‌های خود اندروید مکث، ادامه، بزرگ یا بسته می‌شود. بستن آن پخش را متوقف می‌کند.", style = MaterialTheme.typography.bodySmall)
                            } else {
                                Text("این دستگاه پشتیبانی تصویر در تصویر را اعلام نکرده است.", style = MaterialTheme.typography.bodySmall)
                            }
                        }
                        SettingsCard {
                            Text("حریم خصوصی", style = MaterialTheme.typography.titleMedium)
                            Text("علاقه‌مندی‌ها، سابقه و لینک‌ها در فضای خصوصی همین گوشی نگه‌داری می‌شوند؛ حساب، تبلیغات یا همگام‌سازی نداریم. میزبان‌های فهرست، راهنما، لوگو و ویدئو IP گوشی را می‌بینند.", style = MaterialTheme.typography.bodySmall)
                            if (crashReport != null) {
                                TextButton(onClick = { showCrashReport = true }) { Text("مشاهده و کپی گزارش کرش") }
                                TextButton(onClick = { model.clearCrashReport() }) { Text("پاک‌کردن گزارش کرش") }
                            }
                            TextButton(onClick = { licenses = true }) { Text("کتابخانه‌ها و مجوزها") }
                        }
                    }
                    Text("IPTV همراه · نسخهٔ ${BuildConfig.VERSION_NAME}",
                        Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 24.dp),
                        style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else {
                if (pane == Pane.Channels) {
                    SourcePickerCard(
                        title = source?.title ?: "آماده‌سازی فهرست…",
                        channelCount = source?.count,
                        enabled = !busy && settings.loaded,
                        onClick = { chooseSource = true },
                    )
                } else {
                    Text(pane.label, Modifier.padding(horizontal = 20.dp, vertical = 8.dp), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                }
                if (pane == Pane.Channels && group == null && channels.size > 1) {
                    val featuredChannel = history.firstOrNull()
                    FeaturedPlaybackCard(
                        channel = featuredChannel,
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
                        onPlay = { featuredChannel?.let(openChannel) },
                    )
                }
                if (pane == Pane.Channels && groups.isNotEmpty()) {
                    LazyRow(contentPadding = PaddingValues(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 8.dp)) {
                        item { FilterChip(selected = group == null, onClick = { group = null }, label = { Text("همه · ${persianNumber(channels.size)}") }) }
                        items(groups.entries.toList(), key = { it.key }) { entry ->
                            FilterChip(selected = group == entry.key, onClick = { group = entry.key }, label = { Text("${entry.key} · ${persianNumber(entry.value)}") })
                        }
                    }
                }
                if (pane == Pane.Channels) {
                    Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text("${persianNumber(visible.size)} کانال" + when {
                            epgBusy -> " · دریافت راهنما…"
                            guide != null -> if (guide.stale(now)) " · راهنمای قدیمی، ساعت محلی" else " · برنامه‌ها به ساعت محلی"
                            settings.epgUrl.isNotBlank() -> " · راهنما را دریافت کن"
                            else -> ""
                        }, Modifier.weight(1f), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        if (settings.epgUrl.isNotBlank()) IconButton(onClick = model::refreshEpg, enabled = !epgBusy && !busy) {
                            Icon(Icons.Default.Refresh, "به‌روزرسانی راهنما")
                        } else TextButton(onClick = { pane = Pane.Settings }) { Text("تنظیم راهنما") }
                    }
                } else Text("${persianNumber(visible.size)} کانال", Modifier.padding(horizontal = 20.dp, vertical = 8.dp), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (visible.isEmpty() && !busy) {
                    Box(Modifier.fillMaxWidth().weight(1f).padding(32.dp), contentAlignment = Alignment.Center) {
                        Text(when {
                            pane == Pane.Favorites -> "با لمس قلب کنار کانال، آن را اینجا نگه دار."
                            pane == Pane.History -> "بعد از شروع پخش، کانال‌های اخیر اینجا دیده می‌شوند."
                            else -> "فهرست آماده نیست. دوباره دریافت کن یا منبع دیگری انتخاب کن."
                        }, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                } else {
                    LazyColumn(state = listState, modifier = Modifier.weight(1f), contentPadding = PaddingValues(start = 16.dp, end = 16.dp, bottom = 20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        if (pane == Pane.Channels && settings.sourceId == SAMPLE_SOURCE_ID) {
                            item {
                                Surface(color = MaterialTheme.colorScheme.primaryContainer, shape = RoundedCornerShape(16.dp)) {
                                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                                        Text("اول پخش را امتحان کنیم", fontWeight = FontWeight.Bold)
                                        Text("این ۲۰ نمونه از فهرست پروژه انتخاب شده‌اند؛ پخش آن‌ها روی گوشی هنوز تأیید نشده است. برای فهرست کامل، منبع را تغییر بده.", style = MaterialTheme.typography.bodySmall)
                                    }
                                }
                            }
                        }
                        items(visible, key = { it.id }) { channel ->
                            val programme = activeEpg?.idsByChannel?.get(channel.id)?.let { guide?.at(it, now) } ?: NowNext()
                            ChannelRow(channel, channel.id in favoriteIds, settings.showLogos, programme, now,
                                onPlay = { openChannel(channel) }, onFavorite = { model.toggleFavorite(channel.id) })
                        }
                    }
                }
            }
        }
    }

    if (editEpg) {
        val current = source?.takeIf { it.id == settings.sourceId }
        if (current != null) EpgUrlDialog(current.id, current.title, settings.epgUrl,
            onSave = { url -> model.configureEpg(current.id, url); editEpg = false }, onDismiss = { editEpg = false })
        else LaunchedEffect(Unit) { editEpg = false }
    }
    if (showCrashReport && crashReport != null) {
        AlertDialog(
            onDismissRequest = { showCrashReport = false },
            title = { Text("گزارش اشکال برنامه") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("یک خطای ثبت‌شده از اجرای قبلی موجود است. گزارش فقط شامل کلاس خطا، محل کد و مشخصات فنی است؛ متن خطا، آدرس و هدرها ثبت نمی‌شوند. می‌توانی آن را پس از بررسی دستی کپی کنی؛ چیزی خودکار ارسال نمی‌شود.", style = MaterialTheme.typography.bodySmall)
                    Text(crashReport.orEmpty(), Modifier.heightIn(max = 240.dp).verticalScroll(rememberScrollState()), style = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Ltr))
                }
            },
            confirmButton = { TextButton(onClick = {
                context.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("IPTV crash report", crashReport.orEmpty()))
                crashCopied = true
            }) { Text(if (crashCopied) "کپی شد" else "کپی گزارش کرش") } },
            dismissButton = { TextButton(onClick = { showCrashReport = false }) { Text("بستن") } },
        )
    }
    if (chooseSource) {
        SourcesDialog(
            sources = availableSources, selectedId = settings.sourceId, busy = busy, error = error,
            onSelect = { model.selectSource(it); chooseSource = false; pane = Pane.Channels },
            onRefresh = { item ->
                if (item.isFileSource) onImportFile(FileImportRequest(item.title, item.id)) else model.refreshSource(item.id)
            },
            onRename = { chooseSource = false; renameSourceId = it },
            onDelete = { chooseSource = false; deleteSourceId = it },
            onAddUrl = { chooseSource = false; sourceImportIsFile = false },
            onAddFile = { chooseSource = false; sourceImportIsFile = true },
            onDismiss = { chooseSource = false },
        )
    }
    sourceImportIsFile?.let { isFile ->
        SourceImportDialog(
            isFile = isFile,
            onSubmit = { title, url ->
                sourceImportIsFile = null
                if (isFile) onImportFile(FileImportRequest(title)) else { model.importUrl(url, title); pane = Pane.Channels }
            },
            onDismiss = { sourceImportIsFile = null },
        )
    }
    renameSourceId?.let { id ->
        availableSources.find { it.id == id }?.let { item ->
            SourceRenameDialog(item,
                onSave = { model.renameSource(id, it); renameSourceId = null; chooseSource = true },
                onDismiss = { renameSourceId = null; chooseSource = true },
            )
        }
    }
    deleteSourceId?.let { id ->
        availableSources.find { it.id == id }?.let { item ->
            SourceDeleteDialog(item,
                onDelete = { model.deleteSource(id); deleteSourceId = null; chooseSource = true },
                onDismiss = { deleteSourceId = null; chooseSource = true },
            )
        }
    }
    if (httpConfirm) {
        AlertDialog(
            onDismissRequest = { httpConfirm = false; pendingHttpChannel = null },
            title = { Text("اجازهٔ ارتباط بدون رمزنگاری؟") },
            text = { Text("HTTP اتصال امن نیست و آدرس، اطلاعات حساب و محتوای رسانه ممکن است برای دیگران قابل‌مشاهده یا تغییر باشد. فقط برای منابع مورداعتماد فعال کن. این اجازه تا زمان خاموش‌کردن آن در تنظیمات باقی می‌ماند؛ بررسی گواهی HTTPS همچنان فعال است.") },
            confirmButton = { TextButton(onClick = {
                val channel = pendingHttpChannel
                httpConfirm = false; pendingHttpChannel = null
                model.allowHttp(true) { channel?.let(onPlay) }
            }) { Text("می‌پذیرم؛ فعال شود") } },
            dismissButton = { TextButton(onClick = { httpConfirm = false; pendingHttpChannel = null }) { Text("انصراف") } },
        )
    }
    if (clearHistory) {
        AlertDialog(onDismissRequest = { clearHistory = false }, title = { Text("پاک‌کردن سابقه؟") }, text = { Text("فقط سابقهٔ پخش روی این گوشی پاک می‌شود؛ فهرست‌ها، علاقه‌مندی‌ها و تنظیمات باقی می‌مانند.") },
            confirmButton = { TextButton(onClick = { model.clearHistory(); clearHistory = false }) { Text("پاک‌کردن سابقه") } },
            dismissButton = { TextButton(onClick = { clearHistory = false }) { Text("انصراف") } })
    }
    if (deleteDataInfo) {
        AlertDialog(
            onDismissRequest = { deleteDataInfo = false },
            title = { Text("مدیریت حذف داده‌ها") },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("اطلاعات در فضای خصوصی همین گوشی ذخیره می‌شوند. می‌توانی تاریخچه را پاک کنی یا فهرست‌های شخصی را جداگانه از مدیریت فهرست‌ها حذف کنی. فایل‌های اصلی گوشی دست‌کاری نمی‌شوند.")
                    TextButton(onClick = { deleteDataInfo = false; chooseSource = true }) { Text("مدیریت فهرست‌های پخش") }
                }
            },
            confirmButton = { TextButton(onClick = { deleteDataInfo = false; clearHistory = true }) { Text("پاک‌کردن تاریخچه") } },
            dismissButton = { TextButton(onClick = { deleteDataInfo = false }) { Text("بستن") } },
        )
    }
    if (premiumInfo) {
        AlertDialog(
            onDismissRequest = { premiumInfo = false },
            title = { Text("نسخهٔ پریمیوم") },
            text = { Text("این خروجی، نسخهٔ آزمایشی IPTV همراه است و در حال حاضر خرید، اشتراک یا قابلیت پریمیوم در برنامه ارائه نمی‌شود.") },
            confirmButton = { TextButton(onClick = { premiumInfo = false }) { Text("متوجه شدم") } },
        )
    }
    if (backupInfo) {
        AlertDialog(
            onDismissRequest = { backupInfo = false },
            title = { Text("پشتیبان‌گیری و بازیابی") },
            text = { Text("خروجی‌گرفتن یا بازیابی داده‌های برنامه هنوز پیاده‌سازی نشده است. پشتیبان ابری نداریم؛ لینک‌های دارای توکن را به فایل یا فضای اشتراکی صادر نمی‌کنیم. برای حفظ داده‌ها برنامه را حذف یا داده‌هایش را پاک نکن.") },
            confirmButton = { TextButton(onClick = { backupInfo = false }) { Text("بستن") } },
        )
    }
    if (languageInfo) {
        AlertDialog(
            onDismissRequest = { languageInfo = false },
            title = { Text("زبان برنامه") },
            text = { Text("در این نسخه فقط زبان فارسی با چیدمان راست‌به‌چپ فعال است؛ انتخاب زبان دیگری هنوز در دسترس نیست.") },
            confirmButton = { TextButton(onClick = { languageInfo = false }) { Text("بستن") } },
        )
    }
    if (helpInfo) {
        AlertDialog(
            onDismissRequest = { helpInfo = false },
            title = { Text("راهنما و پشتیبانی") },
            text = { Text("برای پخش، از فهرست کانال انتخاب کن. فهرست‌های لینک و فایل از بخش «لیست‌های پخش» مدیریت می‌شوند. راهنمای EPG را جداگانه برای هر فهرست تنظیم و دستی دریافت کن. دکمهٔ «شناور» پس از شروع تصویر، ویدئو را به PiP می‌برد؛ Home خودکار پیش‌فرض خاموش است. فقط از منابع مجاز استفاده کن و لینک خصوصی، رمز یا توکن را در گفتگو نفرست.") },
            confirmButton = { TextButton(onClick = { helpInfo = false }) { Text("بستن") } },
            dismissButton = { if (crashReport != null) TextButton(onClick = { helpInfo = false; showCrashReport = true }) { Text("گزارش کرش") } },
        )
    }
    if (themePicker) {
        AlertDialog(
            onDismissRequest = { themePicker = false },
            title = { Text("رنگ تم") },
            text = {
                Column {
                    AppThemeStyle.entries.forEach { style ->
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            RadioButton(selected = settings.themeStyle == style.id, onClick = { model.themeStyle(style.id) })
                            TextButton(onClick = { model.themeStyle(style.id) }) { Text(style.label) }
                        }
                    }
                }
            },
            confirmButton = { TextButton(onClick = { themePicker = false }) { Text("بستن") } },
        )
    }
    if (licenses) {
        val text = remember { context.assets.open("open_source_licenses.txt").bufferedReader().use { it.readText() } }
        AlertDialog(onDismissRequest = { licenses = false }, title = { Text("کتابخانه‌ها و مجوزها") }, text = { Text(text, Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()), style = MaterialTheme.typography.bodySmall) }, confirmButton = { TextButton(onClick = { licenses = false }) { Text("بستن") } })
    }
}

@Composable
private fun FeaturedPlaybackCard(channel: Channel?, modifier: Modifier = Modifier, onPlay: () -> Unit) {
    Surface(
        onClick = onPlay,
        enabled = channel != null,
        shape = RoundedCornerShape(24.dp),
        color = Color.Transparent,
        modifier = modifier,
    ) {
        Box(
            Modifier.fillMaxWidth().height(96.dp).background(
                Brush.linearGradient(listOf(Color(0xFF171A35), Color(0xFF5148B8))),
            ),
        ) {
            Box(
                Modifier.align(Alignment.CenterEnd).offset(x = 46.dp).size(176.dp)
                    .clip(CircleShape).background(Color.White.copy(alpha = 0.06f)),
            )
            Row(Modifier.fillMaxSize().padding(horizontal = 18.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier.size(56.dp).clip(CircleShape).background(Color.White.copy(alpha = 0.16f)),
                    contentAlignment = Alignment.Center,
                ) {
                    Icon(Icons.Default.PlayArrow, contentDescription = null, tint = Color.White, modifier = Modifier.size(34.dp))
                }
                Spacer(Modifier.width(14.dp))
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    Text(if (channel == null) "IPTV · TV ON THE GO" else "آخرین کانال",
                        style = MaterialTheme.typography.labelMedium, color = Color(0xFFD7D5FF))
                    Text(channel?.name ?: "تلویزیون، هرجا", style = MaterialTheme.typography.titleLarge,
                        color = Color.White, fontWeight = FontWeight.Bold, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(channel?.group?.takeIf { it.isNotBlank() } ?: if (channel == null) "یک کانال را از فهرست انتخاب کن" else "برای ادامه لمس کن",
                        style = MaterialTheme.typography.bodySmall, color = Color.White.copy(alpha = 0.78f),
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

@Composable
private fun SourcePickerCard(title: String, channelCount: Int?, enabled: Boolean, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        enabled = enabled,
        shape = RoundedCornerShape(16.dp),
        color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.5f)),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 3.dp),
    ) {
        Row(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier.size(36.dp).clip(RoundedCornerShape(11.dp)).background(MaterialTheme.colorScheme.primaryContainer),
                contentAlignment = Alignment.Center,
            ) {
                Icon(Icons.AutoMirrored.Filled.List, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(1.dp)) {
                Text("فهرست منتخب", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.primary)
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(title, Modifier.weight(1f), style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                    channelCount?.let {
                        Text("  ·  ${persianNumber(it)} کانال", style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                    }
                }
            }
            Icon(Icons.Default.ArrowDropDown, contentDescription = "تغییر فهرست",
                tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun ChannelRow(channel: Channel, favorite: Boolean, showLogo: Boolean, programmes: NowNext, now: Long,
                       onPlay: () -> Unit, onFavorite: () -> Unit) {
    Surface(
        onClick = onPlay,
        shape = RoundedCornerShape(17.dp),
        color = MaterialTheme.colorScheme.surface,
        border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.45f)),
        tonalElevation = 0.5.dp,
    ) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 11.dp, vertical = 9.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(42.dp).clip(RoundedCornerShape(12.dp)).background(MaterialTheme.colorScheme.surfaceVariant), contentAlignment = Alignment.Center) {
                Text(channel.name.take(1), color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.titleLarge)
                if (showLogo && channel.logo.isNotBlank()) AsyncImage(model = channel.logo, contentDescription = null, modifier = Modifier.fillMaxSize().padding(5.dp), contentScale = ContentScale.Fit)
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(channel.name, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold,
                    maxLines = 2, overflow = TextOverflow.Ellipsis)
                if (channel.group.isNotBlank()) {
                    Text(channel.group, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
                programmes.current?.let { Text("اکنون · ${epgClock(it.start, now)} · ${it.title}",
                    style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis,
                    color = MaterialTheme.colorScheme.primary) }
                programmes.next?.let { Text("بعدی · ${epgClock(it.start, now)} · ${it.title}",
                    style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis,
                    color = MaterialTheme.colorScheme.onSurfaceVariant) }
                if (channel.isCleartext) Text("HTTP · بدون رمزنگاری", style = MaterialTheme.typography.labelSmall, color = Color(0xFFFFCF80))
            }
            IconButton(onClick = onFavorite, modifier = Modifier.size(44.dp)) {
                Icon(if (favorite) Icons.Default.Favorite else Icons.Default.FavoriteBorder,
                    if (favorite) "حذف از علاقه‌مندی‌ها" else "افزودن به علاقه‌مندی‌ها",
                    tint = if (favorite) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}

@Composable
private fun SettingsCard(content: @Composable ColumnScope.() -> Unit) {
    Surface(shape = RoundedCornerShape(18.dp), color = MaterialTheme.colorScheme.surface) {
        Column(Modifier.fillMaxWidth().padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp), content = content)
    }
}

@Composable
private fun SettingsMenuRow(
    title: String,
    trailing: String? = null,
    accent: Color? = null,
    enabled: Boolean = true,
    onClick: () -> Unit,
) {
    Surface(
        onClick = onClick,
        enabled = enabled,
        color = MaterialTheme.colorScheme.surface,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            Modifier.fillMaxWidth().heightIn(min = 68.dp).padding(horizontal = 20.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(title, Modifier.weight(1f), style = MaterialTheme.typography.titleSmall,
                color = if (enabled) accent ?: MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            trailing?.let { Text(it, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            Spacer(Modifier.width(8.dp))
            Icon(Icons.Default.ChevronLeft, contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.size(22.dp))
        }
    }
    HorizontalDivider(color = MaterialTheme.colorScheme.outline.copy(alpha = 0.42f), thickness = 1.dp)
}

@Composable
private fun SettingToggle(title: String, description: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Text(description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Spacer(Modifier.width(12.dp))
        Switch(checked = checked, onCheckedChange = onChange,
            modifier = Modifier.semantics { contentDescription = title })
    }
}
