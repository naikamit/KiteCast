package com.millsandgoon.ear

/**
 * Intervals, the lesson ladder, and speech-to-answer matching.
 *
 * A transliteration of ear/intervals.js. The canonical copy is kitecast/ear.py;
 * tests/test_ear.py parses this file and fails if the three ever disagree.
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

enum class Mode { HARMONIC, MELODIC }

data class Lesson(val n: Int, val title: String, val mode: Mode, val set: List<String>)

val LESSONS: List<Lesson> = listOf(
    Lesson(1, "Harmonic: Seconds", Mode.HARMONIC, listOf("m2", "M2")),
    Lesson(2, "Melodic: Seconds", Mode.MELODIC, listOf("m2", "M2")),
    Lesson(3, "Harmonic: Thirds", Mode.HARMONIC, listOf("m3", "M3")),
    Lesson(4, "Melodic: Thirds", Mode.MELODIC, listOf("m3", "M3")),
    Lesson(5, "Harmonic: Fourths and Fifths", Mode.HARMONIC, listOf("P4", "TT", "P5")),
    Lesson(6, "Melodic: Fourths and Fifths", Mode.MELODIC, listOf("P4", "TT", "P5")),
    Lesson(7, "Harmonic: Sixths", Mode.HARMONIC, listOf("m6", "M6")),
    Lesson(8, "Melodic: Sixths", Mode.MELODIC, listOf("m6", "M6")),
    Lesson(9, "Harmonic: Sevenths", Mode.HARMONIC, listOf("m7", "M7")),
    Lesson(10, "Melodic: Sevenths", Mode.MELODIC, listOf("m7", "M7")),
    Lesson(11, "Harmonic: Tritones and Major Sevenths", Mode.HARMONIC, listOf("TT", "M7")),
    Lesson(12, "All Intervals: Harmonic", Mode.HARMONIC, ALL),
    Lesson(13, "All Intervals: Melodic", Mode.MELODIC, ALL)
)

fun lessonOf(n: Int): Lesson? = LESSONS.firstOrNull { it.n == n }

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
    "P8" to listOf("octave", "perfect octave")
)

private val SIZE_WORDS = mapOf(
    "second" to 2, "seconds" to 2, "third" to 3, "thirds" to 3,
    "fourth" to 4, "fourths" to 4, "fifth" to 5, "fifths" to 5,
    "sixth" to 6, "sixths" to 6, "seventh" to 7, "sevenths" to 7,
    "octave" to 8, "eighth" to 8
)

private val FILLER = Regex(
    "\\b(its|it is|thats|that is|i think|maybe|sounds like|sounds|like|a|an|the|um|uh|er|ah|hmm|" +
    "up|down|upward|downward|ascending|descending|rising|falling|higher|lower|going)\\b"
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

/** Longest alias wins, so "minor seventh" is never shortened to "seventh". */
private fun findInterval(norm: String, set: List<String>): String? {
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
 * That is a narrower job than the web app gave it, and a better one: the
 * recogniser no longer has to tell an answer from an instruction, an
 * instruction can no longer be triggered by a stray word, and the grammar
 * shrinks to the twelve things being taught.
 */
sealed class Heard {
    data class Answer(val id: String) : Heard()
    data class Ambiguous(val size: Int, val options: List<String>) : Heard()
}

/**
 * The core of the voice interaction. Null means "not recognised", which is
 * treated as silence: humming to work out what you are hearing produces no
 * grammar match and is simply ignored rather than scored or re-prompted.
 */
fun interpret(alternatives: List<String>, lesson: Lesson): Heard? {
    val norms = alternatives.map { normalise(it) }.filter { it.isNotEmpty() }
    if (norms.isEmpty()) return null

    val hits = norms.mapNotNull { findInterval(it, lesson.set) }.distinct()
    if (hits.size > 1 && hits.map { INTERVALS.getValue(it).size }.distinct().size == 1) {
        val size = INTERVALS.getValue(hits[0]).size
        return Heard.Ambiguous(size, lesson.set.filter { INTERVALS.getValue(it).size == size })
    }
    if (hits.size == 1) return Heard.Answer(hits[0])

    for (n in norms) {
        val size = findSize(n) ?: continue
        val options = lesson.set.filter { INTERVALS.getValue(it).size == size }
        if (options.size == 1) return Heard.Answer(options[0])
        if (options.size > 1) return Heard.Ambiguous(size, options)
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
 * The grammar handed to Vosk. Constraining recognition to the only things you
 * could sensibly say is the point of going native: the recogniser is choosing
 * between these, not between them and the whole language. "[unk]" lets it
 * report out-of-grammar audio — humming — which we then ignore.
 */
fun voskGrammar(): String {
    val phrases = LinkedHashSet<String>()
    ALIASES.values.forEach { phrases.addAll(it) }
    phrases.addAll(SIZE_WORDS.keys)
    // yes/no only, for the one question the app ever asks back
    phrases.addAll(listOf("yes", "no", "yeah", "nope"))
    return phrases.joinToString(prefix = "[", postfix = ", \"[unk]\"]") { "\"$it\"" }
}
