package io.github.fourkings6114.iptv.core

import java.security.MessageDigest
import java.util.Locale

data class Channel(
    val id: String,
    val name: String,
    val group: String,
    val url: String,
    val logo: String = "",
    val tvgId: String = "",
    val headers: Map<String, String> = emptyMap(),
) {
    val isCleartext: Boolean get() = url.startsWith("http://", ignoreCase = true)
    val searchText: String = normalizeSearch("$name $group")
}

fun sha256(value: String): String = MessageDigest.getInstance("SHA-256")
    .digest(value.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }

private val diacritics = Regex("[\\u064B-\\u065F\\u0670]")
private val whitespace = Regex("\\s+")

fun normalizeSearch(value: String): String = value
    .lowercase(Locale.ROOT)
    .replace('ي', 'ی').replace('ى', 'ی').replace('ك', 'ک')
    .replace('\u200c', ' ').replace("\u0640", "")
    .replace(diacritics, "")
    .replace(whitespace, " ").trim()

fun persianNumber(number: Number): String = number.toString().map { character ->
    if (character in '0'..'9') '۰' + (character - '0') else character
}.joinToString("")
