package io.github.fourkings6114.iptv.ui

import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalLayoutDirection
import androidx.compose.ui.unit.LayoutDirection

/** Palette selections shown in Settings. IDs are persisted in private DataStore preferences. */
enum class AppThemeStyle(val id: String, val label: String) {
    Graphite("graphite", "خاکستری تیره"),
    Navy("navy", "سرمه‌ای"),
    Plum("plum", "بنفش تیره");

    companion object {
        fun fromId(id: String): AppThemeStyle = entries.firstOrNull { it.id == id } ?: Graphite
    }
}

private val playerPalette = darkColorScheme(
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

private val graphitePalette = darkColorScheme(
    primary = Color(0xFFD6D0E5),
    onPrimary = Color(0xFF29282E),
    primaryContainer = Color(0xFF49474F),
    onPrimaryContainer = Color(0xFFF3F0F8),
    secondary = Color(0xFFC4C1CB),
    background = Color(0xFF292A30),
    onBackground = Color(0xFFF4F2F5),
    surface = Color(0xFF34353B),
    onSurface = Color(0xFFF4F2F5),
    surfaceVariant = Color(0xFF3D3E45),
    onSurfaceVariant = Color(0xFFC7C5CC),
    outline = Color(0xFF53545B),
    error = Color(0xFFFFB4AB),
    errorContainer = Color(0xFF5A2729),
)

private val navyPalette = playerPalette

private val plumPalette = darkColorScheme(
    primary = Color(0xFFE4C9F2),
    onPrimary = Color(0xFF33213E),
    primaryContainer = Color(0xFF574367),
    onPrimaryContainer = Color(0xFFF8ECFF),
    secondary = Color(0xFFD0C2D8),
    background = Color(0xFF211E25),
    onBackground = Color(0xFFF6EFF8),
    surface = Color(0xFF302B35),
    onSurface = Color(0xFFF6EFF8),
    surfaceVariant = Color(0xFF403946),
    onSurfaceVariant = Color(0xFFD0C6D5),
    outline = Color(0xFF5A5160),
    error = Color(0xFFFFB4AB),
    errorContainer = Color(0xFF5A2729),
)

private fun AppThemeStyle.colorScheme(): ColorScheme = when (this) {
    AppThemeStyle.Graphite -> graphitePalette
    AppThemeStyle.Navy -> navyPalette
    AppThemeStyle.Plum -> plumPalette
}

/** Full-screen video remains the original dark player theme. */
@Composable
fun IPTVTheme(content: @Composable () -> Unit) = provideTheme(playerPalette, content)

/** Browse/settings screens use the selected dark palette and keep Persian RTL layout. */
@Composable
fun IPTVMainTheme(style: AppThemeStyle, content: @Composable () -> Unit) = provideTheme(style.colorScheme(), content)

@Composable
private fun provideTheme(colors: ColorScheme, content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalLayoutDirection provides LayoutDirection.Rtl) {
        MaterialTheme(colorScheme = colors, typography = Typography(), content = content)
    }
}
