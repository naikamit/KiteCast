package com.millsandgoon.ear

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * A circle drawn on the wrong fret is a lie told confidently — nobody notices
 * until they pick up a guitar and find the app has been teaching the wrong
 * shape for a fortnight. So the arithmetic is checked against every question
 * the drill can actually ask.
 */
class NeckTest {

    @Test fun `the tuning is a guitar in standard tuning`() {
        assertEquals(listOf(40, 45, 50, 55, 59, 64), Neck.OPEN.toList())
        // Every step a fourth but the major third between G and B.
        val steps = Neck.OPEN.toList().zipWithNext { a, b -> b - a }
        assertEquals(listOf(5, 5, 5, 4, 5), steps)
    }

    @Test fun `a spot sounds the note it is drawn for`() {
        for (midi in Neck.LOWEST..Neck.HIGHEST) {
            val places = Neck.places(midi)
            assertTrue("nothing plays $midi", places.isNotEmpty())
            for (p in places) {
                assertEquals("$p is not $midi", midi, Neck.midiAt(p.string, p.fret))
                assertTrue("$p is off the neck", p.fret in 0..Neck.FRETS)
                assertTrue("$p is not a string", p.string in Neck.OPEN.indices)
            }
        }
    }

    @Test fun `notes outside the neck have nowhere to go`() {
        assertTrue(Neck.places(Neck.LOWEST - 1).isEmpty())
        assertTrue(Neck.places(Neck.HIGHEST + 1).isEmpty())
    }

    /**
     * The whole point: every question the drill can generate must be drawable,
     * every note of it, on its own string and at the fret that really sounds
     * it. The root range in EarService is bounded by the neck for this reason.
     */
    @Test fun `every question the drill can ask has a shape`() {
        var checked = 0
        for (lesson in LESSONS) {
            for (id in lesson.set) {
                val offsets = offsetsOf(id)
                val span = offsets.max()
                for (root in 50..(Neck.HIGHEST - span)) {
                    val midis = listOf(root) + offsets.map { root + it }
                    val shape = Neck.shapeOf(midis)

                    assertEquals("$id at $root lost a note", midis.size, shape.size)
                    assertEquals("$id at $root is drawn as the wrong notes",
                        midis.sorted(), shape.map { Neck.midiAt(it.string, it.fret) })
                    assertEquals("$id at $root doubles up a string",
                        shape.size, shape.map { it.string }.toSet().size)
                    checked++
                }
            }
        }
        assertTrue("the sweep did not run", checked > 200)
    }

    /** Ascending notes on ascending strings is what makes it a shape a hand
     *  could hold rather than marks strung along one string. */
    @Test fun `a shape climbs the strings as it climbs in pitch`() {
        val shape = Neck.shapeOf(listOf(50, 57, 62))
        assertEquals(listOf(0, 2, 3), shape.map { it.fret })
        assertEquals(listOf(2, 3, 4), shape.map { it.string })
    }
}
