package io.github.fourkings6114.iptv.data

import java.io.IOException

const val SOURCE_TITLE_MAX_LENGTH = 80
const val DEFAULT_LINK_SOURCE_TITLE = "فهرست از لینک شخصی"
const val DEFAULT_FILE_SOURCE_TITLE = "فهرست از فایل"

fun isBuiltInSource(id: String): Boolean = SOURCE_PRESETS.any { it.id == id }
val SourceEntity.isFileSource: Boolean get() = !isBuiltInSource(id) && url.isBlank()

class InvalidSourceNameException : IllegalArgumentException("Invalid source title")
class SourceMissingException : IOException("Source is no longer available")
class ProtectedSourceException : IllegalArgumentException("Built-in sources cannot be renamed or deleted")
class FileSourceSelectionRequiredException : IOException("Select the file again to refresh this source")
class NotAFileSourceException : IllegalArgumentException("This source is not a file import")

/** Preserve Persian ZWNJ, but discard control/bidi-override characters and bound the title. */
fun sourceTitle(raw: String): String {
    val title = raw.replace(Regex("\\s+"), " ").filterNot {
        it.isISOControl() || it in '\u202a'..'\u202e' || it in '\u2066'..'\u2069'
    }.trim()
    if (title.isEmpty() || title.length > SOURCE_TITLE_MAX_LENGTH) throw InvalidSourceNameException()
    return title
}
