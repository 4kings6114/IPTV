package io.github.fourkings6114.iptv.data

import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ParentalPinTest {
    @Test fun pinIsSaltedAndOnlyMatchingPinVerifies() {
        val (salt, hash) = ParentalPin.create("4815")
        assertNotEquals("4815", hash)
        assertTrue(ParentalPin.verify("4815", salt, hash))
        assertFalse(ParentalPin.verify("5814", salt, hash))
        assertFalse(ParentalPin.verify("123", salt, hash))
    }

    @Test fun pinMustBeFourToEightDigits() {
        listOf("", "123", "123456789", "12a4").forEach { pin ->
            assertFalse(ParentalPin.verify(pin, "", ""))
        }
    }
}
