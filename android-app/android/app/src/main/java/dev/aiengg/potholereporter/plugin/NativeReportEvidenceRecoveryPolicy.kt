package dev.aiengg.potholereporter.plugin

import dev.aiengg.potholereporter.db.ReportMediaRef

internal data class NativeLiveKeyframeIdentity(
    val sessionId: String,
    val captureSeq: Int
)

/** Pure, bounded policy for repairing native outbox rows that cannot cross the WebView bridge. */
internal object NativeReportEvidenceRecoveryPolicy {
    const val REPORT_PAGE_SIZE = 32

    fun hasCompleteReferenceShape(report: ReportMediaRef): Boolean =
        (report.photoFullPath?.isNotBlank() == true || report.photoPath?.isNotBlank() == true) &&
            (report.photoDataUrlChars ?: 0L) > 0L

    /**
     * Only the factory's exact `live:<session>:<sequence>` identity may reopen a keyframe.
     * Approximate location/time matching could re-run the wrong road event.
     */
    fun exactLiveKeyframeIdentity(report: ReportMediaRef): NativeLiveKeyframeIdentity? {
        val sessionId = report.driveId?.takeIf(String::isNotBlank) ?: return null
        val sourceEventKey = report.sourceEventKey ?: return null
        val prefix = "live:$sessionId:"
        if (!sourceEventKey.startsWith(prefix)) return null
        val captureSeq = sourceEventKey.removePrefix(prefix).toIntOrNull()
            ?.takeIf { it > 0 } ?: return null
        if (sourceEventKey != "$prefix$captureSeq") return null
        return NativeLiveKeyframeIdentity(sessionId, captureSeq)
    }
}
