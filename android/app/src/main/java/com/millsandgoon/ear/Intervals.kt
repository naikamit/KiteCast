package com.millsandgoon.ear

/**
 * Intervals, chords, the lesson ladder, and speech-to-answer matching.
 *
 * A transliteration of kitecast/ear.py, which is canonical; tests/test_ear.py
 * parses this file and fails if the two disagree.
 */

data class Interval(val semitones: Int, val display: String, val size: Int, val quality: String)

val INTERVALS: Map<String, Interval> = linkedMapOf(
    "m2" to Interval(1, "minor second", 2, "minor"),
    "M2" to Interval(2, "major second", 2, "major"),
    "m3" to Interval(3, "minor third", 3, "minor"),
    "M3" to Interval(4, "major third", 3, "major"),
    "P4" to Interval(5, "perfect fourth", 4, "perfect"),
    "TT" to Interval(6, "tritone", 0, "tritone"),
    "P5" to Interval(7, "perfect fifth", 5, "perfect"),
    "m6" to Interval(8, "minor sixth", 6, "minor"),
    "M6" to Interval(9, "major sixth", 6, "major"),
    "m7" to Interval(10, "minor seventh", 7, "minor"),
    "M7" to Interval(11, "major seventh", 7, "major"),
    "P8" to Interval(12, "octave", 8, "perfect")
)

val ALL: List<String> = INTERVALS.keys.toList()

/**
 * Chords are the same exercise with more notes: offsets above the root, played
 * as a block. Naming a chord quality is the same act of recall as naming an
 * interval, so they share the drill, the scoring and the mastery store.
 */
data class Chord(val offsets: List<Int>, val display: String)

val CHORDS: Map<String, Chord> = linkedMapOf(
    "maj" to Chord(listOf(4, 7), "major"),
    "min" to Chord(listOf(3, 7), "minor"),
    "dim" to Chord(listOf(3, 6), "diminished"),
    "aug" to Chord(listOf(4, 8), "augmented"),
    "maj7" to Chord(listOf(4, 7, 11), "major seventh"),
    "dom7" to Chord(listOf(4, 7, 10), "dominant seventh"),
    "min7" to Chord(listOf(3, 7, 10), "minor seventh"),
    "m7b5" to Chord(listOf(3, 6, 10), "half diminished"),
    "dim7" to Chord(listOf(3, 6, 9), "diminished seventh")
)

val ALL_CHORDS: List<String> = CHORDS.keys.toList()

enum class Mode { HARMONIC, MELODIC }

/** The top level of the menu: what kind of thing you are naming. */
enum class Group(val label: String) { HARMONIC("Harmonic"), MELODIC("Melodic"), CHORDS("Chords") }

enum class Kind { INTERVAL, CHORD }

data class Lesson(val n: Int, val title: String, val mode: Mode,
                  val set: List<String>, val group: Group, val kind: Kind)

val LESSONS: List<Lesson> = listOf(
    Lesson(1, "Seconds", Mode.HARMONIC, listOf("m2", "M2"), Group.HARMONIC, Kind.INTERVAL),
    Lesson(2, "Thirds", Mode.HARMONIC, listOf("m3", "M3"), Group.HARMONIC, Kind.INTERVAL),
    Lesson(3, "Fourths and Fifths", Mode.HARMONIC, listOf("P4", "TT", "P5"), Group.HARMONIC, Kind.INTERVAL),
    Lesson(4, "Sixths", Mode.HARMONIC, listOf("m6", "M6"), Group.HARMONIC, Kind.INTERVAL),
    Lesson(5, "Sevenths", Mode.HARMONIC, listOf("m7", "M7"), Group.HARMONIC, Kind.INTERVAL),
    Lesson(6, "Tritones and Major Sevenths", Mode.HARMONIC, listOf("TT", "M7"), Group.HARMONIC, Kind.INTERVAL),
    Lesson(7, "All Intervals", Mode.HARMONIC, ALL, Group.HARMONIC, Kind.INTERVAL),

    Lesson(8, "Seconds", Mode.MELODIC, listOf("m2", "M2"), Group.MELODIC, Kind.INTERVAL),
    Lesson(9, "Thirds", Mode.MELODIC, listOf("m3", "M3"), Group.MELODIC, Kind.INTERVAL),
    Lesson(10, "Fourths and Fifths", Mode.MELODIC, listOf("P4", "TT", "P5"), Group.MELODIC, Kind.INTERVAL),
    Lesson(11, "Sixths", Mode.MELODIC, listOf("m6", "M6"), Group.MELODIC, Kind.INTERVAL),
    Lesson(12, "Sevenths", Mode.MELODIC, listOf("m7", "M7"), Group.MELODIC, Kind.INTERVAL),
    Lesson(13, "All Intervals", Mode.MELODIC, ALL, Group.MELODIC, Kind.INTERVAL),

    Lesson(14, "Major and Minor Triads", Mode.HARMONIC, listOf("maj", "min"), Group.CHORDS, Kind.CHORD),
    Lesson(15, "All Four Triads", Mode.HARMONIC, listOf("maj", "min", "dim", "aug"), Group.CHORDS, Kind.CHORD),
    Lesson(16, "Sevenths: Three", Mode.HARMONIC, listOf("maj7", "dom7", "min7"), Group.CHORDS, Kind.CHORD),
    Lesson(17, "All Sevenths", Mode.HARMONIC, listOf("maj7", "dom7", "min7", "m7b5", "dim7"), Group.CHORDS, Kind.CHORD)
)

fun lessonOf(n: Int): Lesson? = LESSONS.firstOrNull { it.n == n }

fun lessonsIn(group: Group): List<Lesson> = LESSONS.filter { it.group == group }

/** The spoken name of an interval or a chord. */
fun displayOf(id: String): String = INTERVALS[id]?.display ?: CHORDS.getValue(id).display

/** Semitone offsets above the root. An interval is just a two-note chord. */
fun offsetsOf(id: String): List<Int> =
    INTERVALS[id]?.let { listOf(it.semitones) } ?: CHORDS.getValue(id).offsets

/** The word the app asks back when it cannot separate two answers. */
fun confirmWord(id: String): String = INTERVALS[id]?.quality ?: CHORDS.getValue(id).display

/**
 * Spoken forms. Bare size words ("third") are deliberately absent: they are
 * what triggers the yes/no question rather than a guess.
 */
val ALIASES: Map<String, List<String>> = mapOf(
    "m2" to listOf("minor second", "flat two", "flat second", "half step", "semitone"),
    "M2" to listOf("major second", "whole step", "whole tone"),
    "m3" to listOf("minor third", "flat three", "flat third"),
    "M3" to listOf("major third", "natural third"),
    "P4" to listOf("perfect fourth", "fourth", "perfect four"),
    "TT" to listOf("tritone", "tri tone", "augmented fourth", "diminished fifth", "flat five", "sharp four"),
    "P5" to listOf("perfect fifth", "fifth", "perfect five"),
    "m6" to listOf("minor sixth", "flat six", "flat sixth", "augmented fifth", "sharp five"),
    "M6" to listOf("major sixth", "natural sixth"),
    "m7" to listOf("minor seventh", "flat seven", "flat seventh", "dominant seventh"),
    "M7" to listOf("major seventh", "natural seventh"),
    "P8" to listOf("octave", "perfect octave"),

    "maj" to listOf("major", "major triad", "major chord"),
    "min" to listOf("minor", "minor triad", "minor chord"),
    "dim" to listOf("diminished", "diminished triad"),
    "aug" to listOf("augmented", "augmented triad"),
    "maj7" to listOf("major seventh", "major seven"),
    "dom7" to listOf("dominant seventh", "dominant", "dominant seven"),
    "min7" to listOf("minor seventh", "minor seven"),
    "m7b5" to listOf("half diminished", "half diminished seventh", "minor seven flat five"),
    "dim7" to listOf("diminished seventh", "fully diminished")
)

private val SIZE_WORDS = mapOf(
    "second" to 2, "seconds" to 2, "third" to 3, "thirds" to 3,
    "fourth" to 4, "fourths" to 4, "fifth" to 5, "fifths" to 5,
    "sixth" to 6, "sixths" to 6, "seventh" to 7, "sevenths" to 7,
    "octave" to 8, "eighth" to 8
)

private val FILLER = Regex(
    "\\b(its|it is|thats|that is|i think|maybe|sounds like|sounds|like|a|an|the|um|uh|er|ah|hmm|" +
    "up|down|upward|downward|ascending|descending|rising|falling|higher|lower|going|chord|triad)\\b"
)

fun normalise(text: String): String =
    (" " + text.lowercase() + " ")
        .replace(Regex("[^a-z0-9 ]+"), " ")
        .replace(Regex("\\bmin\\b"), "minor")
        .replace(Regex("\\bmaj\\b"), "major")
        .replace(Regex("\\bperf\\b"), "perfect")
        .replace(FILLER, " ")
        .replace(Regex("\\s+"), " ")
        .trim()

private fun wordMatch(haystack: String, needle: String): Boolean =
    Regex("(^| )" + Regex.escape(needle) + "($| )").containsMatchIn(haystack)

/** Longest alias wins, so "half diminished" is never shortened to "diminished". */
private fun findItem(norm: String, set: List<String>): String? {
    var best: String? = null
    var bestLen = 0
    for (id in set) for (alias in ALIASES.getValue(id)) {
        if (alias.length > bestLen && wordMatch(norm, alias)) { best = id; bestLen = alias.length }
    }
    return best
}

private fun findSize(norm: String): Int? =
    SIZE_WORDS.entries.firstOrNull { wordMatch(norm, it.key) }?.value

private val YES = Regex("(^| )(yes|yeah|yep|yup|correct|right|affirmative)($| )")
private val NO = Regex("(^| )(no|nope|nah|negative|wrong)($| )")

/**
 * Your voice answers the question and does nothing else. Controls are buttons.
 *
 * The recogniser never has to tell an answer from an instruction, an
 * instruction can never be triggered by a stray word, and the grammar stays as
 * small as the thing being taught.
 */
sealed class Heard {
    data class Answer(val id: String) : Heard()
    data class Ambiguous(val options: List<String>) : Heard()
}

/**
 * Null means "not recognised", which is treated as silence: humming to work
 * out what you are hearing produces no grammar match and is ignored rather
 * than scored.
 */
fun interpret(alternatives: List<String>, lesson: Lesson): Heard? {
    val norms = alternatives.map { normalise(it) }.filter { it.isNotEmpty() }
    if (norms.isEmpty()) return null

    val hits = norms.mapNotNull { findItem(it, lesson.set) }.distinct()

    // The recogniser could not decide between two answers in this lesson.
    // Rather than gamble on the vowel that separates major from minor, ask.
    if (hits.size > 1) return Heard.Ambiguous(hits.take(2))
    if (hits.size == 1) return Heard.Answer(hits[0])

    if (lesson.kind == Kind.INTERVAL) {
        for (n in norms) {
            val size = findSize(n) ?: continue
            val options = lesson.set.filter { INTERVALS.getValue(it).size == size }
            if (options.size == 1) return Heard.Answer(options[0])
            if (options.size > 1) return Heard.Ambiguous(options)
        }
    }
    return null
}

fun interpretYesNo(alternatives: List<String>): Boolean? {
    for (raw in alternatives) {
        val n = " " + normalise(raw) + " "
        if (YES.containsMatchIn(n)) return true
        if (NO.containsMatchIn(n)) return false
    }
    return null
}

/**
 * The grammar handed to Vosk: the only things you could sensibly say. It is
 * choosing between these, not between them and the whole language. "[unk]"
 * lets it report out-of-grammar audio — humming — which is then ignored.
 */
fun voskGrammar(): String {
    val phrases = LinkedHashSet<String>()
    ALIASES.values.forEach { phrases.addAll(it) }
    phrases.addAll(SIZE_WORDS.keys)
    phrases.addAll(listOf("yes", "no", "yeah", "nope"))
    return phrases.joinToString(prefix = "[", postfix = ", \"[unk]\"]") { "\"$it\"" }
}
