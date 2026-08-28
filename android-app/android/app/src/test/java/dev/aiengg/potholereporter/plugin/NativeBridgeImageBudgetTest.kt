package dev.aiengg.potholereporter.plugin

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class NativeBridgeImageBudgetTest {
    @Test
    fun encodedLengthAccountsForBase64PaddingAndHeader() {
        assertEquals(23L, NativeBridgeImageBudget.encodedDataUrlChars(0L))
        assertEquals(27L, NativeBridgeImageBudget.encodedDataUrlChars(1L))
        assertEquals(27L, NativeBridgeImageBudget.encodedDataUrlChars(2L))
        assertEquals(27L, NativeBridgeImageBudget.encodedDataUrlChars(3L))
        assertEquals(31L, NativeBridgeImageBudget.encodedDataUrlChars(4L))
        assertThrows(ArithmeticException::class.java) {
            NativeBridgeImageBudget.encodedDataUrlChars(Long.MAX_VALUE)
        }
    }

    @Test
    fun rawImagesAreRejectedBeforeTheyExceedSingleOrAggregateLimit() {
        val budget = NativeBridgeImageBudget(maxEncodedChars = 31L, maxSingleRawBytes = 4L)

        assertTrue(budget.claimRawBytes(1L))
        assertEquals(27L, budget.usedEncodedChars)
        assertFalse(budget.claimRawBytes(1L))
        assertEquals(27L, budget.usedEncodedChars)
        assertFalse(budget.claimRawBytes(5L))
        assertFalse(budget.claimRawBytes(0L))
        assertEquals(27L, budget.usedEncodedChars)
    }

    @Test
    fun alreadyEncodedImagesConsumeTheSameBatchBudgetWithoutPartialClaims() {
        val budget = NativeBridgeImageBudget(maxEncodedChars = 50L, maxSingleRawBytes = 32L)

        assertTrue(budget.claimDataUrl("data:image/jpeg;base64,AAAA"))
        val used = budget.usedEncodedChars
        assertFalse(budget.claimDataUrl("x".repeat(50)))
        assertFalse(budget.claimDataUrl(null))
        assertEquals(used, budget.usedEncodedChars)
    }

    @Test
    fun noNativeResultCanCarryMoreThanThreeImagesEvenWhenTheyAreTiny() {
        val budget = NativeBridgeImageBudget(
            maxEncodedChars = 1_000L,
            maxSingleRawBytes = 100L
        )

        assertTrue(budget.claimRawBytes(1L))
        assertTrue(budget.claimRawBytes(1L))
        assertTrue(budget.claimRawBytes(1L))
        assertEquals(3, budget.usedItems)
        assertFalse(budget.claimRawBytes(1L))
        assertEquals(3, budget.usedItems)
    }

    @Test
    fun uncommittedRecordReservationRollsBackEveryImageClaim() {
        val budget = NativeBridgeImageBudget(
            maxEncodedChars = 1_000L,
            maxSingleRawBytes = 100L
        )
        val claims = listOfNotNull(
            budget.rawImageClaim(40L),
            budget.rawImageClaim(50L)
        )

        val failedRecord = requireNotNull(budget.reserve(claims))
        assertEquals(2, budget.usedItems)
        failedRecord.close()
        assertEquals(0, budget.usedItems)
        assertEquals(0L, budget.usedEncodedChars)

        val retry = requireNotNull(budget.reserve(claims))
        retry.commit()
        retry.close()
        assertEquals(2, budget.usedItems)
        assertEquals(claims.sumOf { it.encodedChars }, budget.usedEncodedChars)
    }
}
