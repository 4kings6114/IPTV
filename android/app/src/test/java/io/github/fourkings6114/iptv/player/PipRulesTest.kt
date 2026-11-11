package io.github.fourkings6114.iptv.player

import org.junit.Assert.*
import org.junit.Test

class PipRulesTest {
    private val ready = PipEligibility(true, true, true, false, false, true)
    private fun ratio(width: Int, height: Int, pixels: Float = 1f): Double =
        pipAspectRatio(width, height, pixels).let { it.width.toDouble() / it.height }

    @Test fun manualEntryDoesNotRequireAutomaticHomeOptIn() {
        assertTrue(ready.canEnter)
        assertFalse(ready.canAutoEnter(false))
        assertTrue(ready.canAutoEnter(true))
    }
    @Test fun pausedVideoCanBeManuallyMinimizedButCannotAutoEnter() {
        val paused = ready.copy(playWhenReady = false)
        assertTrue(paused.canEnter)
        assertFalse(paused.canAutoEnter(true))
    }
    @Test fun unsupportedDeviceNeverEntersPip() {
        assertFalse(ready.copy(supported = false).canEnter)
        assertFalse(ready.copy(supported = false).canAutoEnter(true))
    }
    @Test fun stoppedPlaybackCannotBeResurrectedByPip() {
        assertFalse(ready.copy(started = false).canEnter)
        assertFalse(ready.copy(started = false).canAutoEnter(true))
    }
    @Test fun missingVideoOrAudioOnlyPlaybackNeverEntersPip() {
        assertFalse(ready.copy(videoReady = false).canEnter)
        assertFalse(ready.copy(videoReady = false).canAutoEnter(true))
    }
    @Test fun failedPlaybackDoesNotAutomaticallyLeaveAnErrorWindow() {
        assertFalse(ready.copy(failed = true).canEnter)
        assertFalse(ready.copy(failed = true).canAutoEnter(true))
    }
    @Test fun explicitBackOrFinishNeverAutoEntersPip() {
        assertFalse(ready.copy(finishing = true).canEnter)
        assertFalse(ready.copy(finishing = true).canAutoEnter(true))
    }
    @Test fun normalVideoRatioIsPreservedWithinRoundingTolerance() {
        assertEquals(16.0 / 9, ratio(1920, 1080), 0.0001)
        assertEquals(9.0 / 16, ratio(1080, 1920), 0.0001)
        assertEquals(1.0, ratio(100, 100), 0.0001)
    }
    @Test fun anamorphicPixelAspectRatioIsApplied() {
        assertEquals(16.0 / 9, ratio(720, 576, 64f / 45), 0.0001)
    }
    @Test fun ultraWideRatioIsClampedToThePlatformMaximum() {
        assertEquals(2.39, ratio(8000, 100), 0.00001)
        assertEquals(2.39, ratio(Int.MAX_VALUE, 1), 0.00001)
    }
    @Test fun extremelyTallRatioRoundsInsideThePlatformMinimum() {
        val value = ratio(1, Int.MAX_VALUE)
        assertTrue(value >= 1.0 / 2.39)
        assertTrue(value < 1.0 / 2.39 + 0.0001)
    }
    @Test fun invalidOrUnknownDimensionsUseASafeFallback() {
        for ((w, h) in listOf(0 to 0, 1 to 0, 0 to 1, -1 to 100, 100 to -1)) {
            assertEquals(PipAspectRatio(16, 9), pipAspectRatio(w, h))
        }
    }
    @Test fun invalidPixelRatiosCannotProduceAnInvalidPlatformRational() {
        for (pixels in listOf(0f, -1f, Float.NaN, Float.POSITIVE_INFINITY, Float.NEGATIVE_INFINITY)) {
            assertEquals(PipAspectRatio(16, 9), pipAspectRatio(100, 100, pixels))
        }
    }
    @Test fun extremeFinitePixelRatiosStayWithinBothPlatformBounds() {
        assertTrue(ratio(1, 1, Float.MIN_VALUE) >= 1.0 / 2.39)
        assertEquals(2.39, ratio(1, 1, Float.MAX_VALUE), 0.00001)
    }
}
