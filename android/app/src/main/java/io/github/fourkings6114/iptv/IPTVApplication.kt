package io.github.fourkings6114.iptv

import android.app.Application
import android.os.Build
import java.io.File
import io.github.fourkings6114.iptv.diagnostics.CrashReports
import io.github.fourkings6114.iptv.diagnostics.RecordingExceptionHandler
import androidx.media3.common.util.Log
import androidx.media3.common.util.UnstableApi
import androidx.room.Room
import coil.ImageLoader
import coil.ImageLoaderFactory
import io.github.fourkings6114.iptv.data.AppDatabase
import io.github.fourkings6114.iptv.data.PlaylistRepository
import io.github.fourkings6114.iptv.data.SettingsStore
import io.github.fourkings6114.iptv.network.NetworkClients
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob

class IPTVApplication : Application(), ImageLoaderFactory {
    private val applicationScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    val crashReports by lazy {
        CrashReports(File(filesDir, "diagnostics/last-crash.txt")) {
            val runtime = Runtime.getRuntime()
            "Version ${BuildConfig.VERSION_NAME}; Android API ${Build.VERSION.SDK_INT}\n" +
                "Device ${Build.MANUFACTURER.take(60)} ${Build.MODEL.take(80)}\n" +
                "Heap bytes max=${runtime.maxMemory()} total=${runtime.totalMemory()} free=${runtime.freeMemory()}"
        }
    }
    val settings by lazy { SettingsStore(this, applicationScope) }
    val database by lazy { Room.databaseBuilder(this, AppDatabase::class.java, "iptv.db").build() }
    val network by lazy { NetworkClients.create(allowHttp = { settings.state.value.allowHttp }) }
    val repository by lazy { PlaylistRepository(this, database.channels(), network) }

    @androidx.annotation.OptIn(UnstableApi::class)
    override fun onCreate() {
        super.onCreate()
        Thread.getDefaultUncaughtExceptionHandler()?.let { original ->
            if (original !is RecordingExceptionHandler) {
                Thread.setDefaultUncaughtExceptionHandler(RecordingExceptionHandler(crashReports, original))
            }
        }
        // Diagnostics offered by the app contain codes, never stream URLs/headers.
        Log.setLogLevel(Log.LOG_LEVEL_OFF)
    }

    override fun newImageLoader(): ImageLoader = ImageLoader.Builder(this)
        .okHttpClient(network)
        .crossfade(true)
        .build()
}
