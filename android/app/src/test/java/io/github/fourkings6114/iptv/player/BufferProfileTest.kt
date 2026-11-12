package io.github.fourkings6114.iptv.player

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class BufferProfileTest {
    @Test fun automaticDelegatesSizingAndManualProfilesHaveIncreasingByteTargets() {
        assertNull(BufferProfile.Automatic.targetBytes)
        val manual = BufferProfile.entries.drop(1)
        assertEquals(listOf(102_400, 204_800, 524_288, 1_048_576, 2_097_152, 4_194_304), manual.map { it.targetBytes })
        assertEquals(manual.mapNotNull { it.targetBytes }.sorted(), manual.mapNotNull { it.targetBytes })
    }

    @Test fun unknownSavedValueFallsBackToNormal() {
        assertEquals(BufferProfile.Normal, BufferProfile.fromId("untrusted-value"))
        assertEquals(BufferProfile.Medium, BufferProfile.fromId("medium"))
    }
}
