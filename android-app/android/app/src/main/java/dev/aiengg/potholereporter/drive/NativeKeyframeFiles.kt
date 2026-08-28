package dev.aiengg.potholereporter.drive

import java.io.File
import kotlin.math.abs

/** A deterministic, single-row representation of temporal frames from one camera burst. */
internal object NativeKeyframeFiles {
    private val burstPrimaryPattern =
        Regex("^frame_([0-9]+)_p([0-9]+)_c([0-9]+)_d([0-9]+)\\.jpg$")
    private val pairedPrimaryPattern =
        Regex("^frame_([0-9]+)_p([0-9]+)_c([0-9]+)\\.jpg$")
    private val legacyPrimaryPattern = Regex("^frame_([0-9]+)\\.jpg$")

    data class WritePlan(
        val primaryFile: File,
        val contextFiles: List<File>,
        val primarySourceIndex: Int,
        val contextSourceIndexes: List<Int>
    ) {
        val files: List<File> = listOf(primaryFile) + contextFiles
        val companionFile: File get() = contextFiles.first()
        val companionSourceIndex: Int get() = contextSourceIndexes.first()
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
    ): WritePlan = writePairPlan(directory, captureSeq, primarySourceIndex, companionSourceIndex)

    fun writePlan(
        directory: File,
        captureSeq: Int,
        primarySourceIndex: Int,
        contextSourceIndexes: List<Int>
    ): WritePlan {
        require(captureSeq >= 0) { "Capture sequence cannot be negative" }
        require(primarySourceIndex >= 0 && contextSourceIndexes.all { it >= 0 }) {
            "Source indexes cannot be negative"
        }
        require(contextSourceIndexes.size == 2 && contextSourceIndexes.distinct().size == 2) {
            "A three-frame burst requires two distinct context frames"
        }
        require(primarySourceIndex !in contextSourceIndexes) {
            "Temporal context must use different source frames"
        }
        val orderedContext = contextSourceIndexes.sorted()
        val stem = "frame_${captureSeq.toString().padStart(6, '0')}_" +
            "p${primarySourceIndex}_c${orderedContext[0]}_d${orderedContext[1]}"
        return WritePlan(
            primaryFile = File(directory, "$stem.jpg"),
            contextFiles = orderedContext.map { File(directory, "${stem}_context_$it.jpg") },
            primarySourceIndex = primarySourceIndex,
            contextSourceIndexes = orderedContext
        )
    }

    private fun writePairPlan(
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
            contextFiles = listOf(File(directory, "${stem}_context.jpg")),
            primarySourceIndex = primarySourceIndex,
            contextSourceIndexes = listOf(companionSourceIndex)
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
                // When before/after are equally far from the sharp frame, retain the
                // later/closer view of an approaching road defect for durable replay.
                (distance == selectedDistance && index > selected!!)
            ) {
                selected = index
                selectedDistance = distance
            }
        }
        return selected
    }

    /** Keeps every non-primary view whose timestamp is genuine, in camera-time order. */
    fun selectTemporalContextIndexes(capturedAtMs: List<Long>, primaryIndex: Int): List<Int>? {
        if (primaryIndex !in capturedAtMs.indices) return null
        val primaryAt = capturedAtMs[primaryIndex]
        val context = capturedAtMs.indices
            .filter { it != primaryIndex && capturedAtMs[it] != primaryAt }
            .sortedBy { capturedAtMs[it] }
        return context.takeIf { it.size == 2 }
    }

    /**
     * Files owned by one Room row. Paired names derive exactly one sibling; arbitrary
     * files in the directory are never swept in. Legacy rows own only their old JPEG.
     */
    fun ownedFiles(primaryFile: File): List<File> {
        val burstMatch = burstPrimaryPattern.matchEntire(primaryFile.name)
        if (burstMatch != null) {
            val stem = primaryFile.name.removeSuffix(".jpg")
            val contextIndexes = listOf(burstMatch.groupValues[3], burstMatch.groupValues[4])
            return listOf(primaryFile) + contextIndexes.map { index ->
                File(primaryFile.parentFile, "${stem}_context_$index.jpg")
            }
        }
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
        val burstMatch = burstPrimaryPattern.matchEntire(primaryFile.name)
        if (burstMatch != null) {
            val primarySourceIndex = burstMatch.groupValues[2].toIntOrNull()
                ?: return ReadSet(listOf(primaryFile), 0, false)
            val contextSourceIndexes = listOf(
                burstMatch.groupValues[3].toIntOrNull(),
                burstMatch.groupValues[4].toIntOrNull()
            )
            val validContext = contextSourceIndexes.filterNotNull()
            if (validContext.size != 2 || validContext.distinct().size != 2 ||
                primarySourceIndex in validContext
            ) return ReadSet(listOf(primaryFile), 0, false)
            val indexedFiles = listOf(primarySourceIndex to primaryFile) +
                validContext.zip(ownedFiles(primaryFile).drop(1))
            val ordered = indexedFiles.sortedBy { it.first }
            return ReadSet(
                files = ordered.map { it.second },
                primaryIndex = ordered.indexOfFirst { it.first == primarySourceIndex },
                hasTemporalContext = true
            )
        }
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
        burstPrimaryPattern.matches(file.name) || pairedPrimaryPattern.matches(file.name) ||
            legacyPrimaryPattern.matches(file.name)

    private fun absDifference(left: Long, right: Long): Long {
        if (left == right) return 0L
        // Saturate instead of overflowing for corrupt timestamps.
        val difference = if (left > right) left - right else right - left
        return if (difference < 0L) Long.MAX_VALUE else abs(difference)
    }
}
