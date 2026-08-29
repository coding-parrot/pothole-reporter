package dev.aiengg.potholereporter.drive

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeInferenceHttpFailurePolicyTest {
    @Test
    fun `only explicit transient statuses and transport are retried`() {
        listOf(0, 408, 409, 425, 429, 500, 503, 599).forEach {
            assertTrue("$it should retry", isTransientInferenceFailure(it))
        }
        listOf(300, 400, 401, 403, 404, 422).forEach {
            assertFalse("$it should not retry", isTransientInferenceFailure(it))
        }
    }

    @Test
    fun `retry after and exponential backoff are bounded`() {
        assertNull(inferenceRetryDelay(400, "30", 0))
        assertNull(inferenceRetryDelay(401, "30", 0))
        assertNull(inferenceRetryDelay(404, "30", 0))
        assertEquals(12_000L, inferenceRetryDelay(429, "12", 0))
        assertEquals(10_000L, inferenceRetryDelay(429, null, 1))
        assertEquals(60_000L, inferenceRetryDelay(429, "999", 10))
        assertEquals(2_000L, inferenceRetryDelay(408, null, 0))
        assertEquals(4_000L, inferenceRetryDelay(503, null, 1))
    }

    @Test
    fun `transport failures receive bounded backoff too`() {
        assertEquals(10_000L, inferenceRetryDelay(0, null, 0))
        assertEquals(20_000L, inferenceRetryDelay(0, null, 1))
        assertEquals(60_000L, inferenceRetryDelay(0, null, 10))
    }
}
