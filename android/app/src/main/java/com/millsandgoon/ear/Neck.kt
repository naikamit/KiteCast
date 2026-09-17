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

    /**
     * The highest note a chord can reach and still be a shape a hand could
     * hold. Above this the upper notes of a four-note voicing all want the
     * top string, which is a real fact about having six of them rather than
     * something a picture should pretend away. Notes sounded one after another
     * have no such limit — you would play them on one string quite happily.
     */
    const val CHORD_TOP = 71

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
     * Ascending notes take ascending strings, and of all the ways to do that
     * the one kept is the lowest on the neck, then the most compact — which
     * is first position wherever first position will have it, and that is
     * both the easiest to read and the one a learner already knows.
     *
     * Taking each note greedily instead does not work: the lowest fret for
     * the root is usually on a high string, which then leaves the notes above
     * it nowhere to go. The whole assignment has to be chosen at once, and
     * with six strings and at most four notes there is little to choose from.
     */
    fun shapeOf(midis: List<Int>): List<Spot> {
        val notes = midis.sorted()
        if (notes.isEmpty()) return emptyList()

        var best: List<Spot>? = null
        var bestScore = Int.MAX_VALUE
        val acc = ArrayList<Spot>(notes.size)

        fun walk(i: Int, from: Int) {
            if (i == notes.size) {
                val frets = acc.map { it.fret }
                val score = frets.max() * 100 + (frets.max() - frets.min())
                if (score < bestScore) { bestScore = score; best = ArrayList(acc) }
                return
            }
            for (s in from until OPEN.size) {
                val f = notes[i] - OPEN[s]
                if (f in 0..FRETS) {
                    acc.add(Spot(s, f))
                    walk(i + 1, s + 1)
                    acc.removeAt(acc.lastIndex)
                }
            }
        }
        walk(0, 0)
        best?.let { return it }

        // Too high to voice: there are not enough strings above the root left
        // for the notes above it. Each note goes where it sits lowest, two of
        // them on one string, and the picture says so rather than inventing a
        // shape that does not exist.
        return notes.mapNotNull { m -> places(m).minByOrNull { it.fret } }
    }
}
