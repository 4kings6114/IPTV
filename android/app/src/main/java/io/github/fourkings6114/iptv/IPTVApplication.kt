package io.github.fourkings6114.iptv

import android.app.Application
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
    val settings by lazy { SettingsStore(this, applicationScope) }
    val database by lazy { Room.databaseBuilder(this, AppDatabase::class.java, "iptv.db").build() }
    val network by lazy { NetworkClients.create(allowHttp = { settings.state.value.allowHttp }) }
    val repository by lazy { PlaylistRepository(this, database.channels(), network) }

    @androidx.annotation.OptIn(UnstableApi::class)
    override fun onCreate() {
        super.onCreate()
        // Diagnostics offered by the app contain codes, never stream URLs/headers.
        Log.setLogLevel(Log.LOG_LEVEL_OFF)
    }

    override fun newImageLoader(): ImageLoader = ImageLoader.Builder(this)
        .okHttpClient(network)
        .crossfade(true)
        .build()
}
