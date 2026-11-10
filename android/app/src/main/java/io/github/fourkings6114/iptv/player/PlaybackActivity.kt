package io.github.fourkings6114.iptv.player

import android.content.ClipData
import android.content.ClipboardManager
import android.content.pm.ActivityInfo
import android.content.res.Configuration
import android.os.Build
import android.os.Bundle
import android.view.ViewGroup
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.SystemBarStyle
import androidx.compose.foundation.background
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.lifecycle.lifecycleScope
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaLibraryInfo
import androidx.media3.common.MediaMetadata
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.Tracks
import androidx.media3.common.util.UnstableApi
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.exoplayer.upstream.DefaultLoadErrorHandlingPolicy
import androidx.media3.ui.AspectRatioFrameLayout
import androidx.media3.ui.PlayerView
import io.github.fourkings6114.iptv.BuildConfig
import io.github.fourkings6114.iptv.IPTVApplication
import io.github.fourkings6114.iptv.core.Channel
import io.github.fourkings6114.iptv.network.NetworkClients
import io.github.fourkings6114.iptv.ui.IPTVTheme
import java.net.URI
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import okhttp3.HttpUrl.Companion.toHttpUrl

@androidx.annotation.OptIn(UnstableApi::class)
class PlaybackActivity : ComponentActivity() {
    companion object { const val CHANNEL_ID = "channel_id" }
    private val app get() = application as IPTVApplication
    private var channel by mutableStateOf<Channel?>(null)
    private var player by mutableStateOf<ExoPlayer?>(null)
    private var failure by mutableStateOf<PlaybackFailure?>(null)
    private var buffering by mutableStateOf(true)
    private var formatInfo by mutableStateOf("")
    private var copied by mutableStateOf(false)
    private var zoom by mutableStateOf(false)
    private var videoView: PlayerView? = null
    private var playbackClient: okhttp3.OkHttpClient? = null
    private var resumePosition = C.TIME_UNSET
    private var wasPlaying = true
    private var historyRecorded = false
    private var started = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.BLACK),
        )
        WindowCompat.getInsetsController(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
        setContent {
            IPTVTheme {
                Box(Modifier.fillMaxSize().background(Color.Black)) {
                    AndroidView(
                        factory = { context ->
                            PlayerView(context).apply {
                                layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
                                setShowBuffering(PlayerView.SHOW_BUFFERING_NEVER)
                                setShowSubtitleButton(true)
                                setShowNextButton(false)
                                setShowPreviousButton(false)
                                setShowShuffleButton(false)
                                layoutDirection = android.view.View.LAYOUT_DIRECTION_LTR
                                videoView = this
                            }
                        },
                        update = { view ->
                            view.player = player
                            view.resizeMode = if (zoom) AspectRatioFrameLayout.RESIZE_MODE_ZOOM else AspectRatioFrameLayout.RESIZE_MODE_FIT
                        },
                        modifier = Modifier.fillMaxSize(),
                    )
                    if (buffering && failure == null) {
                        Column(Modifier.align(Alignment.Center).background(Color.Black.copy(alpha = 0.8f), RoundedCornerShape(16.dp)).padding(24.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
                            CircularProgressIndicator()
                            Text("در حال اتصال به رسانه…", color = Color.White)
                        }
                    }
                    Row(Modifier.fillMaxWidth().background(Color.Black.copy(alpha = 0.7f)).safeDrawingPadding().padding(horizontal = 8.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        IconButton(onClick = { finish() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, "بازگشت به کانال‌ها", tint = Color.White) }
                        Text(channel?.name ?: "پخش کانال", Modifier.weight(1f), color = Color.White, maxLines = 2, style = MaterialTheme.typography.titleSmall)
                        TextButton(onClick = { zoom = !zoom }) { Text(if (zoom) "نمای کامل" else "برش تصویر") }
                        TextButton(onClick = ::rotateScreen) { Text("چرخش") }
                    }
                    failure?.let { problem ->
                        Surface(
                            color = MaterialTheme.colorScheme.surface,
                            shape = RoundedCornerShape(20.dp),
                            modifier = Modifier.align(Alignment.Center).safeDrawingPadding().padding(20.dp).widthIn(max = 520.dp),
                        ) {
                            Column(Modifier.verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                                Text("پخش متوقف شد", style = MaterialTheme.typography.titleLarge)
                                Text(problem.message, style = MaterialTheme.typography.bodyMedium)
                                Text(problem.code, color = MaterialTheme.colorScheme.primary, style = MaterialTheme.typography.labelSmall)
                                if (formatInfo.isNotBlank()) Text(formatInfo, style = MaterialTheme.typography.bodySmall)
                                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                                    Button(onClick = ::retry, enabled = channel != null) { Text("تلاش دوباره") }
                                    OutlinedButton(onClick = ::copyDiagnostics) { Text(if (copied) "کپی شد" else "کپی گزارش بی‌نام") }
                                }
                                Text("گزارش کپی‌شده شامل نسخه، مدل دستگاه و کد خطاست؛ آدرس، توکن و هدرهای استریم در آن نیست.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            }
                        }
                    }
                }
            }
        }
        lifecycleScope.launch {
            try {
                app.settings.state.first { it.loaded }
                channel = intent.getStringExtra(CHANNEL_ID)?.let { app.database.channels().channel(it)?.channel() }
                if (channel == null) {
                    buffering = false
                    failure = PlaybackFailure("اطلاعات این کانال در حافظهٔ برنامه پیدا نشد. به فهرست برگرد و دوباره انتخاب کن.", "CHANNEL_NOT_FOUND")
                } else if (started) startPlayer()
            } catch (cancelled: CancellationException) { throw cancelled
            } catch (_: Exception) {
                buffering = false
                failure = PlaybackFailure("اطلاعات کانال قابل خواندن نیست. به فهرست برگرد و دوباره انتخاب کن.", "CHANNEL_LOAD_FAILED")
            }
        }
    }

    override fun onStart() {
        super.onStart()
        started = true
        if (channel != null && player == null) startPlayer()
    }

    private fun startPlayer() {
        try { createPlayer() } catch (_: Exception) {
            releasePlayer()
            buffering = false
            failure = PlaybackFailure("موتور پخش آماده نشد. به فهرست برگرد و دوباره امتحان کن.", "PLAYER_INIT_FAILED")
        }
    }

    private fun createPlayer() {
        val item = channel ?: return
        if (player != null || !started) return
        failure = null
        copied = false
        buffering = true
        // Capture the application-owned settings, not this Activity's app getter, in
        // the client retained briefly by application-lifetime background cleanup.
        val playbackSettings = app.settings
        val client = NetworkClients.create(
            allowHttp = { playbackSettings.state.value.allowHttp },
            origin = item.url.toHttpUrl(), headers = item.headers, playback = true,
        )
        playbackClient = client
        // This factory cannot open file/content/data URLs from an untrusted manifest.
        val dataSource = OkHttpDataSource.Factory(client).setUserAgent("IPTVHamrah/0.1 (Android)")
        val sourceFactory = DefaultMediaSourceFactory(dataSource)
            .setLoadErrorHandlingPolicy(DefaultLoadErrorHandlingPolicy(2))
        val renderers = DefaultRenderersFactory(this).setEnableDecoderFallback(true)
        val engine = ExoPlayer.Builder(this, renderers).setMediaSourceFactory(sourceFactory).build()
        player = engine
        engine.setAudioAttributes(AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MOVIE).build(), true)
        engine.setHandleAudioBecomingNoisy(true)
        engine.addListener(object : Player.Listener {
            override fun onPlaybackStateChanged(state: Int) {
                buffering = state == Player.STATE_BUFFERING || state == Player.STATE_IDLE && failure == null
                if (state == Player.STATE_ENDED) {
                    failure = PlaybackFailure("پخش این رسانه به پایان رسید. می‌توانی دوباره تلاش کنی یا به فهرست برگردی.", "PLAYBACK_ENDED")
                }
            }
            override fun onPlayerError(error: PlaybackException) {
                failure = describePlaybackError(error)
                buffering = false
                window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            }
            override fun onIsPlayingChanged(isPlaying: Boolean) {
                if (isPlaying) {
                    window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
                    if (!historyRecorded) {
                        historyRecorded = true
                        lifecycleScope.launch { app.database.channels().recordPlayback(item.id) }
                    }
                } else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            }
            override fun onTracksChanged(tracks: Tracks) {
                val selected = tracks.groups.flatMap { group ->
                    (0 until group.length).filter { group.isTrackSelected(it) }.map { group.getTrackFormat(it) }
                }
                formatInfo = selected.mapNotNull { it.sampleMimeType }.distinct().joinToString(" · ")
            }
        })
        val media = MediaItem.Builder().setUri(item.url).setMediaId(item.id)
            .setMediaMetadata(MediaMetadata.Builder().setTitle(item.name).build())
        val path = URI(item.url).path.orEmpty().lowercase()
        when {
            path.endsWith(".m3u8") -> media.setMimeType(MimeTypes.APPLICATION_M3U8)
            path.endsWith(".mpd") -> media.setMimeType(MimeTypes.APPLICATION_MPD)
        }
        engine.setMediaItem(media.build())
        if (resumePosition != C.TIME_UNSET) engine.seekTo(resumePosition)
        engine.prepare()
        engine.playWhenReady = wasPlaying
    }

    private fun retry() {
        releasePlayer()
        resumePosition = C.TIME_UNSET
        wasPlaying = true
        startPlayer()
    }

    private fun releasePlayer() {
        val engine = player
        if (engine != null) {
            resumePosition = if (engine.isCurrentMediaItemLive) C.TIME_UNSET else engine.currentPosition
            wasPlaying = engine.playWhenReady
        }
        // Detach ownership on main before asynchronous cleanup; a retry/resume can now
        // create a new client without the outgoing cleanup ever touching its resources.
        val outgoingClient = playbackClient
        playbackClient = null
        player = null
        videoView?.player = null
        try {
            // Media3/PlayerView must still be accessed/released on their main looper.
            engine?.release()
        } finally {
            // Conscrypt can write TLS close_notify in cancelAll/evictAll. Never do it
            // on main, or in lifecycleScope (which is cancelled during onDestroy).
            outgoingClient?.let { app.playbackClientCleanup.release(it) }
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
    }

    private fun rotateScreen() {
        requestedOrientation = if (resources.configuration.orientation == Configuration.ORIENTATION_LANDSCAPE)
            ActivityInfo.SCREEN_ORIENTATION_USER_PORTRAIT else ActivityInfo.SCREEN_ORIENTATION_USER_LANDSCAPE
    }

    private fun copyDiagnostics() {
        val text = "IPTV Hamrah ${BuildConfig.VERSION_NAME}\nAndroid API ${Build.VERSION.SDK_INT}\nDevice ${Build.MANUFACTURER} ${Build.MODEL}\nMedia3 ${MediaLibraryInfo.VERSION}\nCode ${failure?.code ?: "NONE"}\nFormats $formatInfo"
        getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("IPTV diagnostics", text))
        copied = true
    }

    override fun onStop() {
        started = false
        releasePlayer()
        super.onStop()
    }

    override fun onDestroy() {
        releasePlayer()
        videoView = null
        super.onDestroy()
    }
}
