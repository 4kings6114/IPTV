package io.github.fourkings6114.iptv.player

import android.app.PendingIntent
import android.app.PictureInPictureParams
import android.app.RemoteAction
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.Rect
import android.graphics.drawable.Icon
import android.os.Build
import android.util.Rational
import androidx.activity.ComponentActivity
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import androidx.media3.common.Player
import androidx.media3.common.util.UnstableApi
import androidx.media3.ui.PlayerView

/** Activity-owned native PiP. No service, media-session URI sharing or overlay permission. */
@androidx.annotation.OptIn(UnstableApi::class)
internal class PlaybackPipController(
    private val activity: ComponentActivity,
    private val player: () -> Player?,
    private val playerView: () -> PlayerView?,
    private val eligibility: () -> PipEligibility,
    private val automaticEnabled: () -> Boolean,
    private val onDenied: () -> Unit,
) {
    val supported = activity.packageManager.hasSystemFeature(PackageManager.FEATURE_PICTURE_IN_PICTURE)
    var hideControls by mutableStateOf(false)
        private set
    var available by mutableStateOf(false)
        private set
    private var leaving = false
    private var disposed = false
    private val playAction = activity.packageName + ".PIP_PLAY"
    private val pauseAction = activity.packageName + ".PIP_PAUSE"

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            // A stale action cannot start hidden playback, resurrect an engine or retry a failed URL.
            if (!activity.isInPictureInPictureMode || !state().canEnter) return
            when (intent?.action) {
                playAction -> player()?.play()
                pauseAction -> player()?.pause()
                else -> return
            }
            update()
        }
    }

    init {
        ContextCompat.registerReceiver(
            activity, receiver, IntentFilter().apply { addAction(playAction); addAction(pauseAction) },
            ContextCompat.RECEIVER_NOT_EXPORTED,
        )
    }

    private fun state(): PipEligibility = eligibility().let { it.copy(finishing = it.finishing || leaving || disposed) }

    private fun params(): PictureInPictureParams {
        val state = state()
        val size = player()?.videoSize
        val ratio = pipAspectRatio(size?.width ?: 0, size?.height ?: 0, size?.pixelWidthHeightRatio ?: 1f)
        val builder = PictureInPictureParams.Builder().setAspectRatio(Rational(ratio.width, ratio.height))
        val bounds = Rect()
        val surface = playerView()?.videoSurfaceView ?: playerView()
        if (surface?.getGlobalVisibleRect(bounds) == true && !bounds.isEmpty) builder.setSourceRectHint(bounds)
        builder.setActions(if (state.canEnter) listOf(playPauseAction(state.playWhenReady)) else emptyList())
        if (Build.VERSION.SDK_INT >= 31) {
            builder.setAutoEnterEnabled(state.canAutoEnter(automaticEnabled()))
            builder.setSeamlessResizeEnabled(true)
        }
        return builder.build()
    }

    private fun playPauseAction(playing: Boolean): RemoteAction {
        val title = if (playing) "مکث" else "ادامهٔ پخش"
        return RemoteAction(
            Icon.createWithResource(activity, if (playing) android.R.drawable.ic_media_pause else android.R.drawable.ic_media_play),
            title, title,
            PendingIntent.getBroadcast(
                activity, if (playing) 410 else 411,
                Intent(if (playing) pauseAction else playAction).setPackage(activity.packageName),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            ),
        )
    }

    fun update() {
        if (disposed) return
        available = state().canEnter
        if (!supported) return
        try {
            activity.setPictureInPictureParams(params())
        } catch (_: IllegalStateException) {
            // Expected platform refusal during teardown; never retry from a background callback.
            available = false
        } catch (_: SecurityException) {
            available = false
        }
    }

    fun enter(): Boolean {
        if (!state().canEnter || activity.isInPictureInPictureMode) return false
        setHidden(true)
        val entered = try {
            activity.enterPictureInPictureMode(params())
        } catch (_: IllegalStateException) {
            false // An OEM/AppOps restriction is not a fatal playback error.
        } catch (_: SecurityException) {
            false
        }
        if (!entered) {
            setHidden(false)
            onDenied()
        }
        return entered
    }

    fun onUserLeaveHint() {
        update()
        if (!state().canAutoEnter(automaticEnabled())) return
        if (Build.VERSION.SDK_INT < 31) enter() else setHidden(true)
    }

    fun onModeChanged(inPictureInPicture: Boolean) {
        setHidden(inPictureInPicture)
        update()
    }

    fun onTransitionStarted() { setHidden(true) }

    fun onResume() {
        // Also restore controls after a cancelled Home gesture or denied auto-entry.
        if (!activity.isInPictureInPictureMode) setHidden(false)
        update()
    }

    private fun setHidden(hidden: Boolean) {
        hideControls = hidden
        playerView()?.apply {
            useController = !hidden
            if (hidden) hideController()
        }
    }

    fun finishPlayback() {
        leaving = true // Explicit Back is NOT an automatic Home-to-PiP request.
        update()
        activity.finish()
    }

    fun dispose() {
        if (disposed) return
        disposed = true
        available = false
        activity.unregisterReceiver(receiver)
    }
}
