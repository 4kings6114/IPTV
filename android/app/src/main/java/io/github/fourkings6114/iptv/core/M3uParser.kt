package io.github.fourkings6114.iptv.core

import java.net.URI
import java.net.URLDecoder
import java.util.Locale
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

class PlaylistParseException(val reason: Reason) : Exception(reason.name) {
    enum class Reason { EMPTY, HLS_MANIFEST, TOO_MANY_CHANNELS }
}

data class ParsedPlaylist(val channels: List<Channel>, val skipped: Int)

/** Pure Kotlin: playlist ingestion is deliberately separate from Media3 playback. */
object M3uParser {
    const val MAX_CHANNELS = 20_000
    private val attribute = Regex("([a-zA-Z0-9_-]+)\\s*=\\s*(?:\"([^\"]*)\"|'([^']*)'|([^\\s]+))")
    private val headerName = Regex("[a-zA-Z0-9-]{1,80}")
    private val forbiddenHeaders = setOf(
        "host", "connection", "content-length", "transfer-encoding", "accept-encoding",
        "range", "upgrade", "te", "trailer", "proxy-authorization", "proxy-connection",
    )

    fun parse(text: String, baseUrl: String? = null): ParsedPlaylist {
        if (text.lineSequence().any { it.trim().startsWith("#EXT-X-", ignoreCase = true) }) {
            throw PlaylistParseException(PlaylistParseException.Reason.HLS_MANIFEST)
        }
        var pending: Map<String, String>? = null
        var name = ""
        var group = ""
        var headers = linkedMapOf<String, String>()
        var skipped = 0
        val channels = linkedMapOf<String, Channel>()
        for (raw in text.removePrefix("\uFEFF").lineSequence()) {
            val line = raw.trim()
            when {
                line.isBlank() -> Unit
                line.startsWith("#EXTINF:", true) -> {
                    if (pending != null) skipped++
                    val separator = displaySeparator(line)
                    pending = if (separator < 0) null else attribute.findAll(line.substring(0, separator))
                        .associate { match ->
                            match.groupValues[1].lowercase(Locale.ROOT) to
                                match.groupValues.drop(2).firstOrNull { it.isNotEmpty() }.orEmpty()
                        }
                    name = if (separator < 0) "" else line.substring(separator + 1).trim().take(300)
                    group = pending?.get("group-title").orEmpty().take(200)
                    headers = linkedMapOf()
                    if (separator < 0) skipped++
                }
                line.startsWith("#EXTGRP:", true) && pending != null -> group = line.substringAfter(':').trim().take(200)
                line.startsWith("#EXTVLCOPT:", true) && pending != null -> {
                    val option = line.substringAfter(':').substringBefore('=').lowercase(Locale.ROOT)
                    val value = line.substringAfter('=', "")
                    val key = when (option) {
                        "http-user-agent" -> "user-agent"
                        "http-referrer", "http-referer" -> "referer"
                        "http-origin" -> "origin"
                        "http-cookie" -> "cookie"
                        else -> ""
                    }
                    putHeader(headers, key, value)
                }
                line.startsWith("#KODIPROP:", true) && pending != null -> {
                    val option = line.substringAfter(':').substringBefore('=').lowercase(Locale.ROOT)
                    if (option in setOf("inputstream.adaptive.stream_headers", "inputstream.adaptive.manifest_headers")) {
                        queryHeaders(line.substringAfter('=', ""), headers)
                    }
                }
                line.startsWith("#EXTHTTP:", true) && pending != null -> {
                    val json = runCatching { Json.parseToJsonElement(line.substringAfter(':')) as? JsonObject }.getOrNull()
                    json?.forEach { (key, value) ->
                        putHeader(headers, key, (value as? JsonPrimitive)?.contentOrNull.orEmpty())
                    }
                }
                line.startsWith('#') -> Unit
                else -> {
                    val metadata = pending
                    if (metadata != null) {
                        val url = httpUrl(line.substringBefore('|'), baseUrl)
                        if ('|' in line) queryHeaders(line.substringAfter('|'), headers)
                        if (url != null && name.isNotBlank()) {
                            val tvgId = (metadata["x-original-tvg-id"] ?: metadata["tvg-id"]).orEmpty().take(300)
                            val identity = JsonArray(listOf(JsonPrimitive(tvgId), JsonPrimitive(url), JsonObject(headers.toSortedMap().mapValues { JsonPrimitive(it.value) })))
                            val id = sha256(identity.toString())
                            channels.putIfAbsent(id, Channel(
                                id = id, name = name, group = group.ifBlank { "بدون دسته‌بندی" },
                                url = url, logo = httpUrl(metadata["tvg-logo"].orEmpty()).orEmpty(),
                                tvgId = tvgId, headers = headers.toMap(),
                            ))
                            if (channels.size > MAX_CHANNELS) {
                                throw PlaylistParseException(PlaylistParseException.Reason.TOO_MANY_CHANNELS)
                            }
                        } else skipped++
                    }
                    pending = null
                    headers = linkedMapOf()
                }
            }
        }
        if (pending != null) skipped++
        if (channels.isEmpty()) throw PlaylistParseException(PlaylistParseException.Reason.EMPTY)
        return ParsedPlaylist(channels.values.toList(), skipped)
    }

    fun httpUrl(value: String, baseUrl: String? = null): String? = runCatching {
        if (value.isBlank() || value.length > 16_384 || value.any { it.code < 0x20 }) return null
        var uri = URI(value.trim())
        if (!uri.isAbsolute && baseUrl != null) uri = URI(baseUrl).resolve(uri)
        if (uri.scheme?.lowercase(Locale.ROOT) !in setOf("http", "https") || uri.host.isNullOrBlank() || uri.userInfo != null) return null
        if (uri.port < -1 || uri.port > 65535) return null
        uri.toASCIIString()
    }.getOrNull()

    private fun displaySeparator(value: String): Int {
        var quote: Char? = null
        value.forEachIndexed { index, character ->
            if (character == '\"' || character == '\'') {
                if (quote == null) quote = character else if (quote == character) quote = null
            } else if (character == ',' && quote == null) return index
        }
        return -1
    }

    private fun queryHeaders(query: String, target: MutableMap<String, String>) {
        query.split('&').take(32).forEach { pair ->
            val key = decode(pair.substringBefore('=')) ?: return@forEach
            val value = decode(pair.substringAfter('=', "")) ?: return@forEach
            putHeader(target, key, value)
        }
    }

    private fun decode(value: String): String? = runCatching { URLDecoder.decode(value, "UTF-8") }.getOrNull()

    private fun putHeader(target: MutableMap<String, String>, key: String, value: String) {
        val normalized = key.lowercase(Locale.ROOT)
        if (target.size < 32 && headerName.matches(normalized) && normalized !in forbiddenHeaders &&
            value.isNotBlank() && value.length <= 4096 && value.all { it.code in 0x20..0x7e }) {
            target[normalized] = value
        }
    }
}
