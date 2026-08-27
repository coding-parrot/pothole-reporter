package dev.aiengg.potholereporter.drive

import java.io.File
import kotlin.math.abs

/** A deterministic, single-row representation of two frames from one camera burst. */
internal object NativeKeyframeFiles {
    private val pairedPrimaryPattern =
        Regex("^frame_([0-9]+)_p([0-9]+)_c([0-9]+)\\.jpg$")
    private val legacyPrimaryPattern = Regex("^frame_([0-9]+)\\.jpg$")

    data class WritePlan(
        val primaryFile: File,
        val companionFile: File,
        val primarySourceIndex: Int,
        val companionSourceIndex: Int
    ) {
        val files: List<File> = listOf(primaryFile, companionFile)
    }

    data class ReadSet(
        val files: List<File>,
        val primaryIndex: Int,
        val hasTemporalContext: Boolean
    ) {
        val isComplete: Boolean
            get() = files.isNotEmpty() && files.all { it.isFile && it.length() > 0L }
    }

    fun writePlan(
        directory: File,
        captureSeq: Int,
        primarySourceIndex: Int,
        companionSourceIndex: Int
    ): WritePlan {
        require(captureSeq >= 0) { "Capture sequence cannot be negative" }
        require(primarySourceIndex >= 0 && companionSourceIndex >= 0) {
            "Source indexes cannot be negative"
        }
        require(primarySourceIndex != companionSourceIndex) {
            "A temporal companion must be a different source frame"
        }
        val stem = "frame_${captureSeq.toString().padStart(6, '0')}_" +
            "p${primarySourceIndex}_c${companionSourceIndex}"
        return WritePlan(
            primaryFile = File(directory, "$stem.jpg"),
            companionFile = File(directory, "${stem}_context.jpg"),
            primarySourceIndex = primarySourceIndex,
            companionSourceIndex = companionSourceIndex
        )
    }

    /**
     * Returns the farthest genuinely later/earlier frame in the burst. Equal timestamps
     * are not temporal evidence and therefore fail closed.
     */
    fun selectTemporalCompanionIndex(capturedAtMs: List<Long>, primaryIndex: Int): Int? {
        if (primaryIndex !in capturedAtMs.indices) return null
        val primaryAt = capturedAtMs[primaryIndex]
        var selected: Int? = null
        var selectedDistance = 0L
        capturedAtMs.indices.forEach { index ->
            if (index == primaryIndex || capturedAtMs[index] == primaryAt) return@forEach
            val distance = absDifference(capturedAtMs[index], primaryAt)
            if (selected == null || distance > selectedDistance ||
                (distance == selectedDistance && index < selected!!)
            ) {
                selected = index
                selectedDistance = distance
            }
        }
        return selected
    }

    /**
     * Files owned by one Room row. Paired names derive exactly one sibling; arbitrary
     * files in the directory are never swept in. Legacy rows own only their old JPEG.
     */
    fun ownedFiles(primaryFile: File): List<File> {
        if (!pairedPrimaryPattern.matches(primaryFile.name)) return listOf(primaryFile)
        val stem = primaryFile.name.removeSuffix(".jpg")
        return listOf(primaryFile, File(primaryFile.parentFile, "${stem}_context.jpg"))
    }

    /**
     * Returns files in camera-time order and the evidence frame's position. A legacy
     * single-file row is deliberately exposed as one view, so strict two-view detection
     * rejects it unless independently supplied with real temporal video context.
     */
    fun readSet(primaryFile: File): ReadSet {
        val match = pairedPrimaryPattern.matchEntire(primaryFile.name)
        if (match == null) {
            // Malformed names are treated like legacy single-view rows: never infer a
            // companion by scanning the directory.
            return ReadSet(listOf(primaryFile), primaryIndex = 0, hasTemporalContext = false)
        }
        val primarySourceIndex = match.groupValues[2].toIntOrNull()
            ?: return ReadSet(listOf(primaryFile), 0, false)
        val companionSourceIndex = match.groupValues[3].toIntOrNull()
            ?: return ReadSet(listOf(primaryFile), 0, false)
        if (primarySourceIndex == companionSourceIndex) {
            return ReadSet(listOf(primaryFile), 0, false)
        }
        val companion = ownedFiles(primaryFile)[1]
        return if (primarySourceIndex < companionSourceIndex) {
            ReadSet(listOf(primaryFile, companion), primaryIndex = 0, hasTemporalContext = true)
        } else {
            ReadSet(listOf(companion, primaryFile), primaryIndex = 1, hasTemporalContext = true)
        }
    }

    fun isRecognizedPrimary(file: File): Boolean =
        pairedPrimaryPattern.matches(file.name) || legacyPrimaryPattern.matches(file.name)

    private fun absDifference(left: Long, right: Long): Long {
        if (left == right) return 0L
        // Saturate instead of overflowing for corrupt timestamps.
        val difference = if (left > right) left - right else right - left
        return if (difference < 0L) Long.MAX_VALUE else abs(difference)
    }
}
