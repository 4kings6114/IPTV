package io.github.fourkings6114.iptv.data

import android.content.ContentResolver
import android.net.Uri
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.URI
import java.net.URLDecoder
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction
import java.nio.charset.StandardCharsets
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerializationException
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

@Serializable
data class BackupPreferences(
    val sourceId: String,
    val showLogos: Boolean,
    val themeStyle: String,
    val bufferProfile: String,
    val playerOrientation: String,
    val hideSourceSelector: Boolean,
    val sleepTimerMinutes: Int,
    val rewindSeconds: Int,
    val fullScreenResizeMode: String,
    val windowedResizeMode: String,
    val controllerTimeoutMs: Int,
    val showPlayerClock: Boolean,
    val audioDecoderFallback: Boolean,
    val textScale: Float,
    val screenScale: Float,
    val epgUrls: Map<String, String>,
)

@Serializable
data class BackupSource(
    val id: String,
    val title: String,
    val url: String,
    val updatedAt: Long,
    val count: Int,
    val skipped: Int,
)

@Serializable
data class BackupChannel(
    val id: String,
    val name: String,
    val group: String,
    val url: String,
    val logo: String,
    val tvgId: String,
    val headers: Map<String, String>,
)

@Serializable
data class BackupSourceChannel(val sourceId: String, val channelId: String, val position: Int)

@Serializable
data class BackupSavedChannel(val channelId: String, val savedAt: Long)

@Serializable
data class IptvBackup(
    val formatVersion: Int,
    val createdAtMillis: Long,
    val sources: List<BackupSource>,
    val channels: List<BackupChannel>,
    val sourceChannels: List<BackupSourceChannel>,
    val favorites: List<BackupSavedChannel>,
    val history: List<BackupSavedChannel>,
    val preferences: BackupPreferences,
    val omittedSensitiveSources: Int = 0,
    val omittedSensitiveChannels: Int = 0,
)

data class BackupPreview(val backup: IptvBackup) {
    val sourceCount: Int get() = backup.sources.size
    val channelCount: Int get() = backup.channels.size
    val favoriteCount: Int get() = backup.favorites.size
    val historyCount: Int get() = backup.history.size
    val omittedSensitiveSources: Int get() = backup.omittedSensitiveSources
    val omittedSensitiveChannels: Int get() = backup.omittedSensitiveChannels
}

data class BackupWriteResult(val bytes: Int, val preview: BackupPreview)

class InvalidBackupException(message: String, cause: Throwable? = null) : IOException(message, cause)

/** A versioned, local-only JSON backup. Credential-bearing catalogs are deliberately omitted. */
object BackupCodec {
    const val FORMAT_VERSION = 1
    const val MAX_BYTES = 32 * 1024 * 1024
    private const val MAX_SOURCES = 500
    private const val MAX_CHANNELS = 50_000
    private const val MAX_SOURCE_CHANNELS = 150_000
    private const val MAX_HEADERS = 64
    private const val MAX_TEXT_LENGTH = 16_384

    private val json = Json {
        encodeDefaults = true
        ignoreUnknownKeys = false
        isLenient = false
        explicitNulls = false
    }
    private val secretParameter = Regex("token|access.?key|api.?key|(^|[_-])key($|[_-])|secret|password|passwd|authorization|credential|session|signature|(^|[_-])sig($|[_-])|(^|[_-])auth($|[_-])", RegexOption.IGNORE_CASE)
    private val secretHeader = Regex("authorization|cookie|token|api.?key|secret|password|credential|(^|[_-])auth($|[_-])", RegexOption.IGNORE_CASE)

    fun create(snapshot: ChannelDatabaseSnapshot, settings: BackupPreferences, now: Long = System.currentTimeMillis()): IptvBackup {
        val safeChannels = snapshot.channels.filter(::isSafeChannel).associateBy(ChannelEntity::id)
        val linksBySource = snapshot.sourceChannels.groupBy(SourceChannelEntity::sourceId)
        val safeSources = snapshot.sources.filter { source ->
            !containsSecret(source.url) && linksBySource[source.id].orEmpty().all { it.channelId in safeChannels }
        }
        if (safeSources.isEmpty()) throw InvalidBackupException("No exportable playlist data is available.")

        val safeSourceIds = safeSources.mapTo(mutableSetOf(), SourceEntity::id)
        val safeReferences = snapshot.sourceChannels.filter { it.sourceId in safeSourceIds && it.channelId in safeChannels }
        val retainedChannelIds = safeReferences.mapTo(mutableSetOf(), SourceChannelEntity::channelId)
        (snapshot.favorites.map(FavoriteEntity::channelId) + snapshot.history.map(HistoryEntity::channelId))
            .filterTo(retainedChannelIds) { it in safeChannels }
        val channels = retainedChannelIds.mapNotNull(safeChannels::get)

        val selectedSourceId = settings.sourceId.takeIf { it in safeSourceIds } ?: safeSources.first().id
        val safeEpgUrls = settings.epgUrls.filter { (sourceId, url) ->
            sourceId in safeSourceIds && url.isNotBlank() && !containsSecret(url)
        }
        val safeSettings = settings.copy(sourceId = selectedSourceId, epgUrls = safeEpgUrls)
        val backup = IptvBackup(
            formatVersion = FORMAT_VERSION,
            createdAtMillis = now,
            sources = safeSources.map { BackupSource(it.id, it.title, it.url, it.updatedAt, it.count, it.skipped) },
            channels = channels.map(ChannelEntity::toBackup),
            sourceChannels = safeReferences.map { BackupSourceChannel(it.sourceId, it.channelId, it.position) },
            favorites = snapshot.favorites.filter { it.channelId in retainedChannelIds }
                .map { BackupSavedChannel(it.channelId, it.savedAt) },
            history = snapshot.history.filter { it.channelId in retainedChannelIds }
                .map { BackupSavedChannel(it.channelId, it.playedAt) },
            preferences = safeSettings,
            omittedSensitiveSources = snapshot.sources.size - safeSources.size,
            omittedSensitiveChannels = snapshot.channels.size - safeChannels.size,
        )
        validate(backup)
        return backup
    }

    fun encode(backup: IptvBackup): ByteArray {
        validate(backup)
        val bytes = json.encodeToString(backup).toByteArray(StandardCharsets.UTF_8)
        if (bytes.size > MAX_BYTES) throw InvalidBackupException("Backup exceeds the supported size limit.")
        return bytes
    }

    fun decode(bytes: ByteArray): IptvBackup {
        if (bytes.isEmpty() || bytes.size > MAX_BYTES) throw InvalidBackupException("Backup file is empty or too large.")
        val text = try {
            StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes)).toString()
        } catch (failure: Exception) {
            throw InvalidBackupException("Backup is not valid UTF-8.", failure)
        }
        val backup = try {
            json.decodeFromString<IptvBackup>(text)
        } catch (failure: SerializationException) {
            throw InvalidBackupException("Backup format is invalid or unsupported.", failure)
        } catch (failure: IllegalArgumentException) {
            throw InvalidBackupException("Backup format is invalid or unsupported.", failure)
        }
        validate(backup)
        return backup
    }

    fun validate(backup: IptvBackup) {
        if (backup.formatVersion != FORMAT_VERSION) throw InvalidBackupException("Backup version is not supported.")
        if (backup.createdAtMillis < 0 || backup.omittedSensitiveSources !in 0..MAX_SOURCES ||
            backup.omittedSensitiveChannels !in 0..MAX_CHANNELS)
            throw InvalidBackupException("Backup metadata is invalid.")
        if (backup.sources.isEmpty() || backup.channels.isEmpty() || backup.sourceChannels.isEmpty() ||
            backup.sources.size > MAX_SOURCES || backup.channels.size > MAX_CHANNELS ||
            backup.sourceChannels.size > MAX_SOURCE_CHANNELS || backup.favorites.size > MAX_CHANNELS || backup.history.size > 40)
            throw InvalidBackupException("Backup contains too many or too few records.")

        val sources = backup.sources.associateBy(BackupSource::id)
        if (sources.size != backup.sources.size || backup.sources.any { source ->
                source.id.isBlank() || source.id.length > 200 || hasControl(source.id) ||
                    source.title.isBlank() || source.title.length > SOURCE_TITLE_MAX_LENGTH || hasControl(source.title) ||
                    source.url.length > MAX_TEXT_LENGTH || source.url.isNotBlank() && (!isHttpUrl(source.url) || containsSecret(source.url)) ||
                    source.updatedAt < 0 || source.count < 0 || source.skipped < 0
            }) throw InvalidBackupException("Backup has invalid playlist records.")

        val channels = backup.channels.associateBy(BackupChannel::id)
        if (channels.size != backup.channels.size || backup.channels.any { channel ->
                channel.id.isBlank() || channel.id.length > MAX_TEXT_LENGTH || hasControl(channel.id) ||
                    channel.name.isBlank() || channel.name.length > MAX_TEXT_LENGTH ||
                    channel.group.length > MAX_TEXT_LENGTH || channel.url.length > MAX_TEXT_LENGTH || !isHttpUrl(channel.url) || containsSecret(channel.url) ||
                    channel.logo.length > MAX_TEXT_LENGTH || channel.logo.isNotBlank() && (!isHttpUrl(channel.logo) || containsSecret(channel.logo)) || channel.tvgId.length > MAX_TEXT_LENGTH ||
                    channel.headers.size > MAX_HEADERS || channel.headers.any { (key, value) ->
                        key.isBlank() || key.length > 512 || value.length > MAX_TEXT_LENGTH || hasControl(key) || hasControl(value) ||
                            secretHeader.containsMatchIn(key) || containsSecret(value)
                    }
            }) throw InvalidBackupException("Backup has invalid channel records.")

        val referencesBySource = backup.sourceChannels.groupBy(BackupSourceChannel::sourceId)
        val referenceKeys = mutableSetOf<Pair<String, String>>()
        val positions = mutableSetOf<Pair<String, Int>>()
        if (backup.sourceChannels.any { ref ->
                ref.sourceId !in sources || ref.channelId !in channels || ref.position < 0 ||
                    !referenceKeys.add(ref.sourceId to ref.channelId) || !positions.add(ref.sourceId to ref.position)
            }) throw InvalidBackupException("Backup contains invalid playlist references.")
        if (backup.sources.any { source -> referencesBySource[source.id].orEmpty().size != source.count })
            throw InvalidBackupException("Playlist counts do not match the backup contents.")
        if (backup.favorites.size != backup.favorites.map(BackupSavedChannel::channelId).toSet().size ||
            backup.history.size != backup.history.map(BackupSavedChannel::channelId).toSet().size ||
            (backup.favorites + backup.history).any { it.channelId !in channels || it.savedAt < 0 })
            throw InvalidBackupException("Backup contains invalid favorites or history.")

        val settings = backup.preferences
        if (settings.sourceId !in sources || settings.epgUrls.keys.any { it !in sources } ||
            settings.epgUrls.size > MAX_SOURCES || settings.epgUrls.values.any {
                it.length > MAX_TEXT_LENGTH || it.isNotBlank() && (!isHttpUrl(it) || containsSecret(it))
            } ||
            settings.themeStyle !in setOf("graphite", "navy", "plum") ||
            settings.bufferProfile !in setOf("auto", "tiny", "small", "normal", "medium", "large", "very_large") ||
            settings.playerOrientation !in setOf("auto", "landscape", "portrait") ||
            settings.sleepTimerMinutes !in setOf(0, 15, 30, 45, 60, 90, 120) ||
            settings.rewindSeconds !in setOf(5, 10, 15, 30, 60) ||
            settings.fullScreenResizeMode !in setOf("fit", "fill", "zoom") ||
            settings.windowedResizeMode !in setOf("fit", "fill", "zoom") ||
            settings.controllerTimeoutMs !in setOf(0, 2_000, 5_000, 10_000, 20_000) ||
            settings.textScale !in setOf(0.85f, 0.9f, 1.0f, 1.1f, 1.2f, 1.3f) ||
            settings.screenScale !in setOf(0.85f, 0.9f, 1.0f, 1.1f, 1.2f, 1.3f))
            throw InvalidBackupException("Backup contains invalid settings.")
    }

    fun toDatabaseSnapshot(backup: IptvBackup): ChannelDatabaseSnapshot {
        validate(backup)
        return ChannelDatabaseSnapshot(
            sources = backup.sources.map { SourceEntity(it.id, it.title, it.url, it.updatedAt, it.count, it.skipped) },
            channels = backup.channels.map(BackupChannel::toEntity),
            sourceChannels = backup.sourceChannels.map { SourceChannelEntity(it.sourceId, it.channelId, it.position) },
            favorites = backup.favorites.map { FavoriteEntity(it.channelId, it.savedAt) },
            history = backup.history.map { HistoryEntity(it.channelId, it.savedAt) },
        )
    }

    fun hasSensitiveData(url: String): Boolean = containsSecret(url)

    private fun ChannelEntity.toBackup(): BackupChannel {
        val parsed = runCatching { Json.parseToJsonElement(headersJson) as? JsonObject }.getOrNull()
            ?: throw InvalidBackupException("A channel contains invalid headers.")
        val headers = parsed.mapValues { (_, value) ->
            (value as? JsonPrimitive)?.takeIf { it.isString }?.contentOrNull
                ?: throw InvalidBackupException("A channel contains invalid headers.")
        }
        return BackupChannel(id, name, group, url, logo, tvgId, headers)
    }

    private fun BackupChannel.toEntity(): ChannelEntity = ChannelEntity(
        id = id,
        name = name,
        group = group,
        url = url,
        logo = logo,
        tvgId = tvgId,
        headersJson = JsonObject(headers.mapValues { JsonPrimitive(it.value) }).toString(),
    )

    private fun isSafeChannel(channel: ChannelEntity): Boolean {
        if (containsSecret(channel.url) || containsSecret(channel.logo)) return false
        val headers = runCatching { Json.parseToJsonElement(channel.headersJson) as? JsonObject }.getOrNull() ?: return false
        if (headers.size > MAX_HEADERS) return false
        return headers.all { (key, value) ->
            val text = (value as? JsonPrimitive)?.takeIf { it.isString }?.contentOrNull
            key.isNotBlank() && key.length <= 512 && !hasControl(key) && !secretHeader.containsMatchIn(key) &&
                text != null && text.length <= MAX_TEXT_LENGTH && !hasControl(text) && !containsSecret(text)
        }
    }

    private fun containsSecret(value: String): Boolean {
        if (value.isBlank()) return false
        val uri = runCatching { URI(value) }.getOrNull() ?: return false
        if (!uri.rawUserInfo.isNullOrBlank()) return true
        if (!uri.rawFragment.isNullOrBlank() && secretParameter.containsMatchIn(uri.rawFragment)) return true
        return uri.rawQuery.orEmpty().split('&').any { item ->
            val rawName = item.substringBefore('=')
            val name = runCatching { URLDecoder.decode(rawName, "UTF-8") }.getOrDefault(rawName)
            secretParameter.containsMatchIn(name)
        }
    }

    private fun isHttpUrl(value: String): Boolean = runCatching {
        val uri = URI(value)
        (uri.scheme.equals("http", true) || uri.scheme.equals("https", true)) &&
            !uri.host.isNullOrBlank() && uri.port in -1..65_535
    }.getOrDefault(false)

    private fun hasControl(value: String): Boolean = value.any { it.isISOControl() }
}

class AppBackupManager(
    private val resolver: ContentResolver,
    private val dao: ChannelDao,
    private val settings: SettingsStore,
    private val invalidateEpgAliases: suspend (Set<String>) -> Unit,
) {
    suspend fun create(uri: Uri): BackupWriteResult = withContext(Dispatchers.IO) {
        val snapshot = dao.backupSnapshot()
        val preferences = settings.backupPreferences()
        val backup = BackupCodec.create(snapshot, preferences)
        val bytes = BackupCodec.encode(backup)
        resolver.openOutputStream(uri, "wt")?.use { output ->
            output.write(bytes)
            output.flush()
        } ?: throw IOException("Unable to open the selected destination.")
        BackupWriteResult(bytes.size, BackupPreview(backup))
    }

    suspend fun preview(uri: Uri): BackupPreview = withContext(Dispatchers.IO) {
        val bytes = resolver.openInputStream(uri)?.use { it.readBounded(BackupCodec.MAX_BYTES) }
            ?: throw IOException("Unable to open the selected backup.")
        BackupPreview(BackupCodec.decode(bytes))
    }

    suspend fun restore(backup: IptvBackup) = withContext(Dispatchers.IO) {
        BackupCodec.validate(backup)
        val oldSnapshot = dao.backupSnapshot()
        val restoredSnapshot = BackupCodec.toDatabaseSnapshot(backup)
        val affectedSourceIds = (oldSnapshot.sources.map(SourceEntity::id) + backup.sources.map(BackupSource::id)).toSet()
        try {
            dao.restoreSnapshot(restoredSnapshot)
            invalidateEpgAliases(affectedSourceIds)
            settings.restoreFromBackup(backup.preferences)
        } catch (failure: Throwable) {
            withContext(NonCancellable + Dispatchers.IO) {
                dao.restoreSnapshot(oldSnapshot)
            }
            throw failure
        }
    }

    private fun InputStream.readBounded(limit: Int): ByteArray {
        val output = ByteArrayOutputStream(minOf(limit, 64 * 1024))
        val buffer = ByteArray(8 * 1024)
        var total = 0
        while (true) {
            val count = read(buffer)
            if (count < 0) break
            total += count
            if (total > limit) throw InvalidBackupException("Backup file is too large.")
            output.write(buffer, 0, count)
        }
        return output.toByteArray()
    }
}
