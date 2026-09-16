package com.millsandgoon.ear

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The lesson ladder and the tables under it.
 *
 * These used to be checked against a Python copy on the server, because there
 * were three transcriptions of the same table and any one of them could drift.
 * There is one now, so the tests assert on it directly — which is stronger
 * than comparing two copies that could both be wrong.
 */
class IntervalsTest {

    @Test fun `every lesson is playable`() {
        for (l in LESSONS) {
            assertTrue("lesson ${l.n} is empty", l.set.isNotEmpty())
            assertTrue("lesson ${l.n} has a blank title", l.title.isNotBlank())
        }
    }

    @Test fun `interval lessons name intervals and chord lessons name chords`() {
        for (l in LESSONS) {
            for (id in l.set) {
                when (l.kind) {
                    Kind.INTERVAL -> assertTrue("$id is not an interval", id in INTERVALS)
                    Kind.CHORD -> assertTrue("$id is not a chord", id in CHORDS)
                }
            }
        }
    }

    @Test fun `the ladder covers everything it teaches`() {
        val taught = LESSONS.flatMap { it.set }.toSet()
        assertEquals("intervals nothing teaches", emptySet<String>(), INTERVALS.keys - taught)
        assertEquals("chords nothing teaches", emptySet<String>(), CHORDS.keys - taught)
    }

    @Test fun `every group has lessons`() {
        for (g in Group.values()) {
            assertTrue("${g.label} has no lessons", lessonsIn(g).isNotEmpty())
        }
    }

    /** Enum order is menu order, so the order lives in the enum. */
    @Test fun `the menu leads with melodic`() {
        assertEquals(listOf("MELODIC", "HARMONIC", "CHORDS"),
            Group.values().map { it.name })
        assertEquals(listOf("Melodic", "Harmonic", "Chords"),
            Group.values().map { it.label })
    }

    @Test fun `semitones are distinct and ordered`() {
        val steps = INTERVALS.values.map { it.semitones }
        assertEquals("two intervals share a size", steps.size, steps.toSet().size)
        assertEquals("the table is out of order", steps.sorted(), steps)
    }

    /** The root is implied, never stored — `offsetsOf` returns the notes above
     *  it, and the synth is handed the root separately. A stored 0 would sound
     *  the root twice. */
    @Test fun `chords are ascending offsets above an implied root`() {
        for ((id, chord) in CHORDS) {
            assertTrue("$id stores the root", chord.offsets.all { it > 0 })
            assertEquals("$id is out of order", chord.offsets.sorted(), chord.offsets)
            assertEquals("$id repeats a note", chord.offsets.size, chord.offsets.toSet().size)
        }
        for (id in INTERVALS.keys + CHORDS.keys) {
            assertTrue("$id sounds the root twice", offsetsOf(id).none { it == 0 })
        }
    }

    @Test fun `every lesson number is unique and every lesson findable`() {
        val numbers = LESSONS.map { it.n }
        assertEquals("two lessons share a number", numbers.size, numbers.toSet().size)
        for (l in LESSONS) assertEquals(l, lessonOf(l.n))
    }

    @Test fun `every item has something to say and something to show`() {
        for (id in INTERVALS.keys + CHORDS.keys) {
            assertTrue("$id displays as nothing", displayOf(id).isNotBlank())
            assertTrue("$id sounds as nothing", offsetsOf(id).isNotEmpty())
        }
    }

    // ---- hearing ----

    @Test fun `the grammar holds only answers, never instructions`() {
        val grammar = voskGrammar()
        for (word in listOf("repeat", "skip", "pause", "stop", "lesson", "score")) {
            assertFalse("\"$word\" is still in the grammar", grammar.contains("\"$word\""))
        }
        assertTrue("humming has nowhere to go", grammar.contains("[unk]"))
    }

    @Test fun `every answer in a lesson can be heard back`() {
        for (l in LESSONS) {
            for (id in l.set) {
                val heard = interpret(listOf(displayOf(id)), l)
                assertTrue("${displayOf(id)} came back as $heard",
                    heard is Heard.Answer && heard.id == id ||
                    heard is Heard.Ambiguous && id in heard.options)
            }
        }
    }

    @Test fun `humming is not an answer`() {
        for (noise in listOf("[unk]", "", "mmm", "la la la")) {
            assertEquals("\"$noise\" scored something", null, interpret(listOf(noise), LESSONS[0]))
        }
    }
}
