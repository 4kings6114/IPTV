package io.github.fourkings6114.iptv.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.LayoutDirection

private val palette = darkColorScheme(
    primary = Color(0xFF68E1C4),
    onPrimary = Color(0xFF00382D),
    primaryContainer = Color(0xFF163F38),
    onPrimaryContainer = Color(0xFFADFAE5),
    secondary = Color(0xFFB2C4DC),
    background = Color(0xFF0B111B),
    onBackground = Color(0xFFE8EEF6),
    surface = Color(0xFF121D2B),
    onSurface = Color(0xFFE8EEF6),
    surfaceVariant = Color(0xFF233144),
    onSurfaceVariant = Color(0xFFB5C2D4),
    outline = Color(0xFF586879),
    error = Color(0xFFFFB4AB),
    errorContainer = Color(0xFF4A2223),
)

@Composable
fun IPTVTheme(content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
        MaterialTheme(colorScheme = palette, typography = Typography(), content = content)
    }
}
