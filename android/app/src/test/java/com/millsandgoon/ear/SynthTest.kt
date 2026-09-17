package com.millsandgoon.ear

import kotlin.math.abs
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.sqrt
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * How loud a question comes out.
 *
 * This is measured rather than asserted about, because the bug it guards
 * against was invisible in the code and obvious in the ear: every note was
 * normalised to a fixed peak, peaks track the sharpest transient rather than
 * the body of the note, and a mixing gain guessed at headroom on top. Across
 * voices, roots and chord shapes that left an 8.8 dB spread — some questions
 * loud, some faint, at random.
 *
 * The loudness meter below is written out again rather than shared with the
 * synth: a normaliser checked with its own ruler proves nothing.
 */
class SynthTest {

    private fun momentary(buf: FloatArray): Float {
        val w = Synth.SAMPLE_RATE / 20                 // 50 ms
        if (buf.size < w) return 0f
        var best = 0f
        var i = 0
        while (i + w <= buf.size) {
            var sum = 0.0
            for (j in i until i + w) { val v = buf[j].toDouble(); sum += v * v }
            best = max(best, sqrt(sum / w).toFloat())
            i += w / 2
        }
        return best
    }

    /** One of each kind the lessons actually ask for. */
    private val shapes = listOf(
        Triple("melodic second", listOf(1), true),
        Triple("melodic fifth", listOf(7), true),
        Triple("melodic seventh chord", listOf(4, 7, 11), true),
        Triple("harmonic fifth", listOf(7), false),
        Triple("major triad", listOf(4, 7), false),
        Triple("half diminished", listOf(3, 6, 10), false))

    @Test fun `every question leaves at the same loudness`() {
        var quietest = Float.MAX_VALUE
        var loudest = 0f
        var worst = ""
        for (voice in Synth.Voice.values()) {
            for (root in listOf(50, 62, 71)) {
                for ((name, offsets, melodic) in shapes) {
                    val level = momentary(Synth.renderQuestion(voice, root, offsets, melodic))
                    assertTrue("$name on ${voice.label} came out silent", level > 0.01f)
                    if (level < quietest) { quietest = level; worst = "$name/${voice.label}/$root" }
                    loudest = max(loudest, level)
                }
            }
        }
        val dB = 20 * log10(loudest / quietest)
        assertTrue("loudness spread is %.2f dB, quietest was %s".format(dB, worst), dB < 1.0)
    }

    @Test fun `nothing clips`() {
        for (voice in Synth.Voice.values()) {
            for ((name, offsets, melodic) in shapes) {
                val buf = Synth.renderQuestion(voice, 62, offsets, melodic)
                var peak = 0f
                for (v in buf) peak = max(peak, abs(v))
                assertTrue("$name on ${voice.label} peaks at $peak", peak < 1.0f)
            }
        }
    }

    /** The chime answers a question; it should not be the loudest thing in
     *  the session. */
    @Test fun `the chime sits under the questions`() {
        val chime = momentary(Synth.chime())
        val question = momentary(Synth.renderQuestion(Synth.Voice.PIANO, 62, listOf(7), true))
        assertTrue("the chime is louder than the drill", chime < question)
        assertTrue("the chime is inaudible", chime > question * 0.4f)
    }
}
