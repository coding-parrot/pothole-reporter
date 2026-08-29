package dev.aiengg.potholereporter.drive

import android.content.Context
import android.graphics.Bitmap
import android.util.Base64
import java.io.File

/** Owns inference evidence encoding and private-file persistence. */
internal class NativeInferenceEvidenceStore(private val context: Context) {
    suspend fun saveDetection(
        bitmap: Bitmap,
        driveId: String,
        captureSeq: Int,
        lease: NativeReportEvidenceStorage.InferenceCapacityLease
    ): File = saveJpegAtomically(
        bitmap = bitmap,
        directory = reportDirectory(driveId),
        fileName = "evidence_${captureSeq}_${System.currentTimeMillis()}.jpg",
        quality = 92,
        lease = lease
    )

    suspend fun saveRepair(
        bitmap: Bitmap,
        driveId: String,
        targetReportId: Long,
        captureSeq: Int,
        lease: NativeReportEvidenceStorage.InferenceCapacityLease
    ): File = saveJpegAtomically(
        bitmap = bitmap,
        directory = reportDirectory(driveId),
        fileName = "repair_${targetReportId}_${captureSeq}_${System.currentTimeMillis()}.jpg",
        quality = 88,
        lease = lease
    )

    fun thumbnailDataUrl(bitmap: Bitmap): String? =
        FrameQualityEvaluator.bitmapToBoundedJpegBytes(
            bitmap,
            NativeStoredImagePolicy.MAX_ROOM_THUMB_IMAGE_BYTES,
            NativeStoredImagePolicy.ROOM_THUMB_MAX_DIMENSION,
            78
        )?.let { bytes ->
            "data:image/jpeg;base64," + Base64.encodeToString(bytes, Base64.NO_WRAP)
        }

    fun fileDataUrl(path: String, mime: String): String? {
        val file = File(path)
        if (!file.isFile || file.length() <= 0 || file.length() > MAX_SOURCE_IMAGE_BYTES) return null
        val safeMime = mime.takeIf { it in SAFE_IMAGE_MIME_TYPES } ?: return null
        return "data:$safeMime;base64," + Base64.encodeToString(file.readBytes(), Base64.NO_WRAP)
    }

    private fun reportDirectory(driveId: String): File {
        val safeDriveId = driveId.replace(UNSAFE_FILE_COMPONENT, "_").take(128)
        return File(context.filesDir, "reports/$safeDriveId")
    }

    private suspend fun saveJpegAtomically(
        bitmap: Bitmap,
        directory: File,
        fileName: String,
        quality: Int,
        lease: NativeReportEvidenceStorage.InferenceCapacityLease
    ): File {
        val jpeg = FrameQualityEvaluator.bitmapToBoundedJpegBytes(
            bitmap = bitmap,
            maxBytes = NativeStoredImagePolicy.MAX_BRIDGE_IMAGE_BYTES,
            maxDimension = NativeStoredImagePolicy.EVIDENCE_MAX_DIMENSION,
            initialQuality = quality
        ) ?: throw NativeInferenceException("Could not encode evidence within the safe image limit")
        return try {
            NativeReportEvidenceStorage.saveJpegAtomically(
                context,
                directory,
                fileName,
                jpeg,
                lease
            )
        } catch (error: NativeInferenceException) {
            throw error
        } catch (error: Exception) {
            throw NativeInferenceException(
                "Could not save evidence image: ${error.message ?: "storage error"}",
                suspendInference = true,
                cause = error
            )
        }
    }

    companion object {
        private const val MAX_SOURCE_IMAGE_BYTES = 8L * 1024L * 1024L
        private val SAFE_IMAGE_MIME_TYPES = setOf("image/jpeg", "image/png", "image/webp", "image/gif")
        private val UNSAFE_FILE_COMPONENT = Regex("[^A-Za-z0-9_-]")
    }
}

/** Publishes file ownership to the caller or removes the otherwise orphaned evidence. */
internal fun publishEvidence(file: File, receiver: (String) -> Unit) {
    try {
        receiver(file.absolutePath)
    } catch (error: Throwable) {
        NativeReportEvidenceStorage.deleteVerified(file)
        throw error
    }
}
