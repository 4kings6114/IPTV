package io.github.fourkings6114.iptv.diagnostics

import java.io.File
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class CrashReportsTest {
    @get:Rule val folder = TemporaryFolder()
    private fun reports(file: File = File(folder.root, "last-crash.txt")) = CrashReports(file) { "Version trial; API 33" }

    @Test fun recordsClassAndFramesButNeverMessagesThreadNamesOrFileNames() {
        val store = reports()
        val error = IllegalStateException("https://private.test/?token=secret Cookie=private", IllegalArgumentException("password=private"))
        error.stackTrace = arrayOf(StackTraceElement("example.Player", "start", "/private/account/file.kt", 42))
        store.record(Thread("OkHttp https://private.test/secret"), error)
        val text = store.read()!!
        assertTrue(text.contains("java.lang.IllegalStateException"))
        assertTrue(text.contains("Caused by: java.lang.IllegalArgumentException"))
        assertTrue(text.contains("example.Player.start:42"))
        listOf("private.test", "token=secret", "Cookie=private", "password=private", "/private/account", "OkHttp").forEach { assertFalse(it, text.contains(it)) }
    }
    @Test fun handlesCyclicCauseChains() {
        val first = IllegalStateException("private")
        val second = IllegalArgumentException("private")
        first.initCause(second); second.initCause(first)
        val text = reports().render(Thread.currentThread(), first)
        assertEquals(1, Regex("java.lang.IllegalStateException").findAll(text).count())
        assertEquals(1, Regex("java.lang.IllegalArgumentException").findAll(text).count())
    }
    @Test fun alwaysDelegatesTheOriginalFailure() {
        val store = reports()
        val expected = IllegalStateException("hidden")
        var delegated: Throwable? = null
        val handler = RecordingExceptionHandler(store) { _, error -> delegated = error }
        handler.uncaughtException(Thread.currentThread(), expected)
        assertSame(expected, delegated)
        assertNotNull(store.read())
    }
    @Test fun storageFailureDoesNotReplaceOrSwallowTheCrash() {
        val directory = folder.newFolder("not-a-file")
        File(directory, "keep").writeText("occupied")
        var delegated: Throwable? = null
        val handler = RecordingExceptionHandler(reports(directory)) { _, error -> delegated = error }
        val original = IllegalStateException("original")
        handler.uncaughtException(Thread.currentThread(), original)
        assertSame(original, delegated)
    }
    @Test fun boundsLargeTracesAndCanDeleteTheReport() {
        val store = reports()
        val error = IllegalArgumentException("sensitive".repeat(200_000))
        error.stackTrace = Array(10_000) { StackTraceElement("Example".repeat(150), "render", "hidden", it) }
        store.record(Thread.currentThread(), error)
        assertTrue(store.read()!!.length <= CrashReports.MAX_CHARS)
        store.clear(); assertNull(store.read())
    }
    @Test fun ignoresAnOversizedOnDiskFile() {
        val file = folder.newFile("oversized.txt")
        file.writeBytes(ByteArray(CrashReports.MAX_CHARS * 4 + 1))
        assertNull(reports(file).read())
    }
}
