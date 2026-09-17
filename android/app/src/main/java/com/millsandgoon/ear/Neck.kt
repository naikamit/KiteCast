package com.millsandgoon.ear

/**
 * Where notes live on a guitar neck.
 *
 * Kept apart from the view that draws it so the arithmetic can be tested
 * without a device: a circle drawn on the wrong fret is a lie told confidently,
 * and it is exactly the kind of mistake nobody notices until they pick up a
 * guitar and the app has been teaching them the wrong shape for a fortnight.
 */
object Neck {

    /** Standard tuning, low to high. Index 0 is the fat E and is drawn at the
     *  bottom, the way the neck looks when you glance down at it. */
    val OPEN = intArrayOf(40, 45, 50, 55, 59, 64)

    /** Twelve is where the octave lands and the neck repeats, and it is as
     *  many divisions as a phone can offer a thumb. */
    const val FRETS = 12

    val LOWEST = OPEN.first()
    val HIGHEST = OPEN.last() + FRETS

    data class Spot(val string: Int, val fret: Int)

    fun midiAt(string: Int, fret: Int): Int = OPEN[string] + fret

    /** Every place a note can be played, low string first. */
    fun places(midi: Int): List<Spot> =
        OPEN.indices.mapNotNull { s ->
            val f = midi - OPEN[s]
            if (f in 0..FRETS) Spot(s, f) else null
        }

    /**
     * Where a guitarist would actually put these notes.
     *
     * Ascending notes go on ascending strings, each at the lowest fret still
     * open to it once the notes below have taken their strings. That is what
     * turns a set of pitches into a shape you could reach, rather than a row
     * of marks strung out along one string.
     */
    fun shapeOf(midis: List<Int>): List<Spot> {
        val out = ArrayList<Spot>(midis.size)
        var from = 0
        for (m in midis.sorted()) {
            val here = places(m)
            val pick = here.filter { it.string >= from }.minByOrNull { it.fret }
                ?: here.minByOrNull { it.fret }
                ?: continue                      // off the end of the neck
            out.add(pick)
            from = pick.string + 1
        }
        return out
    }
}
