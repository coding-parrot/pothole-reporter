package dev.aiengg.potholereporter.drive

/** Process-owned truth for one pending Drive start and its recent completion acknowledgements. */
internal class DriveStartRegistry(
    private val nowMs: () -> Long,
    private val admissionTtlMs: Long = 30_000L
) {
    private data class Admission(
        val requestId: String,
        val admittedAtMs: Long,
        val captureSource: NativeFrameSourceKind,
        val cancelled: Boolean = false,
        val discardData: Boolean = false
    )

    private var admission: Admission? = null
    private val completions = LinkedHashMap<String, DriveEndSummary>()

    @Synchronized
    fun admit(
        requestId: String,
        captureSource: NativeFrameSourceKind = NativeFrameSourceKind.PHONE_CAMERA
    ): Boolean {
        expireAdmission()
        if (admission != null) return false
        admission = Admission(requestId, nowMs(), captureSource)
        return true
    }

    @Synchronized
    fun cancel(requestId: String, discardData: Boolean): Boolean {
        expireAdmission()
        val current = admission?.takeIf { it.requestId == requestId } ?: return false
        admission = current.copy(
            cancelled = true,
            discardData = current.discardData || discardData
        )
        return true
    }

    @Synchronized
    fun clear(requestId: String?) {
        if (requestId != null && admission?.requestId == requestId) admission = null
    }

    @Synchronized
    fun cancellationFor(requestId: String?): Boolean? {
        expireAdmission()
        val current = admission ?: return null
        return if (requestId == current.requestId && current.cancelled) {
            current.discardData
        } else {
            null
        }
    }

    @Synchronized
    fun pendingRequestId(): String? {
        expireAdmission()
        return admission?.requestId
    }

    @Synchronized
    fun pendingCaptureSource(): NativeFrameSourceKind? {
        expireAdmission()
        return admission?.captureSource
    }

    @Synchronized
    fun recordCompletion(requestId: String?, summary: DriveEndSummary) {
        if (requestId.isNullOrBlank() || requestId in completions) return
        completions[requestId] = summary
        while (completions.size > MAX_COMPLETIONS) {
            completions.remove(completions.keys.first())
        }
    }

    @Synchronized
    fun completionFor(requestId: String): DriveEndSummary? = completions[requestId]

    private fun expireAdmission() {
        val current = admission ?: return
        if (nowMs() - current.admittedAtMs > admissionTtlMs) admission = null
    }

    companion object {
        private const val MAX_COMPLETIONS = 32
    }
}
