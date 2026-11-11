package io.github.fourkings6114.iptv.data

import org.junit.Assert.*
import org.junit.Test

class SourceRulesTest {
    @Test fun namesAreTrimmedWithoutBreakingPersianZwnj() {
        assertEquals("فهرست‌های من", sourceTitle("  فهرست‌های   من\n"))
    }
    @Test fun controlAndBidiOverrideCharactersAreRemoved() {
        assertEquals("My list", sourceTitle("\u202eMy\u202c list\u0000\u2066\u2069"))
    }
    @Test fun emptyAndOversizedNamesAreRejected() {
        assertThrows(InvalidSourceNameException::class.java) { sourceTitle(" \n\t\u202e\u202c") }
        assertThrows(InvalidSourceNameException::class.java) { sourceTitle("x".repeat(81)) }
        assertEquals(80, sourceTitle("x".repeat(80)).length)
    }
    @Test fun sourceKindSupportsLegacyIdsAndNeverMistakesTheSampleForAFile() {
        assertTrue(source("file").isFileSource)
        assertTrue(source("user-new").isFileSource)
        assertFalse(source("custom", "https://example.invalid/list").isFileSource)
        assertFalse(source("user-link", "https://example.invalid/list").isFileSource)
        SOURCE_PRESETS.forEach { assertFalse(source(it.id).isFileSource); assertTrue(isBuiltInSource(it.id)) }
        assertFalse(isBuiltInSource("custom"))
        assertFalse(isBuiltInSource("file"))
    }
    private fun source(id: String, url: String = "") = SourceEntity(id, "Name", url, 1, 1)
}
