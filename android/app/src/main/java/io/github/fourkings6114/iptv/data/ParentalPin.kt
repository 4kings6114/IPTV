package io.github.fourkings6114.iptv.data

import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

/** Salted, deliberately slow verifier for the optional local adult-content filter PIN. */
internal object ParentalPin {
    private const val ITERATIONS = 120_000
    private const val KEY_BITS = 256
    private const val SALT_BYTES = 16
    private val random = SecureRandom()

    fun create(pin: String): Pair<String, String> {
        require(pin.matches(Regex("[0-9]{4,8}")))
        val salt = ByteArray(SALT_BYTES).also(random::nextBytes)
        val encodedSalt = Base64.getEncoder().encodeToString(salt)
        return encodedSalt to derive(pin, salt)
    }

    fun verify(pin: String, encodedSalt: String, expectedHash: String): Boolean {
        if (!pin.matches(Regex("[0-9]{4,8}")) || encodedSalt.isBlank() || expectedHash.isBlank()) return false
        return runCatching {
            val salt = Base64.getDecoder().decode(encodedSalt)
            if (salt.size != SALT_BYTES) return false
            MessageDigest.isEqual(derive(pin, salt).toByteArray(), expectedHash.toByteArray())
        }.getOrDefault(false)
    }

    private fun derive(pin: String, salt: ByteArray): String {
        val spec = PBEKeySpec(pin.toCharArray(), salt, ITERATIONS, KEY_BITS)
        return try {
            Base64.getEncoder().encodeToString(SecretKeyFactory.getInstance("PBKDF2WithHmacSHA256").generateSecret(spec).encoded)
        } finally { spec.clearPassword() }
    }
}
