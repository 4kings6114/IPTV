package io.github.fourkings6114.iptv.data

import org.junit.Assert.*
import org.junit.Test

class AppBackupManagerTest {
    private val safeChannel = ChannelEntity(
        id = "safe-channel", name = "Safe channel", group = "News",
        url = "https://example.invalid/live.m3u8", logo = "", tvgId = "safe",
        headersJson = "{\"user-agent\":\"IPTV test\"}",
    )
    private val tokenChannel = ChannelEntity(
        id = "token-channel", name = "Private channel", group = "Private",
        url = "https://example.invalid/live.m3u8?access_token=do-not-export", logo = "", tvgId = "private",
        headersJson = "{}",
    )
    private val authorizationChannel = ChannelEntity(
        id = "authorization-channel", name = "Header private", group = "Private",
        url = "https://example.invalid/stream.m3u8", logo = "", tvgId = "private-header",
        headersJson = "{\"Authorization\":\"Bearer do-not-export\"}",
    )
    private val preferences = BackupPreferences(
        sourceId = "secret-source", showLogos = false, themeStyle = "navy", bufferProfile = "normal",
        playerOrientation = "auto", hideSourceSelector = false, sleepTimerMinutes = 0, rewindSeconds = 10,
        fullScreenResizeMode = "fit", windowedResizeMode = "fit", controllerTimeoutMs = 5_000,
        showPlayerClock = false, audioDecoderFallback = true, textScale = 1.0f, screenScale = 1.0f,
        epgUrls = mapOf(
            "safe-source" to "https://example.invalid/guide.xml",
            "secret-source" to "https://example.invalid/guide.xml?token=do-not-export",
        ),
    )

    @Test fun exportOmitsCredentialBearingSourcesAndRoundTripsSafeData() {
        val snapshot = ChannelDatabaseSnapshot(
            sources = listOf(
                SourceEntity("safe-source", "Safe list", "https://example.invalid/safe.m3u", 100, 1),
                SourceEntity("secret-source", "Token list", "https://example.invalid/list.m3u?api_key=secret", 101, 2),
                SourceEntity("header-source", "Header list", "https://example.invalid/header.m3u", 102, 1),
            ),
            channels = listOf(safeChannel, tokenChannel, authorizationChannel),
            sourceChannels = listOf(
                SourceChannelEntity("safe-source", safeChannel.id, 0),
                SourceChannelEntity("secret-source", tokenChannel.id, 0),
                SourceChannelEntity("secret-source", safeChannel.id, 1),
                SourceChannelEntity("header-source", authorizationChannel.id, 0),
            ),
            favorites = listOf(FavoriteEntity(safeChannel.id, 10), FavoriteEntity(tokenChannel.id, 11)),
            history = listOf(HistoryEntity(authorizationChannel.id, 12)),
        )

        val backup = BackupCodec.create(snapshot, preferences, now = 1234)
        val bytes = BackupCodec.encode(backup)
        val text = bytes.toString(Charsets.UTF_8)

        assertEquals(2, backup.omittedSensitiveSources)
        assertEquals(2, backup.omittedSensitiveChannels)
        assertEquals(listOf("safe-source"), backup.sources.map { it.id })
        assertEquals(listOf(safeChannel.id), backup.channels.map { it.id })
        assertEquals(listOf(safeChannel.id), backup.favorites.map { it.channelId })
        assertTrue(backup.history.isEmpty())
        assertEquals("safe-source", backup.preferences.sourceId)
        assertEquals(mapOf("safe-source" to "https://example.invalid/guide.xml"), backup.preferences.epgUrls)
        assertFalse(text.contains("do-not-export"))
        assertFalse(text.contains("Bearer"))
        assertFalse(text.contains("allowHttp"))
        assertFalse(text.contains("parentalPin"))

        val decoded = BackupCodec.decode(bytes)
        assertEquals(backup, decoded)
        assertEquals(listOf(safeChannel), BackupCodec.toDatabaseSnapshot(decoded).channels)
    }

    @Test fun rejectsUnsupportedSchemaAndBrokenReferences() {
        val valid = BackupCodec.create(
            ChannelDatabaseSnapshot(
                sources = listOf(SourceEntity("safe-source", "Safe list", updatedAt = 100, count = 1)),
                channels = listOf(safeChannel),
                sourceChannels = listOf(SourceChannelEntity("safe-source", safeChannel.id, 0)),
                favorites = emptyList(), history = emptyList(),
            ),
            preferences.copy(sourceId = "safe-source", epgUrls = emptyMap()),
            now = 1234,
        )

        assertThrows(InvalidBackupException::class.java) { BackupCodec.validate(valid.copy(formatVersion = 99)) }
        assertThrows(InvalidBackupException::class.java) {
            BackupCodec.validate(valid.copy(sourceChannels = listOf(BackupSourceChannel("safe-source", "missing", 0))))
        }
        assertThrows(InvalidBackupException::class.java) {
            BackupCodec.decode(ByteArray(0))
        }
    }

    @Test fun detectsCredentialsInUrlUserInfoQueryAndSensitiveHeaders() {
        assertTrue(BackupCodec.hasSensitiveData("https://user:pass@example.invalid/list.m3u"))
        assertTrue(BackupCodec.hasSensitiveData("https://example.invalid/list.m3u?signature=abc"))
        assertFalse(BackupCodec.hasSensitiveData("https://example.invalid/list.m3u?quality=hd"))
    }
}
