package io.github.fourkings6114.iptv.diagnostics

import java.io.File
import java.util.Collections
import java.util.IdentityHashMap
import java.util.concurrent.atomic.AtomicBoolean

/** Local, bounded diagnostics. Exception messages and thread names can contain URLs/tokens. */
class CrashReports(private val file: File, private val environment: () -> String) {
    companion object { const val MAX_CHARS = 24_000 }

    fun read(): String? = runCatching {
        if (!file.isFile || file.length() > MAX_CHARS * 4L) null else file.readText().take(MAX_CHARS)
    }.getOrNull()

    fun clear() { runCatching { file.delete() } }

    internal fun record(thread: Thread, failure: Throwable) {
        val report = render(thread, failure)
        file.parentFile?.mkdirs()
        val temporary = File(file.parentFile, file.name + ".tmp")
        try {
            temporary.writeText(report)
            check(temporary.renameTo(file)) { "Unable to replace local crash report" }
        } finally { temporary.delete() }
    }

    internal fun render(thread: Thread, failure: Throwable): String = buildString {
        appendLine("IPTV local fatal exception report")
        appendLine(environment().take(1200))
        appendLine("Thread role: " + if (thread.name == "main") "main" else "worker")
        appendLine("Exception messages, URLs, headers, file paths, thread names and user input omitted.")
        val seen = Collections.newSetFromMap(IdentityHashMap<Throwable, Boolean>())
        var current: Throwable? = failure
        var count = 0
        while (current != null && seen.add(current) && count < 8) {
            appendLine(if (count == 0) current.javaClass.name else "Caused by: ${current.javaClass.name}")
            current.stackTrace.take(32).forEach { frame ->
                // Only class/method/line: no potentially sensitive exception or source-file strings.
                appendLine("  at ${frame.className}.${frame.methodName}:${frame.lineNumber}")
            }
            current = current.cause
            count++
        }
        appendLine("Native crashes, forced termination and some out-of-memory failures cannot be captured here.")
    }.take(MAX_CHARS)
}

/** Always delegate to Android's original handler: never try to resume a damaged process. */
class RecordingExceptionHandler(
    private val reports: CrashReports,
    private val delegate: Thread.UncaughtExceptionHandler,
) : Thread.UncaughtExceptionHandler {
    private val recording = AtomicBoolean(false)

    override fun uncaughtException(thread: Thread, error: Throwable) {
        try {
            if (recording.compareAndSet(false, true)) reports.record(thread, error)
        } catch (_: Throwable) {
            // Best effort only (including OOM): the original handler MUST still receive this crash.
        } finally {
            delegate.uncaughtException(thread, error)
        }
    }
}
