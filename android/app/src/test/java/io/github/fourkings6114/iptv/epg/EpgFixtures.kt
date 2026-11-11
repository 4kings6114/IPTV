package io.github.fourkings6114.iptv.epg

import java.io.ByteArrayOutputStream
import java.time.Instant
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.zip.GZIPOutputStream

internal val GUIDE_NOW = Instant.parse("2026-11-10T12:00:00Z").toEpochMilli()
internal fun xmlTime(time: Long) = DateTimeFormatter.ofPattern("uuuuMMddHHmmss xx").withZone(ZoneOffset.UTC).format(Instant.ofEpochMilli(time))
internal fun programme(id: String = "Exact.ID", title: String = "Current programme", start: Long = GUIDE_NOW - 60_000,
                       stop: Long? = GUIDE_NOW + 60_000): String =
    """<programme channel="$id" start="${xmlTime(start)}" ${stop?.let { "stop=\"${xmlTime(it)}\"" }.orEmpty()}><title>$title</title></programme>"""
internal fun guideXml(id: String = "Exact.ID", title: String = "Current programme") =
    "<tv>${programme(id, title)}${programme(id, "Next programme", GUIDE_NOW + 60_000, GUIDE_NOW + 120_000)}</tv>"
internal fun gzip(bytes: ByteArray): ByteArray = ByteArrayOutputStream().also { output -> GZIPOutputStream(output).use { it.write(bytes) } }.toByteArray()

internal suspend inline fun <reified T : Throwable> expectEpgFailure(action: () -> Unit): T {
    try { action() } catch (failure: Throwable) { if (failure is T) return failure else throw failure }
    throw AssertionError("Expected ${T::class.java.simpleName}")
}
