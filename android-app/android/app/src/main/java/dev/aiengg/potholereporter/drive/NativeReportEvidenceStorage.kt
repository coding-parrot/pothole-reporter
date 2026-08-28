package dev.aiengg.potholereporter.drive

import android.content.Context
import dev.aiengg.potholereporter.db.PotholeDatabase
import kotlinx.coroutines.sync.withLock
import java.io.File
import java.io.FileOutputStream

/** Deterministic pruning policy kept Android-free for focused JVM tests. */
internal object NativeReportEvidencePruningPolicy {
    fun oldestUnowned(
        files: List<File>,
        referencedCanonicalPaths: Set<String>,
        protectedRoots: List<File>
    ): List<File> {
        val canonicalProtected = protectedRoots.mapNotNull {
            runCatching { it.canonicalFile }.getOrNull()
        }
        return files.asSequence()
            .filter(File::isFile)
            .mapNotNull { runCatching { it.canonicalFile }.getOrNull() }
            .filter { it.path !in referencedCanonicalPaths }
            .filter { candidate ->
                canonicalProtected.none { root -> contains(root, candidate) }
            }
            // Every item is canonical already. Do not resolve the filesystem a second
            // time here: an entry can disappear between inventory and sorting, and a
            // second canonical lookup would turn a harmless cleanup race into pruning
            // failure.
            .distinctBy(File::getPath)
            .sortedWith(compareBy<File>({ it.lastModified() }, { it.path }))
            .toList()
    }

    private fun contains(root: File, candidate: File): Boolean {
        if (root == candidate) return true
        val prefix = root.path.trimEnd(File.separatorChar) + File.separator
        return candidate.path.startsWith(prefix)
    }
}

/**
 * Separate hard quota for full report and repair evidence under `filesDir/reports`.
 *
 * Every persistent write is reserved and committed while holding the same filesystem mutex
 * as Room ownership and bridge acknowledgement. When space is needed, only paths absent from
 * every report/repair row are considered, oldest first; the active producer directory remains
 * protected until Drive teardown completes.
 */
internal object NativeReportEvidenceStorage {
    const val MAX_TOTAL_BYTES = 512L * 1024L * 1024L
    private val deletionLock = Any()
    private val quota = NativeMediaStorageQuota(
        maxTotalBytes = MAX_TOTAL_BYTES,
        minFreeBytes = NativeMediaStorageQuota.MIN_FREE_BYTES
    )

    /**
     * Opaque reservation held across one inference request and its possible evidence save.
     * The lease reserves the maximum encoded JPEG size without retaining bitmap/JPEG bytes
     * in memory while the network is in flight.
     */
    internal class InferenceCapacityLease internal constructor(
        internal val reservation: NativeMediaStorageQuota.Reservation
    ) {
        private var finished = false

        @Synchronized
        fun finishOnce(): Boolean {
            if (finished) return false
            finished = true
            return true
        }

        @Synchronized
        fun isFinished(): Boolean = finished
    }

    suspend fun initialize(context: Context) {
        val db = PotholeDatabase.getDatabase(context)
        NativeMediaFilesystemMutation.mutex.withLock {
            reconcileFromDiskLocked(context)
            pruneToCapLocked(context, db)
        }
    }

    /** Caller must hold [NativeMediaFilesystemMutation.mutex]. */
    fun reconcileFromDiskLocked(context: Context) {
        // Uncommitted worker cleanup does not suspend to take the filesystem mutex. Share
        // this small monitor so its byte credit cannot race the inventory snapshot.
        synchronized(deletionLock) {
            quota.reconcile(directoryBytes(root(context)))
        }
    }

    suspend fun reserveInferenceCapacity(context: Context): InferenceCapacityLease =
        NativeMediaFilesystemMutation.mutex.withLock {
            if (!quota.isReconciled()) reconcileFromDiskLocked(context)
            val database = PotholeDatabase.getDatabase(context)
            val reservation = reserveAfterPruningLocked(
                context,
                database,
                NativeStoredImagePolicy.MAX_BRIDGE_IMAGE_BYTES
            ) ?: throw NativeInferenceException(
                "Report evidence storage is full. Sync reports or clear app data to continue.",
                suspendInference = true
            )
            InferenceCapacityLease(reservation)
        }

    fun releaseInferenceCapacity(lease: InferenceCapacityLease) {
        if (lease.finishOnce()) quota.release(lease.reservation)
    }

    suspend fun saveJpegAtomically(
        context: Context,
        directory: File,
        fileName: String,
        jpeg: ByteArray,
        lease: InferenceCapacityLease
    ): File = NativeMediaFilesystemMutation.mutex.withLock {
        require(jpeg.isNotEmpty() && jpeg.size.toLong() <= NativeStoredImagePolicy.MAX_BRIDGE_IMAGE_BYTES) {
            "Evidence image is empty or too large"
        }
        require(File(fileName).name == fileName && !fileName.startsWith('.')) {
            "Evidence file name is invalid"
        }
        val reportsRoot = root(context).canonicalFile
        val canonicalDirectory = directory.canonicalFile
        require(contains(reportsRoot, canonicalDirectory, includeRoot = false)) {
            "Evidence directory is outside private report storage"
        }
        if (lease.isFinished() || jpeg.size.toLong() > lease.reservation.bytes) {
            throw NativeInferenceException(
                "Report evidence storage reservation is unavailable.",
                suspendInference = true
            )
        }
        val reservation = lease.reservation
        if (!canonicalDirectory.exists() && !canonicalDirectory.mkdirs()) {
            releaseInferenceCapacity(lease)
            throw NativeInferenceException(
                "Could not create private evidence storage",
                suspendInference = true
            )
        }

        val file = File(canonicalDirectory, fileName)
        val temporary = File(canonicalDirectory, ".$fileName.tmp")
        if (file.exists() || temporary.exists()) {
            releaseInferenceCapacity(lease)
            throw NativeInferenceException(
                "Evidence file already exists",
                suspendInference = true
            )
        }

        try {
            FileOutputStream(temporary).use { out -> out.write(jpeg) }
            if (temporary.length() != jpeg.size.toLong()) {
                throw NativeInferenceException(
                    "Could not write the complete evidence image",
                    suspendInference = true
                )
            }
            val committed = temporary.renameTo(file) || runCatching {
                temporary.copyTo(file, overwrite = false)
                NativeRetryableFileCleanup.deleteVerified(temporary) && file.isFile
            }.getOrDefault(false)
            if (!committed || !file.isFile || file.length() != jpeg.size.toLong()) {
                throw NativeInferenceException(
                    "Could not commit evidence image",
                    suspendInference = true
                )
            }
            if (!quota.commit(reservation, file.length())) {
                throw NativeInferenceException(
                    "Evidence image exceeded its storage reservation",
                    suspendInference = true
                )
            }
            lease.finishOnce()
            file
        } catch (error: Throwable) {
            val survivors = listOf(temporary, file).distinctBy(File::getAbsolutePath)
                .onEach { NativeRetryableFileCleanup.deleteVerified(it) }
                .filter(File::isFile)
                .sumOf { it.length().coerceAtLeast(0L) }
            releaseInferenceCapacity(lease)
            quota.noteUnexpectedExistingFile(survivors)
            throw error
        }
    }

    /** Quota-aware verified deletion for uncommitted and acknowledged evidence. */
    fun deleteVerified(file: File): Boolean = synchronized(deletionLock) {
        val existed = file.isFile
        val bytes = if (existed) file.length().coerceAtLeast(0L) else 0L
        val removed = NativeRetryableFileCleanup.deleteVerified(file)
        if (removed && existed) quota.noteDeletion(bytes)
        removed
    }

    fun deleteAll(files: Iterable<File>): Boolean {
        var complete = true
        files.distinctBy { runCatching { it.canonicalPath }.getOrDefault(it.absolutePath) }
            .forEach { if (!deleteVerified(it)) complete = false }
        return complete
    }

    private suspend fun reserveAfterPruningLocked(
        context: Context,
        db: PotholeDatabase,
        bytes: Long
    ): NativeMediaStorageQuota.Reservation? {
        quota.tryReserve(bytes, context.filesDir.usableSpace)?.let { return it }
        for (candidate in pruningCandidatesLocked(context, db)) {
            deleteVerified(candidate)
            quota.tryReserve(bytes, context.filesDir.usableSpace)?.let { return it }
        }
        return null
    }

    private suspend fun pruneToCapLocked(context: Context, db: PotholeDatabase) {
        if ((quota.accountedBytes() ?: 0L) <= MAX_TOTAL_BYTES) return
        for (candidate in pruningCandidatesLocked(context, db)) {
            deleteVerified(candidate)
            if ((quota.accountedBytes() ?: Long.MAX_VALUE) <= MAX_TOTAL_BYTES) return
        }
    }

    private suspend fun pruningCandidatesLocked(
        context: Context,
        db: PotholeDatabase
    ): List<File> {
        val reportsRoot = root(context)
        val referenced = HashSet<String>()
        db.reportDao().getAllReportMediaRefs().forEach { report ->
            listOfNotNull(report.photoPath, report.photoFullPath).forEach { path ->
                managedDescendant(reportsRoot, path)?.let { referenced.add(it.canonicalPath) }
            }
        }
        db.repairObservationDao().getAllPhotoPaths().forEach { path ->
            managedDescendant(reportsRoot, path)?.let { referenced.add(it.canonicalPath) }
        }
        val status = DriveForegroundService.status()
        val protectedRoots = status.sessionId
            ?.takeIf { status.isRunning || status.isStopping }
            ?.let { sessionId ->
                managedDescendant(reportsRoot, File(reportsRoot, sessionId).absolutePath)
            }
            ?.let(::listOf)
            .orEmpty()
        val files = if (reportsRoot.exists()) {
            reportsRoot.walkTopDown().filter(File::isFile).toList()
        } else emptyList()
        return NativeReportEvidencePruningPolicy.oldestUnowned(
            files,
            referenced,
            protectedRoots
        )
    }

    private fun managedDescendant(root: File, storedPath: String): File? = runCatching {
        val canonicalRoot = root.canonicalFile
        val candidate = File(storedPath).canonicalFile
        candidate.takeIf { contains(canonicalRoot, it, includeRoot = false) }
    }.getOrNull()

    private fun root(context: Context): File = File(context.filesDir, "reports")

    private fun directoryBytes(root: File): Long = if (!root.exists()) 0L else root.walkTopDown()
        .filter(File::isFile)
        .fold(0L) { total, file -> safeAdd(total, file.length().coerceAtLeast(0L)) }

    private fun contains(root: File, candidate: File, includeRoot: Boolean): Boolean {
        if (includeRoot && root == candidate) return true
        val prefix = root.path.trimEnd(File.separatorChar) + File.separator
        return candidate.path.startsWith(prefix)
    }

    private fun safeAdd(left: Long, right: Long): Long =
        if (right > 0L && Long.MAX_VALUE - left < right) Long.MAX_VALUE else left + right
}
