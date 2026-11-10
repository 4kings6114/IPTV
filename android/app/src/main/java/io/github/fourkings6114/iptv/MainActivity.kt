package io.github.fourkings6114.iptv

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.SystemBarStyle
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import io.github.fourkings6114.iptv.player.PlaybackActivity
import io.github.fourkings6114.iptv.ui.IPTVTheme
import io.github.fourkings6114.iptv.ui.MainScreen
import io.github.fourkings6114.iptv.ui.MainViewModel

class MainActivity : ComponentActivity() {
    private val model by viewModels<MainViewModel>()
    private val chooseFile = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri?.let(model::importFile)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.dark(android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.dark(android.graphics.Color.BLACK),
        )
        setContent {
            IPTVTheme {
                MainScreen(
                    model = model,
                    onImportFile = { chooseFile.launch(arrayOf("*/*")) },
                    onPlay = { channel ->
                        startActivity(Intent(this, PlaybackActivity::class.java)
                            .putExtra(PlaybackActivity.CHANNEL_ID, channel.id))
                    },
                )
            }
        }
    }
}
