package dev.aiengg.potholereporter.drive

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.joinAll
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeLiveInferencePolicyTest {
    @Test
    fun `live concurrency is explicitly bounded to two raw bursts`() {
        assertEquals(2, NativeLiveInferencePolicy.MAX_CONCURRENT_BURSTS)
    }

    @Test
    fun `two consumers admit two active bursts without buffering a third`() = runBlocking {
        val channel = Channel<Int>(Channel.RENDEZVOUS)
        val release = CompletableDeferred<Unit>()
        val workers = List(NativeLiveInferencePolicy.MAX_CONCURRENT_BURSTS) {
            launch(start = CoroutineStart.UNDISPATCHED) {
                for (item in channel) {
                    assertTrue(item > 0)
                    release.await()
                }
            }
        }

        assertTrue(channel.trySend(1).isSuccess)
        assertTrue(channel.trySend(2).isSuccess)
        assertFalse(channel.trySend(3).isSuccess)

        release.complete(Unit)
        channel.close()
        workers.joinAll()
    }
}
