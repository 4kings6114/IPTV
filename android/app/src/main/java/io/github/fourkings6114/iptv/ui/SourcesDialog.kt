package io.github.fourkings6114.iptv.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.text.style.TextDirection
import androidx.compose.ui.unit.dp
import io.github.fourkings6114.iptv.core.persianNumber
import io.github.fourkings6114.iptv.data.SOURCE_PRESETS
import io.github.fourkings6114.iptv.data.SOURCE_TITLE_MAX_LENGTH
import io.github.fourkings6114.iptv.data.SourceEntity
import io.github.fourkings6114.iptv.data.isBuiltInSource
import io.github.fourkings6114.iptv.data.isFileSource

/** No URI is retained here: SAF supplies it later to the saved pending action in the model. */
data class FileImportRequest(val title: String, val sourceId: String? = null)

@Composable
fun SourcesDialog(
    sources: List<SourceEntity>, selectedId: String, busy: Boolean, error: String?,
    onSelect: (String) -> Unit, onRefresh: (SourceEntity) -> Unit,
    onRename: (String) -> Unit, onDelete: (String) -> Unit,
    onAddUrl: () -> Unit, onAddFile: () -> Unit, onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("فهرست‌ها") },
        text = {
            Column(Modifier.heightIn(max = 480.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("هر لینک یا فایل، فهرست جداگانه‌ای می‌سازد. لینک‌ها و اطلاعات منبع فقط روی گوشی نگه‌داری می‌شوند.", style = MaterialTheme.typography.bodySmall)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = onAddUrl, enabled = !busy, modifier = Modifier.weight(1f)) { Text("افزودن لینک") }
                    OutlinedButton(onClick = onAddFile, enabled = !busy, modifier = Modifier.weight(1f)) { Text("افزودن فایل") }
                }
                if (busy) LinearProgressIndicator(Modifier.fillMaxWidth())
                error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
                Text("فهرست‌های پروژه", style = MaterialTheme.typography.titleSmall)
                SOURCE_PRESETS.forEach { preset ->
                    val cached = sources.find { it.id == preset.id }
                    SourceChoice(
                        preset.id, preset.title,
                        cached?.let { "${persianNumber(it.count)} کانال · از پروژه" } ?: "برای دریافت انتخاب کن",
                        selectedId == preset.id, busy, onSelect,
                        onRefresh = { onRefresh(cached ?: SourceEntity(preset.id, preset.title, preset.url, 0, 0)) },
                    )
                }
                HorizontalDivider()
                Text("فهرست‌های شخصی", style = MaterialTheme.typography.titleSmall)
                val personal = sources.filterNot { isBuiltInSource(it.id) }
                if (personal.isEmpty()) Text("هنوز فهرست شخصی اضافه نکرده‌ای.", style = MaterialTheme.typography.bodySmall)
                personal.forEach { source ->
                    SourceChoice(
                        source.id, source.title,
                        "${persianNumber(source.count)} کانال · ${if (source.isFileSource) "فایل؛ برای به‌روزرسانی دوباره انتخاب شود" else "لینک شخصی"}",
                        selectedId == source.id, busy, onSelect,
                        onRefresh = { onRefresh(source) },
                        onRename = { onRename(source.id) }, onDelete = { onDelete(source.id) },
                    )
                }
                Text("حذف یا تغییر فهرست، علاقه‌مندی‌ها و سابقه را پاک نمی‌کند. وجود کانال در فهرست نیز تضمین پخش یا مجوز محتوا نیست.", style = MaterialTheme.typography.bodySmall)
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("بستن") } },
    )
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SourceChoice(
    id: String, title: String, description: String, selected: Boolean, busy: Boolean,
    onSelect: (String) -> Unit, onRefresh: () -> Unit,
    onRename: (() -> Unit)? = null, onDelete: (() -> Unit)? = null,
) {
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier.fillMaxWidth().selectable(selected = selected, enabled = !busy, role = Role.RadioButton, onClick = { onSelect(id) })
                .semantics { contentDescription = "انتخاب فهرست $title" }.padding(vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            RadioButton(selected = selected, onClick = null)
            Spacer(Modifier.width(8.dp))
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.bodyMedium)
                Text(description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            TextButton(onClick = onRefresh, enabled = !busy, modifier = Modifier.semantics { contentDescription = "به‌روزرسانی فهرست $title" }) { Text("به‌روزرسانی") }
            onRename?.let { action -> TextButton(onClick = action, enabled = !busy, modifier = Modifier.semantics { contentDescription = "تغییر نام فهرست $title" }) { Text("تغییر نام") } }
            onDelete?.let { action -> TextButton(onClick = action, enabled = !busy, modifier = Modifier.semantics { contentDescription = "حذف فهرست $title" }) { Text("حذف", color = MaterialTheme.colorScheme.error) } }
        }
    }
}

@Composable
fun SourceImportDialog(isFile: Boolean, onSubmit: (String, String) -> Unit, onDismiss: () -> Unit) {
    var title by rememberSaveable(isFile) { mutableStateOf("") }
    var url by rememberSaveable(isFile) { mutableStateOf("") }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(if (isFile) "افزودن فهرست از فایل" else "افزودن فهرست از لینک") },
        text = {
            Column(Modifier.heightIn(max = 440.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(if (isFile) "یک نام انتخاب کن؛ سپس فایل M3U را از انتخاب‌گر گوشی باز کن. فایل‌های قبلی جایگزین نمی‌شوند. برای به‌روزرسانی همین فهرست، از دکمهٔ به‌روزرسانی آن استفاده کن." else "نام دلخواه و لینک فهرست کانال‌ها را وارد کن، نه آدرس یک استریم. لینک فقط روی گوشی ذخیره می‌شود؛ اطلاعات حساب را برای کسی نفرست.", style = MaterialTheme.typography.bodySmall)
                SourceTitleField(title, { title = it })
                if (!isFile) OutlinedTextField(
                    value = url, onValueChange = { url = it }, singleLine = true,
                    modifier = Modifier.fillMaxWidth(), label = { Text("لینک HTTP / HTTPS") },
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    textStyle = MaterialTheme.typography.bodyMedium.copy(textDirection = TextDirection.Ltr),
                )
            }
        },
        confirmButton = { TextButton(enabled = title.isNotBlank() && (isFile || url.isNotBlank()), onClick = { onSubmit(title, url) }) { Text(if (isFile) "انتخاب فایل M3U" else "افزودن فهرست") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("انصراف") } },
    )
}

@Composable
fun SourceRenameDialog(source: SourceEntity, onSave: (String) -> Unit, onDismiss: () -> Unit) {
    var title by rememberSaveable(source.id) { mutableStateOf(source.title) }
    AlertDialog(
        onDismissRequest = onDismiss, title = { Text("تغییر نام فهرست") },
        text = { SourceTitleField(title, { title = it }) },
        confirmButton = { TextButton(enabled = title.isNotBlank(), onClick = { onSave(title) }) { Text("ذخیره نام") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("انصراف") } },
    )
}

@Composable
fun SourceDeleteDialog(source: SourceEntity, onDelete: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss, title = { Text("حذف این فهرست؟") },
        text = { Text("فهرست «${source.title}» حذف می‌شود. علاقه‌مندی‌ها، سابقه و کانال‌های مشترک با فهرست‌های دیگر باقی می‌مانند. اگر این فهرست انتخاب شده باشد، به فهرست نمونه برمی‌گردی.") },
        confirmButton = { TextButton(onClick = onDelete) { Text("حذف فهرست", color = MaterialTheme.colorScheme.error) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("انصراف") } },
    )
}

@Composable
private fun SourceTitleField(value: String, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value, onValueChange = { onChange(it.take(SOURCE_TITLE_MAX_LENGTH)) }, singleLine = true,
        modifier = Modifier.fillMaxWidth(), label = { Text("نام فهرست") },
        supportingText = { Text("${persianNumber(value.length)} از ${persianNumber(SOURCE_TITLE_MAX_LENGTH)} نویسه") },
    )
}
