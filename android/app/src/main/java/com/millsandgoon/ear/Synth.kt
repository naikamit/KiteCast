package com.millsandgoon.ear

import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.exp
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.pow
import kotlin.math.sin
import kotlin.math.sqrt
import kotlin.random.Random

/**
 * Instrument synthesis — a transliteration of ear/audio.js, whose numbers were
 * measured rather than guessed: guitars within 0.4 cents absolute, interval
 * error under 0.25 cents on all three voices.
 *
 * Piano is additive with string inharmonicity. Both guitars are Karplus-Strong
 * with a fractionally interpolated delay line, because integer delay lengths
 * detune high notes by up to ~17 cents and interval identification is the
 * entire task.
 */
object Synth {

    const val SAMPLE_RATE = 48000

    /** Vosk's models are trained at 16 kHz; feeding anything else degrades them. */
    const val SAMPLE_RATE_RECOGNITION = 16000.0f

    enum class Voice(val label: String) {
        PIANO("piano"), NYLON("classical guitar"), STEEL("acoustic guitar")
    }

    fun midiToFreq(midi: Int): Double = 440.0 * 2.0.pow((midi - 69) / 12.0)

    // ---- piano -----------------------------------------------------------

    private fun renderPiano(freq: Double, seconds: Double): FloatArray {
        val n = Math.ceil(seconds * SAMPLE_RATE).toInt()
        val out = DoubleArray(n)
        val b = 0.00015                    // midrange string stretch
        val nyquist = SAMPLE_RATE * 0.45

        for (p in 1..20) {
            val f = p * freq * sqrt(1 + b * p * p)
            if (f > nyquist) break
            val amp = p.toDouble().pow(-1.25) * (if (p % 2 == 0) 0.82 else 1.0)
            val tau = 2.4 / (1 + 0.6 * (p - 1))
            val w = 2 * Math.PI * f / SAMPLE_RATE
            val c = cos(w); val s = sin(w)
            val phase = Random.nextDouble() * 2 * Math.PI
            var re = cos(phase); var im = sin(phase)
            var env = amp
            val decay = exp(-1.0 / (tau * SAMPLE_RATE))
            for (i in 0 until n) {
                out[i] += env * im
                val nre = re * c - im * s
                im = re * s + im * c
                re = nre
                env *= decay
            }
        }

        // hammer thump
        var lp = 0.0
        val hn = (SAMPLE_RATE * 0.018).toInt()
        for (i in 0 until hn) {
            lp += 0.22 * ((Random.nextDouble() * 2 - 1) - lp)
            out[i] += lp * 0.5 * (1 - i.toDouble() / hn)
        }

        shape(out, 0.004)
        return out.map { it.toFloat() }.toFloatArray()
    }

    // ---- plucked string --------------------------------------------------

    private fun renderPluck(freq: Double, seconds: Double, s: Double, t60: Double,
                            bright: Double, pick: Double): FloatArray {
        val n = Math.ceil(seconds * SAMPLE_RATE).toInt()
        val out = DoubleArray(n)

        // Loop gain set so the fundamental reaches -60 dB at t60 whatever the
        // pitch. Raw Karplus-Strong kills high notes far too quickly.
        val g = exp(-6.9078 / (freq * t60))

        // The one-pole loop filter contributes (1 - s) samples of delay.
        val l = SAMPLE_RATE / freq - (1 - s)
        val d = max(2, floor(l).toInt())
        val frac = l - d
        val m = d + 1

        val buf = DoubleArray(m)
        var lp = 0.0
        for (i in 0 until m) {
            lp += bright * ((Random.nextDouble() * 2 - 1) - lp)
            buf[i] = lp
        }
        var mean = 0.0
        for (v in buf) mean += v
        mean /= m
        var peak = 1e-9
        for (i in 0 until m) { buf[i] -= mean; peak = max(peak, abs(buf[i])) }
        for (i in 0 until m) buf[i] = buf[i] / peak * 0.85

        var p = 0
        var last = 0.0
        for (i in 0 until n) {
            val xd = (1 - frac) * buf[(p + 1) % m] + frac * buf[p]
            val y = g * (s * xd + (1 - s) * last)
            last = xd
            out[i] = y
            buf[p] = y
            p = (p + 1) % m
        }

        if (pick > 0) {
            val pn = (SAMPLE_RATE * 0.004).toInt()
            for (i in 0 until pn) out[i] += (Random.nextDouble() * 2 - 1) * pick * (1 - i.toDouble() / pn)
        }

        shape(out, 0.002)
        return out.map { it.toFloat() }.toFloatArray()
    }

    private fun shape(buf: DoubleArray, attackSeconds: Double) {
        val a = (SAMPLE_RATE * attackSeconds).toInt()
        for (i in 0 until a) buf[i] *= i.toDouble() / a
        val r = (SAMPLE_RATE * 0.05).toInt()
        for (i in 0 until r) {
            val j = buf.size - 1 - i
            if (j >= 0) buf[j] *= i.toDouble() / r
        }
    }

    private fun note(voice: Voice, midi: Int, seconds: Double): FloatArray {
        val f = midiToFreq(midi)
        val raw = when (voice) {
            Voice.PIANO -> renderPiano(f, seconds)
            Voice.NYLON -> renderPluck(f, seconds, 0.34, 1.9, 0.11, 0.0)
            Voice.STEEL -> renderPluck(f, seconds, 0.56, 3.1, 0.40, 0.06)
        }
        var peak = 1e-9f
        for (v in raw) peak = max(peak, abs(v))
        val norm = 0.55f / peak
        for (i in raw.indices) raw[i] *= norm
        return raw
    }

    /**
     * One whole question as a single buffer. The web version scheduled several
     * sources; here mixing up front is both simpler and lower latency.
     *
     * `offsets` are semitones above the root, so a two-note interval and a
     * four-note seventh chord are the same shape of thing.
     */
    fun renderQuestion(voice: Voice, rootMidi: Int, offsets: List<Int>,
                       melodic: Boolean, descending: Boolean): FloatArray {
        var midis = listOf(rootMidi) + offsets.map { rootMidi + it }
        // More notes sharing one output need more headroom, or a four-note
        // chord clips where a two-note interval did not.
        val gain = (0.85f / Math.sqrt(midis.size.toDouble()).toFloat()) * 1.25f

        if (!melodic) {
            val rendered = midis.map { note(voice, it, 3.0) }
            val out = FloatArray(rendered.maxOf { it.size })
            for (r in rendered) for (i in r.indices) out[i] += r[i] * gain
            return out
        }

        if (descending) midis = midis.reversed()
        val step = (0.62 * SAMPLE_RATE).toInt()
        val rendered = midis.mapIndexed { k, m -> note(voice, m, if (k == midis.size - 1) 2.6 else 2.2) }
        val out = FloatArray(rendered.indices.maxOf { it * step + rendered[it].size })
        for ((k, r) in rendered.withIndex()) {
            val at = k * step
            for (i in r.indices) out[at + i] += r[i] * gain
        }
        return out
    }

    /** Short non-verbal confirmation: faster and less grating than a spoken one. */
    fun chime(): FloatArray {
        val n = (SAMPLE_RATE * 0.34).toInt()
        val out = FloatArray(n)
        val freqs = doubleArrayOf(880.0, 1318.5)
        for ((k, f) in freqs.withIndex()) {
            val start = (SAMPLE_RATE * 0.09 * k).toInt()
            val w = 2 * Math.PI * f / SAMPLE_RATE
            for (i in start until n) {
                val t = (i - start).toDouble() / SAMPLE_RATE
                if (t > 0.22) break
                val env = 0.18 * exp(-t * 18)
                out[i] += (sin(w * (i - start)) * env).toFloat()
            }
        }
        return out
    }
}
