package io.github.fourkings6114.iptv.data

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import java.io.IOException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn

private val Context.preferences by preferencesDataStore("player_settings")

const val DEFAULT_THEME_STYLE = "graphite"
private val allowedThemeStyles = setOf("graphite", "navy", "plum")

data class AppSettings(
    val sourceId: String = SAMPLE_SOURCE_ID,
    val allowHttp: Boolean = false,
    val showLogos: Boolean = true,
    val loaded: Boolean = false,
    val autoPictureInPicture: Boolean = false,
    val epgUrl: String = "",
    val themeStyle: String = DEFAULT_THEME_STYLE,
)

class SettingsStore(context: Context, scope: CoroutineScope) {
    private val store = context.preferences
    private val source = stringPreferencesKey("source_id")
    private val http = booleanPreferencesKey("allow_cleartext_http")
    private val logos = booleanPreferencesKey("show_logos")
    private val autoPip = booleanPreferencesKey("automatic_picture_in_picture")
    private val theme = stringPreferencesKey("theme_style")
    private fun epgKey(id: String) = stringPreferencesKey("xmltv_url_" + id)
    val state = store.data.catch { error ->
        if (error is IOException) emit(emptyPreferences()) else throw error
    }.map { values ->
        val id = values[source] ?: SAMPLE_SOURCE_ID
        AppSettings(id, values[http] ?: false, values[logos] ?: true,
            loaded = true, autoPictureInPicture = values[autoPip] ?: false, epgUrl = values[epgKey(id)].orEmpty(),
            themeStyle = values[theme]?.takeIf { it in allowedThemeStyles } ?: DEFAULT_THEME_STYLE)
    }.stateIn(scope, SharingStarted.Eagerly, AppSettings())

    suspend fun selectSource(id: String) { store.edit { it[source] = id } }
    suspend fun forgetSource(id: String) {
        // Compare inside DataStore's transaction so a newer selection is not overwritten.
        store.edit {
            if (it[source] == id) it[source] = SAMPLE_SOURCE_ID
            it.remove(epgKey(id))
        }
    }
    suspend fun allowHttp(allow: Boolean) { store.edit { it[http] = allow } }
    suspend fun showLogos(show: Boolean) { store.edit { it[logos] = show } }
    suspend fun automaticPictureInPicture(allow: Boolean) { store.edit { it[autoPip] = allow } }
    suspend fun themeStyle(id: String) {
        if (id !in allowedThemeStyles) return
        store.edit { it[theme] = id }
    }
    suspend fun epgUrlFor(id: String): String = store.data.first()[epgKey(id)].orEmpty()
    suspend fun epgUrl(id: String, url: String) {
        store.edit { if (url.isBlank()) it.remove(epgKey(id)) else it[epgKey(id)] = url }
    }
}

const val SAMPLE_SOURCE_ID = "sample-v1"

data class SourcePreset(val id: String, val title: String, val file: String) {
    val url: String get() = "https://raw.githubusercontent.com/4kings6114/IPTV/main/$file"
}

val SOURCE_PRESETS = listOf(
    SourcePreset(SAMPLE_SOURCE_ID, "آزمون اولیه · ۲۰ کانال", ""),
    SourcePreset("persian", "کانال‌های فارسی", "persian.m3u"),
    SourcePreset("fa-en", "فارسی و انگلیسی", "profile-fa-en.m3u"),
    SourcePreset("low-bandwidth", "پروفایل کم‌مصرف", "profile-low-bandwidth.m3u"),
    SourcePreset("all", "همهٔ کانال‌ها", "playlist.m3u"),
)
