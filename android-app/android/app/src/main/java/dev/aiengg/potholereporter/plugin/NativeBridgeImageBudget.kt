package dev.aiengg.potholereporter.plugin

import dev.aiengg.potholereporter.drive.NativeStoredImagePolicy

/**
 * Caps image-bearing Capacitor results before Base64 allocation.
 *
 * Capacitor serializes the native object to JSON and the WebView then materializes the
 * same Base64 again as JavaScript strings. A count-only page limit is therefore not
 * enough: a few unusually large JPEGs can temporarily require several copies of tens of
 * megabytes. This budget is charged before File.readBytes/Base64 encoding.
 */
internal class NativeBridgeImageBudget(
    private val maxEncodedChars: Long = MAX_BATCH_ENCODED_CHARS,
    private val maxSingleRawBytes: Long = MAX_SINGLE_RAW_BYTES,
    private val maxItems: Int = MAX_BATCH_ITEMS
) {
    internal data class ImageClaim(val encodedChars: Long)

    internal inner class Reservation internal constructor(
        private val encodedChars: Long,
        private val items: Int
    ) : AutoCloseable {
        private var committed = false
        private var closed = false

        fun commit() {
            check(!closed) { "Bridge reservation is already closed" }
            committed = true
        }

        override fun close() {
            if (closed) return
            closed = true
            if (!committed) {
                usedEncodedChars -= encodedChars
                usedItems -= items
                check(usedEncodedChars >= 0L && usedItems >= 0) {
                    "Bridge reservation accounting underflow"
                }
            }
        }
    }

    var usedEncodedChars: Long = 0L
        private set
    var usedItems: Int = 0
        private set

    init {
        require(maxItems > 0) { "Image item limit must be positive" }
    }

    fun claimRawBytes(rawBytes: Long, headerChars: Int = JPEG_DATA_URL_HEADER_CHARS): Boolean {
        val cost = rawImageClaim(rawBytes, headerChars) ?: return false
        return reserve(listOf(cost))?.let { reservation ->
            reservation.commit()
            reservation.close()
            true
        } ?: false
    }

    fun claimDataUrl(value: String?): Boolean {
        if (value.isNullOrEmpty()) return false
        val cost = dataUrlClaim(value.length.toLong()) ?: return false
        return reserve(listOf(cost))?.let { reservation ->
            reservation.commit()
            reservation.close()
            true
        } ?: false
    }

    fun rawImageClaim(
        rawBytes: Long,
        headerChars: Int = JPEG_DATA_URL_HEADER_CHARS
    ): ImageClaim? {
        if (rawBytes <= 0L || rawBytes > maxSingleRawBytes) return null
        return ImageClaim(encodedDataUrlChars(rawBytes, headerChars))
    }

    fun dataUrlClaim(encodedChars: Long): ImageClaim? {
        if (encodedChars <= 0L ||
            encodedChars > encodedDataUrlChars(maxSingleRawBytes, MAX_DATA_URL_HEADER_CHARS)
        ) return null
        return ImageClaim(encodedChars)
    }

    fun canEverReserve(claims: List<ImageClaim>): Boolean {
        if (claims.isEmpty() || claims.size > maxItems) return false
        var total = 0L
        for (claim in claims) {
            if (claim.encodedChars <= 0L || claim.encodedChars > maxEncodedChars - total) {
                return false
            }
            total += claim.encodedChars
        }
        return total <= maxEncodedChars
    }

    fun reserve(claims: List<ImageClaim>): Reservation? {
        if (!canEverReserve(claims) || claims.size > maxItems - usedItems) return null
        val total = claims.fold(0L) { sum, claim -> Math.addExact(sum, claim.encodedChars) }
        if (total > maxEncodedChars - usedEncodedChars) return null
        usedEncodedChars += total
        usedItems += claims.size
        return Reservation(total, claims.size)
    }

    companion object {
        const val MAX_BATCH_ITEMS = 2
        const val MAX_SINGLE_RAW_BYTES = NativeStoredImagePolicy.MAX_BRIDGE_IMAGE_BYTES
        const val MAX_BATCH_ENCODED_CHARS = 4L * 1024L * 1024L
        private const val JPEG_DATA_URL_HEADER_CHARS = 23
        private const val MAX_DATA_URL_HEADER_CHARS = 64

        fun encodedDataUrlChars(rawBytes: Long, headerChars: Int = JPEG_DATA_URL_HEADER_CHARS): Long {
            require(rawBytes >= 0L) { "Image byte count cannot be negative" }
            require(headerChars >= 0) { "Data URL header cannot be negative" }
            val groups = rawBytes / 3L + if (rawBytes % 3L == 0L) 0L else 1L
            return Math.addExact(Math.multiplyExact(groups, 4L), headerChars.toLong())
        }
    }
}
