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
     * The whole point: every question the drill can generate must be drawn as
     * the notes it really is, on frets that really sound them. The root range
     * in EarService is bounded by the neck for exactly this reason.
     */
    @Test fun `every question the drill can ask is drawn as the right notes`() {
        var checked = 0
        forEveryQuestion { id, root, midis ->
            val shape = Neck.shapeOf(midis)
            assertEquals("$id at $root lost a note", midis.size, shape.size)
            assertEquals("$id at $root is drawn as the wrong notes",
                midis.sorted(), shape.map { Neck.midiAt(it.string, it.fret) })
            for (spot in shape) {
                assertTrue("$id at $root runs off the neck", spot.fret in 0..Neck.FRETS)
            }
            checked++
        }
        assertTrue("the sweep did not run", checked > 200)
    }

    /**
     * Notes that sound together have to be reachable together. Above a certain
     * register the upper notes of a four-note voicing all want the top string,
     * which is why chords keep to CHORD_TOP while melodic notes do not.
     */
    @Test fun `every chord the drill can ask is a shape a hand could hold`() {
        for (lesson in LESSONS.filter { it.mode == Mode.HARMONIC }) {
            for (id in lesson.set) {
                val offsets = offsetsOf(id)
                for (root in 50..(Neck.CHORD_TOP - offsets.max())) {
                    val midis = listOf(root) + offsets.map { root + it }
                    val strings = Neck.shapeOf(midis).map { it.string }
                    assertEquals("$id at $root wants one string twice",
                        strings.size, strings.toSet().size)
                    assertEquals("$id at $root climbs out of order",
                        strings.sorted(), strings)
                }
            }
        }
    }

    /** First position wherever first position will have it: the easiest shape
     *  to read, and the one a learner already knows. */
    @Test fun `a shape sits as low on the neck as it can`() {
        val shape = Neck.shapeOf(listOf(50, 57, 62))
        assertEquals(listOf(2, 3, 4), shape.map { it.string })
        assertEquals(listOf(0, 2, 3), shape.map { it.fret })
    }

    /** Taking each note greedily gives the root a high string and strands
     *  everything above it. The whole assignment is chosen at once. */
    @Test fun `a high root does not strand the notes above it`() {
        val shape = Neck.shapeOf(listOf(60, 64, 67))
        assertEquals(3, shape.size)
        assertEquals(listOf(60, 64, 67), shape.map { Neck.midiAt(it.string, it.fret) })
        assertEquals("these three are reachable together",
            3, shape.map { it.string }.toSet().size)
    }

    private fun forEveryQuestion(check: (String, Int, List<Int>) -> Unit) {
        for (lesson in LESSONS) {
            val ceiling = if (lesson.mode == Mode.MELODIC) Neck.HIGHEST else Neck.CHORD_TOP
            for (id in lesson.set) {
                val offsets = offsetsOf(id)
                for (root in 50..(ceiling - offsets.max())) {
                    check(id, root, listOf(root) + offsets.map { root + it })
                }
            }
        }
    }
}
