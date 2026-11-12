package io.github.fourkings6114.iptv.data

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.emptyPreferences
import androidx.datastore.preferences.core.floatPreferencesKey
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import java.io.IOException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.catch
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

private val Context.preferences by preferencesDataStore("player_settings")

const val DEFAULT_THEME_STYLE = "graphite"
private val allowedThemeStyles = setOf("graphite", "navy", "plum")
private val allowedBufferProfiles = setOf("auto", "tiny", "small", "normal", "medium", "large", "very_large")
private val allowedOrientations = setOf("auto", "landscape", "portrait")
private val allowedResizeModes = setOf("fit", "fill", "zoom")
private val allowedRewindSeconds = setOf(5, 10, 15, 30, 60)
private val allowedControllerTimeouts = setOf(0, 2_000, 5_000, 10_000, 20_000)
private val allowedSleepTimers = setOf(0, 15, 30, 45, 60, 90, 120)
private val allowedScales = setOf(0.85f, 0.9f, 1.0f, 1.1f, 1.2f, 1.3f)

data class AppSettings(
    val sourceId: String = SAMPLE_SOURCE_ID,
    val allowHttp: Boolean = false,
    val showLogos: Boolean = true,
    val loaded: Boolean = false,
    val autoPictureInPicture: Boolean = false,
    val epgUrl: String = "",
    val themeStyle: String = DEFAULT_THEME_STYLE,
    val bufferProfile: String = "normal",
    val playerOrientation: String = "auto",
    val hideSourceSelector: Boolean = false,
    val sleepTimerMinutes: Int = 0,
    val rewindSeconds: Int = 10,
    val fullScreenResizeMode: String = "fit",
    val windowedResizeMode: String = "fit",
    val controllerTimeoutMs: Int = 5_000,
    val showPlayerClock: Boolean = false,
    val audioDecoderFallback: Boolean = true,
    val textScale: Float = 1.0f,
    val screenScale: Float = 1.0f,
    val parentalControlEnabled: Boolean = false,
    val parentalPinSalt: String = "",
    val parentalPinHash: String = "",
)

class SettingsStore(context: Context, scope: CoroutineScope) {
    private val store = context.preferences
    private val source = stringPreferencesKey("source_id")
    private val http = booleanPreferencesKey("allow_cleartext_http")
    private val logos = booleanPreferencesKey("show_logos")
    private val autoPip = booleanPreferencesKey("automatic_picture_in_picture")
    private val theme = stringPreferencesKey("theme_style")
    private val buffer = stringPreferencesKey("network_buffer_profile")
    private val orientation = stringPreferencesKey("player_orientation")
    private val hideSource = booleanPreferencesKey("hide_source_selector")
    private val sleepTimer = intPreferencesKey("sleep_timer_minutes")
    private val rewind = intPreferencesKey("rewind_seconds")
    private val fullScreenResize = stringPreferencesKey("fullscreen_resize_mode")
    private val windowedResize = stringPreferencesKey("windowed_resize_mode")
    private val controllerTimeout = intPreferencesKey("controller_timeout_ms")
    private val playerClock = booleanPreferencesKey("show_player_clock")
    private val decoderFallback = booleanPreferencesKey("audio_decoder_fallback")
    private val textScaleKey = floatPreferencesKey("text_scale")
    private val screenScaleKey = floatPreferencesKey("screen_scale")
    private val parentalEnabled = booleanPreferencesKey("parental_control_enabled")
    private val parentalSalt = stringPreferencesKey("parental_pin_salt")
    private val parentalHash = stringPreferencesKey("parental_pin_hash")
    private fun epgKey(id: String) = stringPreferencesKey("xmltv_url_" + id)

    val state = store.data.catch { error ->
        if (error is IOException) emit(emptyPreferences()) else throw error
    }.map { values ->
        val id = values[source] ?: SAMPLE_SOURCE_ID
        AppSettings(
            sourceId = id,
            allowHttp = values[http] ?: false,
            showLogos = values[logos] ?: true,
            loaded = true,
            autoPictureInPicture = values[autoPip] ?: false,
            epgUrl = values[epgKey(id)].orEmpty(),
            themeStyle = values[theme]?.takeIf { it in allowedThemeStyles } ?: DEFAULT_THEME_STYLE,
            bufferProfile = values[buffer]?.takeIf { it in allowedBufferProfiles } ?: "normal",
            playerOrientation = values[orientation]?.takeIf { it in allowedOrientations } ?: "auto",
            hideSourceSelector = values[hideSource] ?: false,
            sleepTimerMinutes = values[sleepTimer]?.takeIf { it in allowedSleepTimers } ?: 0,
            rewindSeconds = values[rewind]?.takeIf { it in allowedRewindSeconds } ?: 10,
            fullScreenResizeMode = values[fullScreenResize]?.takeIf { it in allowedResizeModes } ?: "fit",
            windowedResizeMode = values[windowedResize]?.takeIf { it in allowedResizeModes } ?: "fit",
            controllerTimeoutMs = values[controllerTimeout]?.takeIf { it in allowedControllerTimeouts } ?: 5_000,
            showPlayerClock = values[playerClock] ?: false,
            audioDecoderFallback = values[decoderFallback] ?: true,
            textScale = values[textScaleKey]?.takeIf { it in allowedScales } ?: 1.0f,
            screenScale = values[screenScaleKey]?.takeIf { it in allowedScales } ?: 1.0f,
            parentalControlEnabled = values[parentalEnabled] ?: false,
            parentalPinSalt = values[parentalSalt].orEmpty(),
            parentalPinHash = values[parentalHash].orEmpty(),
        )
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
    suspend fun bufferProfile(id: String) {
        if (id in allowedBufferProfiles) store.edit { it[buffer] = id }
    }
    suspend fun playerOrientation(id: String) {
        if (id in allowedOrientations) store.edit { it[orientation] = id }
    }
    suspend fun hideSourceSelector(hide: Boolean) { store.edit { it[hideSource] = hide } }
    suspend fun sleepTimerMinutes(minutes: Int) {
        if (minutes in allowedSleepTimers) store.edit { it[sleepTimer] = minutes }
    }
    suspend fun rewindSeconds(seconds: Int) {
        if (seconds in allowedRewindSeconds) store.edit { it[rewind] = seconds }
    }
    suspend fun fullScreenResizeMode(id: String) {
        if (id in allowedResizeModes) store.edit { it[fullScreenResize] = id }
    }
    suspend fun windowedResizeMode(id: String) {
        if (id in allowedResizeModes) store.edit { it[windowedResize] = id }
    }
    suspend fun controllerTimeoutMs(timeout: Int) {
        if (timeout in allowedControllerTimeouts) store.edit { it[controllerTimeout] = timeout }
    }
    suspend fun showPlayerClock(show: Boolean) { store.edit { it[playerClock] = show } }
    suspend fun audioDecoderFallback(enabled: Boolean) { store.edit { it[decoderFallback] = enabled } }
    suspend fun textScale(scale: Float) {
        if (scale in allowedScales) store.edit { it[textScaleKey] = scale }
    }
    suspend fun screenScale(scale: Float) {
        if (scale in allowedScales) store.edit { it[screenScaleKey] = scale }
    }
    suspend fun setParentalPin(pin: String) {
        val (salt, hash) = withContext(Dispatchers.Default) { ParentalPin.create(pin) }
        store.edit {
            it[parentalSalt] = salt
            it[parentalHash] = hash
            it[parentalEnabled] = true
        }
    }
    suspend fun verifyParentalPin(pin: String): Boolean {
        val values = store.data.first()
        return withContext(Dispatchers.Default) {
            ParentalPin.verify(pin, values[parentalSalt].orEmpty(), values[parentalHash].orEmpty())
        }
    }
    suspend fun parentalControl(enabled: Boolean) {
        store.edit { it[parentalEnabled] = enabled }
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
