package io.github.fourkings6114.iptv.ui

import android.content.ClipData
import android.content.ClipboardManager

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil.compose.AsyncImage
import io.github.fourkings6114.iptv.BuildConfig
import io.github.fourkings6114.iptv.core.Channel
import io.github.fourkings6114.iptv.core.normalizeSearch
import io.github.fourkings6114.iptv.core.persianNumber
import io.github.fourkings6114.iptv.data.SAMPLE_SOURCE_ID
import io.github.fourkings6114.iptv.data.isFileSource
import io.github.fourkings6114.iptv.data.isBuiltInSource
import java.text.DateFormat
import java.util.Date
import java.util.Locale

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
    var query by rememberSaveable { mutableStateOf("") }
    var group by remember(settings.sourceId) { mutableStateOf<String?>(null) }
    var chooseSource by rememberSaveable { mutableStateOf(false) }
    var sourceImportIsFile by rememberSaveable { mutableStateOf<Boolean?>(null) }
    var renameSourceId by rememberSaveable { mutableStateOf<String?>(null) }
    var deleteSourceId by rememberSaveable { mutableStateOf<String?>(null) }
    var httpConfirm by remember { mutableStateOf(false) }
    var pendingHttpChannel by remember { mutableStateOf<Channel?>(null) }
    var licenses by remember { mutableStateOf(false) }
    var clearHistory by remember { mutableStateOf(false) }
    val snackbar = remember { SnackbarHostState() }
    val listState = rememberLazyListState()
    val context = LocalContext.current

    LaunchedEffect(notice) {
        notice?.let { snackbar.showSnackbar(it); model.dismissNotice() }
    }
    LaunchedEffect(query, group, pane, settings.sourceId) { listState.scrollToItem(0) }

    val original = when (pane) {
        Pane.Favorites -> favorites
        Pane.History -> history
        else -> channels
    }
    val visible = remember(original, query, group, pane) {
        val search = normalizeSearch(query)
        original.filter { channel ->
            (pane != Pane.Channels || group == null || channel.group == group) &&
                (search.isEmpty() || channel.searchText.contains(search))
        }
    }
    val groups = remember(channels) { channels.groupingBy { it.group }.eachCount().toSortedMap() }
    val openChannel: (Channel) -> Unit = { channel ->
        if (channel.isCleartext && !settings.allowHttp) {
            pendingHttpChannel = channel
            httpConfirm = true
        } else onPlay(channel)
    }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                Pane.entries.forEach { item ->
                    NavigationBarItem(
                        selected = pane == item, onClick = { pane = item; query = "" },
                        icon = { Icon(item.icon, contentDescription = null) },
                        label = { Text(item.label, maxLines = 1) },
                    )
                }
            }
        },
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding)) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 20.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(46.dp).clip(RoundedCornerShape(14.dp)).background(MaterialTheme.colorScheme.primaryContainer), contentAlignment = Alignment.Center) {
                    Icon(Icons.Default.PlayArrow, null, tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(32.dp))
                }
                Spacer(Modifier.width(12.dp))
                Column(Modifier.weight(1f)) {
                    Text("IPTV همراه", style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                    Text("نسخهٔ آزمایشی · پخش مستقیم از منبع", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                if (pane == Pane.Channels) {
                    IconButton(onClick = {
                        // Source info and settings are separate flows: never target a stale file row.
                        val current = source?.takeIf { it.id == settings.sourceId }
                        if (current?.isFileSource == true) onImportFile(FileImportRequest(current.title, current.id)) else model.refresh()
                    }, enabled = !busy && settings.loaded && (isBuiltInSource(settings.sourceId) || source?.id == settings.sourceId)) {
                        Icon(Icons.Default.Refresh, "به‌روزرسانی فهرست")
                    }
                }
            }
            if (busy) LinearProgressIndicator(Modifier.fillMaxWidth())
            if (error != null) {
                Surface(color = MaterialTheme.colorScheme.errorContainer, modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp), shape = RoundedCornerShape(12.dp)) {
                    Text(error.orEmpty(), Modifier.padding(14.dp), style = MaterialTheme.typography.bodySmall)
                }
            }
            if (pane == Pane.Settings) {
                Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                    Text("منبع و حریم خصوصی", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                    SettingsCard {
                        Text("فهرست فعلی", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                        Text(source?.title ?: "در حال آماده‌سازی…", style = MaterialTheme.typography.titleMedium)
                        source?.let { info ->
                            val date = remember(info.updatedAt) { DateFormat.getDateTimeInstance(DateFormat.SHORT, DateFormat.SHORT, Locale.forLanguageTag("fa")).format(Date(info.updatedAt)) }
                            Text("${persianNumber(info.count)} کانال · ذخیره در $date", style = MaterialTheme.typography.bodySmall)
                            if (info.skipped > 0) Text("${persianNumber(info.skipped)} ورودی نامعتبر کنار گذاشته شد.", style = MaterialTheme.typography.bodySmall)
                        }
                        Text("زمان بالا مربوط به دریافت فهرست است، نه تأیید سلامت پخش.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        OutlinedButton(onClick = { chooseSource = true }, enabled = !busy && settings.loaded, modifier = Modifier.fillMaxWidth()) { Text("مدیریت فهرست‌ها") }
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(onClick = { sourceImportIsFile = false }, enabled = !busy && settings.loaded, modifier = Modifier.weight(1f)) { Text("افزودن لینک") }
                            OutlinedButton(onClick = { sourceImportIsFile = true }, enabled = !busy && settings.loaded, modifier = Modifier.weight(1f)) { Text("افزودن فایل") }
                        }
                        Text("پروفایل کم‌مصرف، فهرستی از نسخه‌های انتخاب‌شده است؛ ویدئو را تبدیل نمی‌کند و مصرف ثابت را تضمین نمی‌کند.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    SettingsCard {
                        SettingToggle("اجازهٔ HTTP بدون رمزنگاری", "پیش‌فرض خاموش؛ روی تمام درخواست‌های بعدی رسانه و فهرست اعمال می‌شود.", settings.allowHttp) { allow ->
                            if (allow) httpConfirm = true else model.allowHttp(false)
                        }
                        HorizontalDivider()
                        SettingToggle("نمایش لوگوها", "دریافت تصویر از میزبان لوگوی هر کانال؛ ممکن است ترافیک مصرف کند.", settings.showLogos, model::showLogos)
                    }
                    SettingsCard {
                        Text("اطلاعات فقط روی این گوشی", style = MaterialTheme.typography.titleMedium)
                        Text("علاقه‌مندی‌ها، سابقه و لینک‌های واردشده در فضای خصوصی برنامه ذخیره می‌شوند. حساب، تبلیغات، تحلیل‌گر و همگام‌سازی نداریم؛ پشتیبان‌گیری برنامه غیرفعال است.", style = MaterialTheme.typography.bodySmall)
                        Text("برای دریافت فهرست، لوگو و ویدئو، گوشی مستقیماً با میزبان آن‌ها ارتباط می‌گیرد و میزبان می‌تواند IP را ببیند. فهرست ذخیره‌شده آفلاین باز می‌شود، اما پخش ویدئو اینترنت می‌خواهد.", style = MaterialTheme.typography.bodySmall)
                        OutlinedButton(onClick = { clearHistory = true }) { Text("پاک‌کردن سابقهٔ پخش") }
                    }
                    if (crashReport != null) SettingsCard {
                        Text("گزارش بسته‌شدن قبلی", style = MaterialTheme.typography.titleMedium)
                        Text("فقط روی همین گوشی نگه‌داری می‌شود و خودکار ارسال نمی‌شود.", style = MaterialTheme.typography.bodySmall)
                        TextButton(onClick = { showCrashReport = true }) { Text("مشاهده و کپی گزارش کرش") }
                        TextButton(onClick = { model.clearCrashReport() }) { Text("پاک‌کردن گزارش کرش") }
                    }
                    SettingsCard {
                        Text("دربارهٔ این نسخه", style = MaterialTheme.typography.titleMedium)
                        Text("نسخهٔ ${BuildConfig.VERSION_NAME} · اندروید ۸ به بالا\nموتور: Media3 · بدون FFmpeg و VLC\nبا خروج از صفحهٔ پخش، پخش متوقف می‌شود.", style = MaterialTheme.typography.bodySmall)
                        Text("این برنامه مالک یا میزبان محتوای کانال‌ها نیست. عمومی‌بودن یک لینک، مجوز استفاده از محتوای آن را ثابت نمی‌کند؛ فقط از منابع مجاز استفاده کن. پخش به منبع، اتصال و قابلیت‌های گوشی وابسته است.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        TextButton(onClick = { licenses = true }) { Text("کتابخانه‌ها و مجوزها") }
                    }
                }
            } else {
                if (pane == Pane.Channels) {
                    TextButton(onClick = { chooseSource = true }, enabled = !busy && settings.loaded, modifier = Modifier.padding(horizontal = 12.dp)) {
                        Text(source?.title ?: "آماده‌سازی فهرست…", maxLines = 1, overflow = TextOverflow.Ellipsis)
                        Spacer(Modifier.width(8.dp))
                        Icon(Icons.Default.ArrowDropDown, null)
                    }
                } else {
                    Text(pane.label, Modifier.padding(horizontal = 20.dp, vertical = 8.dp), style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.Bold)
                }
                OutlinedTextField(
                    value = query, onValueChange = { query = it }, singleLine = true,
                    modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp), shape = RoundedCornerShape(16.dp),
                    placeholder = { Text("جست‌وجوی نام یا دستهٔ کانال") },
                    leadingIcon = { Icon(Icons.Default.Search, null) },
                    trailingIcon = { if (query.isNotEmpty()) IconButton(onClick = { query = "" }) { Icon(Icons.Default.Close, "پاک‌کردن جست‌وجو") } },
                )
                if (pane == Pane.Channels && groups.isNotEmpty()) {
                    LazyRow(contentPadding = PaddingValues(horizontal = 16.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 8.dp)) {
                        item { FilterChip(selected = group == null, onClick = { group = null }, label = { Text("همه · ${persianNumber(channels.size)}") }) }
                        items(groups.entries.toList(), key = { it.key }) { entry ->
                            FilterChip(selected = group == entry.key, onClick = { group = entry.key }, label = { Text("${entry.key} · ${persianNumber(entry.value)}") })
                        }
                    }
                }
                Text("${persianNumber(visible.size)} کانال", Modifier.padding(horizontal = 20.dp, vertical = 8.dp), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                if (visible.isEmpty() && !busy) {
                    Box(Modifier.fillMaxWidth().weight(1f).padding(32.dp), contentAlignment = Alignment.Center) {
                        Text(when {
                            query.isNotEmpty() -> "کانالی با این جست‌وجو پیدا نشد."
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
                            ChannelRow(channel, channel.id in favoriteIds, settings.showLogos,
                                onPlay = { openChannel(channel) }, onFavorite = { model.toggleFavorite(channel.id) })
                        }
                    }
                }
            }
        }
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
        AlertDialog(onDismissRequest = { clearHistory = false }, title = { Text("پاک‌کردن سابقه؟") }, text = { Text("سابقهٔ پخش روی این گوشی پاک می‌شود؛ علاقه‌مندی‌ها باقی می‌مانند.") },
            confirmButton = { TextButton(onClick = { model.clearHistory(); clearHistory = false }) { Text("پاک شود") } },
            dismissButton = { TextButton(onClick = { clearHistory = false }) { Text("انصراف") } })
    }
    if (licenses) {
        val text = remember { context.assets.open("open_source_licenses.txt").bufferedReader().use { it.readText() } }
        AlertDialog(onDismissRequest = { licenses = false }, title = { Text("کتابخانه‌ها و مجوزها") }, text = { Text(text, Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()), style = MaterialTheme.typography.bodySmall) }, confirmButton = { TextButton(onClick = { licenses = false }) { Text("بستن") } })
    }
}

@Composable
private fun ChannelRow(channel: Channel, favorite: Boolean, showLogo: Boolean, onPlay: () -> Unit, onFavorite: () -> Unit) {
    Surface(onClick = onPlay, shape = RoundedCornerShape(16.dp), color = MaterialTheme.colorScheme.surface) {
        Row(Modifier.fillMaxWidth().padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(48.dp).clip(RoundedCornerShape(12.dp)).background(MaterialTheme.colorScheme.surfaceVariant), contentAlignment = Alignment.Center) {
                Text(channel.name.take(1), color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.titleLarge)
                if (showLogo && channel.logo.isNotBlank()) AsyncImage(model = channel.logo, contentDescription = null, modifier = Modifier.fillMaxSize().padding(5.dp), contentScale = ContentScale.Fit)
            }
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(channel.name, style = MaterialTheme.typography.bodyLarge, maxLines = 2, overflow = TextOverflow.Ellipsis)
                Text(channel.group, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                if (channel.isCleartext) Text("HTTP · بدون رمزنگاری", style = MaterialTheme.typography.labelSmall, color = Color(0xFFFFCF80))
            }
            IconButton(onClick = onFavorite) { Icon(if (favorite) Icons.Default.Favorite else Icons.Default.FavoriteBorder, if (favorite) "حذف از علاقه‌مندی‌ها" else "افزودن به علاقه‌مندی‌ها", tint = if (favorite) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant) }
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
private fun SettingToggle(title: String, description: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Text(description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Spacer(Modifier.width(12.dp))
        Switch(checked = checked, onCheckedChange = onChange)
    }
}
