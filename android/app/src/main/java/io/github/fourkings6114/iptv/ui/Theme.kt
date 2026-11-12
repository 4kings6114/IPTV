package io.github.fourkings6114.iptv.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.LayoutDirection

private val darkPalette = darkColorScheme(
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

private val lightPalette = lightColorScheme(
    primary = Color(0xFF5754D8),
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFFE8E7FF),
    onPrimaryContainer = Color(0xFF211F54),
    secondary = Color(0xFF5F6378),
    onSecondary = Color(0xFFFFFFFF),
    background = Color(0xFFF5F4ED),
    onBackground = Color(0xFF25271D),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF25271D),
    surfaceVariant = Color(0xFFECEDE7),
    onSurfaceVariant = Color(0xFF606358),
    outline = Color(0xFFD6D8CE),
    error = Color(0xFFB3261E),
    errorContainer = Color(0xFFF9DEDC),
    onErrorContainer = Color(0xFF410E0B),
)

/** The browsing app uses the light TV-guide palette; the immersive player stays dark. */
@Composable
fun IPTVTheme(dark: Boolean = true, content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
        MaterialTheme(colorScheme = if (dark) darkPalette else lightPalette, typography = Typography(), content = content)
    }
}
