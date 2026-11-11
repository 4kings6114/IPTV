package io.github.fourkings6114.iptv.player

import kotlin.math.roundToInt

/** No Android/Activity ownership: independently test the entry and ratio policy. */
internal data class PipEligibility(
    val supported: Boolean,
    val started: Boolean,
    val videoReady: Boolean,
    val failed: Boolean,
    val finishing: Boolean,
    val playWhenReady: Boolean,
) {
    // A paused, already-rendered video may be explicitly minimized by its user.
    val canEnter: Boolean get() = supported && started && videoReady && !failed && !finishing
    fun canAutoEnter(enabled: Boolean): Boolean = canEnter && enabled && playWhenReady
}

internal data class PipAspectRatio(val width: Int, val height: Int)

/** Platform PiP accepts only [1 / 2.39, 2.39]. Never pass zero/NaN/extreme media values. */
internal fun pipAspectRatio(width: Int, height: Int, pixelRatio: Float = 1f): PipAspectRatio {
    if (width <= 0 || height <= 0 || !pixelRatio.isFinite() || pixelRatio <= 0f) return PipAspectRatio(16, 9)
    val ratio = width.toDouble() * pixelRatio / height
    // Round *inside* the inclusive platform bounds, rather than rounding below its lower limit.
    val numerator = (ratio.coerceIn(1.0 / 2.39, 2.39) * 10_000).roundToInt().coerceIn(4185, 23_900)
    return PipAspectRatio(numerator, 10_000)
}
