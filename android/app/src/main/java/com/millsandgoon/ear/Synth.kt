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
        PIANO("piano"), NYLON("classical guitar"), STEEL("acoustic guitar"), SAX("alto sax")
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

    // ---- alto sax --------------------------------------------------------

    /**
     * A reed rather than a struck or plucked string: it sustains instead of
     * decaying, so a harmonic interval holds for as long as it is played.
     *
     * Additive again, but shaped by formants rather than by a falling
     * amplitude curve — the sax's character is that fixed resonance around
     * 700 Hz and 1.4 kHz, not the relative strength of its partials. Phase is
     * accumulated per sample rather than rotated, because the vibrato moves
     * the frequency and a fixed rotation cannot follow it.
     */
    private fun renderSax(freq: Double, seconds: Double): FloatArray {
        val n = Math.ceil(seconds * SAMPLE_RATE).toInt()
        val out = DoubleArray(n)
        val nyquist = SAMPLE_RATE * 0.45

        // body resonances, plus a floor so the upper partials do not vanish
        fun formant(f: Double): Double =
            exp(-((f - 700.0) / 420.0).pow(2)) * 1.0 +
            exp(-((f - 1400.0) / 650.0).pow(2)) * 0.55 +
            exp(-((f - 2600.0) / 900.0).pow(2)) * 0.22 + 0.16

        val partials = ArrayList<Pair<Double, Double>>()   // harmonic number, amplitude
        for (p in 1..18) {
            val f = p * freq
            if (f > nyquist) break
            partials.add(Pair(p.toDouble(), p.toDouble().pow(-0.75) * formant(f)))
        }
        var sum = 0.0
        for ((_, a) in partials) sum += a
        val scale = 0.9 / sum

        val phases = DoubleArray(partials.size)
        val vibHz = 5.2
        val twoPi = 2 * Math.PI

        for (i in 0 until n) {
            val t = i.toDouble() / SAMPLE_RATE
            // vibrato eases in; a reed player does not start with it
            val depth = 0.004 * ((t - 0.25) / 0.5).coerceIn(0.0, 1.0)
            val bend = 1.0 + depth * sin(twoPi * vibHz * t)
            var v = 0.0
            for (k in partials.indices) {
                val (h, a) = partials[k]
                phases[k] += twoPi * h * freq * bend / SAMPLE_RATE
                v += a * sin(phases[k])
            }
            out[i] = v * scale * saxEnvelope(t, seconds)
        }

        // breath at the onset, which is most of what makes a reed read as one
        var lp = 0.0
        val bn = (SAMPLE_RATE * 0.09).toInt()
        for (i in 0 until bn) {
            lp += 0.35 * ((Random.nextDouble() * 2 - 1) - lp)
            out[i] += lp * 0.09 * (1 - i.toDouble() / bn)
        }

        shape(out, 0.045)
        return out.map { it.toFloat() }.toFloatArray()
    }

    /** Attack, a slight settle, then hold — not the decay of a struck string. */
    private fun saxEnvelope(t: Double, total: Double): Double {
        val release = 0.18
        val a = if (t < 0.055) t / 0.055 else 1.0
        val settle = if (t < 0.22) 1.0 else 0.88 + 0.12 * exp(-(t - 0.22) * 6)
        val r = if (t > total - release) ((total - t) / release).coerceIn(0.0, 1.0) else 1.0
        return a * settle * r
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
            Voice.SAX -> renderSax(f, seconds)
        }
        var peak = 1e-9f
        for (v in raw) peak = max(peak, abs(v))
        val norm = 0.55f / peak
        for (i in raw.indices) raw[i] *= norm
        return raw
    }

    /**
     * One whole question as a single buffer. The web version scheduled two
     * sources; here mixing up front is both simpler and lower latency.
     */
    fun renderQuestion(voice: Voice, lowMidi: Int, semitones: Int,
                       melodic: Boolean, descending: Boolean): FloatArray {
        val high = lowMidi + semitones
        if (!melodic) {
            val a = note(voice, lowMidi, 3.0)
            val b = note(voice, high, 3.0)
            val out = FloatArray(max(a.size, b.size))
            for (i in a.indices) out[i] += a[i] * 0.85f
            for (i in b.indices) out[i] += b[i] * 0.85f
            return out
        }
        val gap = (0.62 * SAMPLE_RATE).toInt()
        val first = note(voice, if (descending) high else lowMidi, 2.2)
        val second = note(voice, if (descending) lowMidi else high, 2.6)
        val out = FloatArray(max(first.size, gap + second.size))
        for (i in first.indices) out[i] += first[i] * 0.85f
        for (i in second.indices) out[gap + i] += second[i] * 0.85f
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
