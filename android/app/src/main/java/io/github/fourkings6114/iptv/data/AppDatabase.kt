package io.github.fourkings6114.iptv.data

import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.RoomDatabase
import androidx.room.Transaction
import io.github.fourkings6114.iptv.core.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

@Entity(tableName = "channels")
data class ChannelEntity(
    @PrimaryKey val id: String,
    val name: String,
    val group: String,
    val url: String,
    val logo: String,
    val tvgId: String,
    val headersJson: String,
) {
    fun channel() = Channel(id, name, group, url, logo, tvgId, decodeHeaders(headersJson))
}

fun Channel.entity() = ChannelEntity(id, name, group, url, logo, tvgId,
    JsonObject(headers.mapValues { JsonPrimitive(it.value) }).toString())

private fun decodeHeaders(value: String): Map<String, String> = runCatching {
    (Json.parseToJsonElement(value) as JsonObject).mapNotNull { (key, item) ->
        (item as? JsonPrimitive)?.contentOrNull?.let { key to it }
    }.toMap()
}.getOrDefault(emptyMap())

@Entity(tableName = "source_channels", primaryKeys = ["sourceId", "channelId"])
data class SourceChannelEntity(val sourceId: String, val channelId: String, val position: Int)

@Entity(tableName = "sources")
data class SourceEntity(
    @PrimaryKey val id: String,
    val title: String,
    val url: String = "",
    val updatedAt: Long,
    val count: Int,
    val skipped: Int = 0,
)

@Entity(tableName = "favorites")
data class FavoriteEntity(@PrimaryKey val channelId: String, val savedAt: Long)

@Entity(tableName = "history")
data class HistoryEntity(@PrimaryKey val channelId: String, val playedAt: Long)

@Dao
abstract class ChannelDao {
    @Query("SELECT * FROM sources ORDER BY updatedAt DESC")
    abstract fun observeSources(): Flow<List<SourceEntity>>

    @Query("SELECT channels.* FROM channels INNER JOIN source_channels ON channels.id = source_channels.channelId WHERE source_channels.sourceId = :sourceId ORDER BY source_channels.position")
    abstract fun observeSource(sourceId: String): Flow<List<ChannelEntity>>

    @Query("SELECT channels.* FROM channels INNER JOIN favorites ON channels.id = favorites.channelId ORDER BY favorites.savedAt DESC")
    abstract fun observeFavorites(): Flow<List<ChannelEntity>>

    @Query("SELECT channelId FROM favorites")
    abstract fun observeFavoriteIds(): Flow<List<String>>

    @Query("SELECT channels.* FROM channels INNER JOIN history ON channels.id = history.channelId ORDER BY history.playedAt DESC LIMIT 40")
    abstract fun observeHistory(): Flow<List<ChannelEntity>>

    @Query("SELECT * FROM sources WHERE id = :sourceId LIMIT 1")
    abstract fun observeSourceInfo(sourceId: String): Flow<SourceEntity?>

    @Query("SELECT * FROM sources WHERE id = :sourceId LIMIT 1")
    abstract suspend fun source(sourceId: String): SourceEntity?

    @Query("SELECT * FROM channels WHERE id = :id LIMIT 1")
    abstract suspend fun channel(id: String): ChannelEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    abstract suspend fun saveChannels(channels: List<ChannelEntity>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    abstract suspend fun saveSource(source: SourceEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    abstract suspend fun saveReferences(references: List<SourceChannelEntity>)

    @Query("DELETE FROM source_channels WHERE sourceId = :sourceId")
    abstract suspend fun clearReferences(sourceId: String)

    @Query("DELETE FROM channels WHERE id NOT IN (SELECT channelId FROM source_channels) AND id NOT IN (SELECT channelId FROM favorites) AND id NOT IN (SELECT channelId FROM history)")
    abstract suspend fun pruneOrphans()

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    abstract suspend fun addFavorite(favorite: FavoriteEntity)

    @Query("DELETE FROM favorites WHERE channelId = :id")
    abstract suspend fun removeFavorite(id: String)

    @Query("SELECT EXISTS(SELECT 1 FROM favorites WHERE channelId = :id)")
    abstract suspend fun isFavorite(id: String): Boolean

    @Transaction
    open suspend fun toggleFavorite(id: String) {
        if (isFavorite(id)) removeFavorite(id) else addFavorite(FavoriteEntity(id, System.currentTimeMillis()))
    }

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    abstract suspend fun saveHistory(history: HistoryEntity)

    @Query("DELETE FROM history WHERE channelId NOT IN (SELECT channelId FROM history ORDER BY playedAt DESC LIMIT 40)")
    abstract suspend fun trimHistory()

    @Query("DELETE FROM history")
    abstract suspend fun clearHistory()

    @Transaction
    open suspend fun recordPlayback(id: String) {
        saveHistory(HistoryEntity(id, System.currentTimeMillis()))
        trimHistory()
    }

    /** A failed/empty fetch must never call this or destroy last-known-good metadata. */
    @Transaction
    open suspend fun replaceSource(source: SourceEntity, channels: List<Channel>) {
        require(channels.isNotEmpty())
        saveChannels(channels.map(Channel::entity))
        clearReferences(source.id)
        saveReferences(channels.mapIndexed { index, channel -> SourceChannelEntity(source.id, channel.id, index) })
        saveSource(source)
        pruneOrphans()
    }
}

@Database(
    entities = [ChannelEntity::class, SourceChannelEntity::class, SourceEntity::class, FavoriteEntity::class, HistoryEntity::class],
    version = 1,
    exportSchema = true,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun channels(): ChannelDao
}
