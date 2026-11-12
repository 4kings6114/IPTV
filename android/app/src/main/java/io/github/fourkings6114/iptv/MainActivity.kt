package io.github.fourkings6114.iptv

import android.content.Intent
import android.content.ActivityNotFoundException
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.SystemBarStyle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.runtime.getValue
import io.github.fourkings6114.iptv.player.PlaybackActivity
import io.github.fourkings6114.iptv.ui.AppThemeStyle
import io.github.fourkings6114.iptv.ui.IPTVMainTheme
import io.github.fourkings6114.iptv.ui.MainScreen
import io.github.fourkings6114.iptv.ui.MainViewModel

class MainActivity : ComponentActivity() {
    private val model by viewModels<MainViewModel>()
    private val chooseFile = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        model.fileChosen(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
        )
        setContent {
            val settings by model.settings.collectAsStateWithLifecycle()
            IPTVMainTheme(
                AppThemeStyle.fromId(settings.themeStyle),
                textScale = settings.textScale,
                screenScale = settings.screenScale,
            ) {
                MainScreen(
                    model = model,
                    onImportFile = { request ->
                        if (model.prepareFileImport(request.title, request.sourceId)) {
                            try { chooseFile.launch(arrayOf("*/*")) }
                            catch (_: ActivityNotFoundException) { model.filePickerUnavailable() }
                            catch (_: SecurityException) { model.filePickerUnavailable() }
                        }
                    },
                    onPlay = { channel ->
                        startActivity(Intent(this, PlaybackActivity::class.java)
                            .putExtra(PlaybackActivity.CHANNEL_ID, channel.id))
                    },
                )
            }
        }
    }
}
