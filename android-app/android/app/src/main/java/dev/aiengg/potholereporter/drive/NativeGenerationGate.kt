package dev.aiengg.potholereporter.drive

/**
 * Small thread-safe token gate for asynchronous Android callbacks.
 *
 * A Boolean cannot distinguish `started -> paused -> started` from the original start.
 * Every asynchronous producer therefore carries the exact token issued for its lifetime;
 * callbacks from an older lifetime become inert even when the current Boolean state looks
 * identical again.
 */
internal class NativeGenerationGate {
    private var generation = 0L

    @Synchronized
    fun issue(): Long {
        generation = nextGeneration(generation)
        return generation
    }

    @Synchronized
    fun invalidate(): Long {
        generation = nextGeneration(generation)
        return generation
    }

    @Synchronized
    fun current(): Long = generation

    @Synchronized
    fun isCurrent(token: Long): Boolean = token == generation

    private fun nextGeneration(value: Long): Long =
        if (value == Long.MAX_VALUE) 1L else value + 1L
}
