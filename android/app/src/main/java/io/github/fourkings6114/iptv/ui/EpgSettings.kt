package io.github.fourkings6114.iptv.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import io.github.fourkings6114.iptv.core.persianNumber
import io.github.fourkings6114.iptv.epg.EpgGuide
import io.github.fourkings6114.iptv.epg.epgClock
import io.github.fourkings6114.iptv.epg.normalizeEpgUrl

@Composable
internal fun EpgSettingsContent(sourceTitle: String, configured: Boolean, guide: EpgGuide?, matchedChannels: Int,
                                now: Long, busy: Boolean, error: String?, enabled: Boolean,
                                onConfigure: () -> Unit, onRefresh: () -> Unit, onCancel: () -> Unit) {
    Text("راهنمای برنامه‌ها (EPG)", style = MaterialTheme.typography.titleMedium)
    Text("فقط برای فهرست «$sourceTitle»", style = MaterialTheme.typography.bodySmall)
    Text(when {
        !configured -> "برای نمایش برنامهٔ فعلی و بعدی، لینک XMLTV یا XMLTV.gz منبع مجاز را اینجا تنظیم کن."
        guide == null -> "لینک XMLTV ذخیره شده؛ راهنمای قابل‌استفاده هنوز دریافت نشده، قدیمی شده یا از کش پاک شده است."
        else -> "برنامه برای ${persianNumber(matchedChannels)} کانال این فهرست · دریافت در ${epgClock(guide.fetchedAt, now)}"
    }, style = MaterialTheme.typography.bodySmall)
    if (guide?.stale(now) == true) Text("راهنما بیش از ۲۴ ساعت پیش دریافت شده؛ برای برنامه‌های تازه دوباره دریافت کن.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.tertiary)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(onClick = onConfigure, enabled = enabled && !busy, modifier = Modifier.weight(1f)) { Text("تنظیم لینک XMLTV") }
        if (configured) Button(onClick = onRefresh, enabled = enabled && !busy, modifier = Modifier.weight(1f)) { Text("دریافت راهنما") }
    }
    if (busy) {
        LinearProgressIndicator(Modifier.fillMaxWidth())
        TextButton(onClick = onCancel) { Text("لغو دریافت راهنما") }
    }
    if (error != null) Text(error, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
    Text("ساعت‌ها با منطقهٔ زمانی گوشی نمایش داده می‌شوند. فقط TVG ID دقیق و منطبق استفاده می‌شود؛ نام کانال برای حدس‌زدن برنامه کافی نیست و همهٔ کانال‌ها راهنما ندارند.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Text("اگر فهرست از نسخهٔ قبلی مانده یا شناسه‌های آن بازنویسی شده‌اند، ابتدا خود فهرست را به‌روز کن؛ برای فهرست فایل، همان فایل را دوباره انتخاب کن.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    Text("دریافت راهنما فقط با دکمهٔ بالا انجام می‌شود؛ دریافت دوره‌ای و پس‌زمینه نداریم. راهنمای ذخیره‌شده تا ۴۸ ساعت برای مرور آفلاین قابل استفاده است. لینک در فضای خصوصی برنامه می‌ماند؛ توکن یا رمز را در گفتگو نفرست.",
        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

@Composable
internal fun EpgUrlDialog(sourceId: String, sourceTitle: String, initial: String,
                          onSave: (String) -> Unit, onDismiss: () -> Unit) {
    // Deliberately NOT rememberSaveable: a provider URL/token must not enter a
    // SavedState/Activity Bundle, clipboard, diagnostics or an outbound Intent.
    var raw by remember(sourceId) { mutableStateOf(initial) }
    var invalid by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("لینک راهنمای این فهرست") },
        text = {
            Column(Modifier.heightIn(max = 380.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("فهرست: $sourceTitle", style = MaterialTheme.typography.bodySmall)
                Text("لینک مستقیم XMLTV یا XMLTV.gz را وارد کن. ذخیرهٔ لینک به‌تنهایی اینترنت مصرف نمی‌کند؛ بعد «دریافت راهنما» را بزن.", style = MaterialTheme.typography.bodySmall)
                OutlinedTextField(value = raw, onValueChange = { raw = it.take(16_385); invalid = false },
                    label = { Text("لینک XMLTV (HTTP / HTTPS)") }, singleLine = true, isError = invalid,
                    textStyle = MaterialTheme.typography.bodySmall.copy(textDirection = TextDirection.Ltr),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri), modifier = Modifier.fillMaxWidth())
                if (invalid) Text("یک لینک معتبر HTTP یا HTTPS، بدون نام کاربری و رمز در بخش میزبان وارد کن.", color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall)
                Text("درخواست راهنما از هدر، کوکی یا رمزِ کانال‌های ویدئو استفاده نمی‌کند. HTTP هم تابع اجازهٔ سراسری برنامه است؛ HTTPS بدون دورزدن گواهی بررسی می‌شود.", style = MaterialTheme.typography.bodySmall)
                if (initial.isNotBlank()) TextButton(onClick = { onSave("") }) { Text("حذف لینک و راهنما") }
            }
        },
        confirmButton = { TextButton(onClick = {
            val url = runCatching { normalizeEpgUrl(raw) }.getOrNull()
            if (url == null) invalid = true else onSave(url)
        }, enabled = raw.isNotBlank()) { Text("ذخیره لینک") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("انصراف") } },
    )
}
