package com.millsandgoon.ear

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The decisions that live in the layout and in the service's wording.
 *
 * Reading source as text is a blunt instrument, but these are choices a
 * compiler cannot hold: an order, a colour, a word that should not be on the
 * screen. Each one was asked for after using the app on a phone, and each is
 * the kind of thing a later tidy-up quietly undoes.
 */
class PageTest {

    private fun src(name: String) = read("src/main/java/com/millsandgoon/ear/$name")
    private fun res(name: String) = read("src/main/res/$name")

    private fun read(relative: String): String = file(relative).readText()

    /** Gradle's working directory for unit tests is the module, but that is a
     *  convention rather than a promise, so try the few places it could be. */
    private fun file(relative: String): File {
        for (root in listOf("", "app/", "../", "../../", "android/app/")) {
            val f = File(root + relative)
            if (f.exists()) return f
        }
        throw AssertionError("cannot find $relative from ${File("").absolutePath}")
    }

    private val layout by lazy { res("layout/activity_main.xml") }
    private val service by lazy { src("EarService.kt") }

    /** The attributes of one view, from its id to the end of its tag. */
    private fun view(id: String): String {
        val from = layout.indexOf("@+id/$id\"")
        assertTrue("no view called $id", from >= 0)
        return layout.substring(from, layout.indexOf("/>", from))
    }

    // ---- the page ----

    @Test fun `the page is ordered for a thumb`() {
        val order = listOf("sayable", "tallyRight", "tallyWrong", "reset", "transport")
            .map { layout.indexOf("@+id/$it\"") }
        assertEquals("the main page is out of order", order.sorted(), order)
        assertEquals("both buttons must stay thumb-sized", 2,
            Regex("android:layout_height=\"74dp\"").findAll(layout).count())
    }

    @Test fun `there is nothing to press mid-question`() {
        assertFalse("@+id/again" in layout)
        assertFalse("@+id/skip" in layout)
        assertFalse("fun repeatQuestion()" in service)
        assertFalse("fun skipQuestion()" in service)
    }

    /** Driving, you read a shape before a word — and no word fits a button
     *  that means Start, Pause and Resume by turns. */
    @Test fun `both buttons are icons`() {
        for (id in listOf("reset", "transport")) {
            assertTrue("$id lost its icon", "android:src=" in view(id))
            assertFalse("$id is showing text", "android:text=" in view(id))
        }
        val activity = src("MainActivity.kt")
        assertTrue("R.drawable.ic_play" in activity && "R.drawable.ic_pause" in activity)
        for (icon in listOf("ic_play", "ic_pause", "ic_reset")) {
            assertTrue("$icon is missing", file("src/main/res/drawable/$icon.xml").exists())
        }
    }

    /** Same control, different consequence, so the shape matches and only the
     *  fill differs. */
    @Test fun `start and reset are the same shape`() {
        val radius = Regex("android:radius=\"(\\d+)dp\"")
        assertEquals(radius.find(res("drawable/bg_transport.xml"))!!.groupValues[1],
                     radius.find(res("drawable/bg_reset.xml"))!!.groupValues[1])
    }

    @Test fun `every answer is also a key`() {
        assertTrue("SPEAK / TAP THE ANSWER" in layout)
        assertTrue("com.millsandgoon.ear.FlowLayout" in layout)
        assertTrue(file("src/main/res/layout/item_answer.xml").exists())
        val activity = src("MainActivity.kt")
        assertTrue("R.layout.item_answer" in activity)
        assertTrue("service?.tapAnswer(id)" in activity)
        assertTrue("fun tapAnswer(id: String)" in service)
    }

    // ---- the score ----

    @Test fun `the score belongs to the exercise`() {
        assertFalse("private var seen = 0" in service)
        assertFalse("private var correct = 0" in service)
        val reset = service.substring(service.indexOf("fun resetScore()"))
            .substringBefore("\n    }")
        assertTrue("perLesson.remove(lesson.n)" in reset)
        assertFalse("reset must not empty the other exercises", "perLesson.clear()" in reset)
    }

    @Test fun `the score is the same two marks everywhere`() {
        // The source holds the escape, not the glyph — a raw string here
        // compares like with like.
        assertTrue("the tick is gone", """\u2713""" in service)
        assertTrue("the cross is gone", """\u2717""" in service)
        val item = res("layout/item_lesson.xml")
        assertTrue("scoreRight" in item && "scoreWrong" in item)
        assertTrue("android:textColor=\"@color/ok\"" in view("tallyRight"))
        assertTrue("android:textColor=\"@color/bad\"" in view("tallyWrong"))
    }

    // ---- what is never said ----

    @Test fun `pausing is shown by the button alone`() {
        assertFalse("status(\"paused\"" in service)
        assertFalse("\"Paused\"" in service)
    }

    @Test fun `nothing narrates the microphone`() {
        assertFalse("status(\"listening\"" in service)
        assertFalse("@+id/heard" in layout)
    }

    @Test fun `the instrument and the direction are never shown`() {
        assertFalse("fun onSource" in service)
        assertFalse("@+id/source" in layout)
    }

    @Test fun `a wrong answer is not told off`() {
        assertFalse("Not quite" in service)
    }

    @Test fun `silence repeats rather than scoring a miss`() {
        assertTrue("const val MAX_REPEATS = 5" in service)
        assertTrue("if (q.replays >= MAX_REPEATS)" in service)
    }

    @Test fun `the drill waits for the recogniser`() {
        assertTrue("startPending" in service)
        assertTrue("if (!modelReady)" in service)
    }

    // ---- the phone ----

    @Test fun `the phone holds the microphone with the screen off`() {
        val manifest = read("src/main/AndroidManifest.xml")
        assertTrue("FOREGROUND_SERVICE_MICROPHONE" in manifest)
        assertTrue("android:foregroundServiceType=\"microphone|mediaPlayback\"" in manifest)
    }

    /** Nothing leaves the phone, and the install prompt should be able to say
     *  so. The record is on disk here, not on anybody's server. */
    @Test fun `the app asks for no network at all`() {
        assertFalse("android.permission.INTERNET" in read("src/main/AndroidManifest.xml"))
        val progress = src("Progress.kt")
        assertFalse("HttpURLConnection" in progress)
        assertFalse("https://" in progress)
    }

    /**
     * A Bluetooth headset is two devices pretending to be one: A2DP carries
     * good audio and has no microphone, the microphone lives on the call
     * channel, and the two cannot both be up. Taking half of each is what
     * sounded broken — earbuds playing, phone in a pocket listening — so the
     * headset takes the whole session or none of it.
     */
    @Test fun `a headset takes the tones and the microphone together`() {
        val routing = src("Routing.kt")
        assertTrue("setCommunicationDevice" in routing)     // Android 12 and up
        assertTrue("startBluetoothSco" in routing)          // and everything older
        assertTrue("MODE_IN_COMMUNICATION" in routing)
        assertTrue("the route must be dropped again",
            "clearCommunicationDevice" in routing && "stopBluetoothSco" in routing)

        // Playback and prompts follow the microphone, or they are played to a
        // profile Bluetooth has suspended.
        assertTrue("USAGE_VOICE_COMMUNICATION" in src("Player.kt"))
        assertTrue("USAGE_VOICE_COMMUNICATION" in src("Speaker.kt"))
        assertTrue("viaHeadset" in service)
        assertTrue("speaker.setVoiceRoute" in service)
    }

    /** The microphone is chosen when capture opens, so a recogniser that
     *  started on the phone stays there however the earbuds are routed. */
    @Test fun `the recogniser pins its microphone and follows a change`() {
        val listener = src("Listener.kt")
        assertTrue("setPreferredDevice" in listener)
        assertTrue("VOICE_COMMUNICATION" in listener)
        assertTrue("fun restart()" in listener)
        assertTrue("listener.restart()" in service)
        assertTrue("MODIFY_AUDIO_SETTINGS" in read("src/main/AndroidManifest.xml"))
    }

    /** Direction is gone from the question, not merely defaulted: a flag that
     *  is always false is a flag someone flips back by accident. */
    @Test fun `melodic questions ascend and nothing chooses otherwise`() {
        assertFalse("descending" in service)
        assertFalse("descending" in src("Synth.kt").substringAfter("fun renderQuestion"))
        assertFalse("Random.nextBoolean" in service)
    }

    /**
     * The app spent a day at the top of the battery screen, flagged for high
     * CPU. Three reasons, all the same mistake: the recogniser kept decoding
     * when nobody wanted an answer — through every spoken prompt, through the
     * pause button, and from the moment the model unpacked whether or not
     * anyone had pressed play. Decoding a 16 kHz stream is not free, and a
     * service does it until the phone is flat.
     */
    @Test fun `a drill nobody is answering costs nothing`() {
        val listener = src("Listener.kt")

        // Quiet means the decoder stops, not that its results are binned.
        assertTrue("decoding must stop while quiet", "if (paused) continue" in listener)
        assertTrue("skipped audio leaves the recogniser mid-word",
            "recognizer.reset()" in listener)

        // A read that keeps failing must not spin at processor speed.
        assertTrue("a failing read must end the loop", "if (n < 0)" in listener)

        // Pause hands the microphone back rather than holding it open.
        assertTrue("fun closeEars()" in service)
        val pause = service.substring(service.indexOf("fun pause()")).substringBefore("\n    }")
        assertTrue("pause must release the microphone", "closeEars()" in pause)

        // And nothing starts listening merely because a model finished unpacking.
        val ready = service.substring(service.indexOf("onReady = {")).substringBefore("},")
        assertFalse("unpacking a model is not a reason to listen",
            "listener.start()" in ready)
    }

    /** The neck sits between the score and the transport, is always there,
     *  and answers a thumb whether or not a drill is running. */
    @Test fun `the neck is always on the page and always playable`() {
        val order = listOf("tallyRight", "reset", "fretboard", "transport")
            .map { layout.indexOf("@+id/$it\"") }
        assertTrue("the neck must sit above play/pause", order.sorted() == order)
        assertTrue("@+id/fretboard" in layout)
        assertFalse("the neck is not something to hide",
            "android:visibility=\"gone\"" in view("fretboard"))

        val activity = src("MainActivity.kt")
        assertTrue("ui.fretboard.onPluck" in activity)
        assertTrue("fun pluck(midi: Int)" in service)

        // Marked only after a miss, and cleared by the next question.
        assertTrue("onShape(Neck.shapeOf" in service)
        assertTrue("observer?.onShape(emptyList())" in service)
    }

    /** These numbers were measured, not chosen. A slip would quietly detune
     *  the whole app. */
    @Test fun `the synth carries the measured constants`() {
        val synth = src("Synth.kt")
        for (c in listOf("0.00015", "-1.25", "0.82", "2.4 /", "6.9078",
                         "0.34, 1.9, 0.11", "0.56, 3.1, 0.40")) {
            assertTrue("$c is gone from the synth", c in synth)
        }
    }

    @Test fun `three voices and all of them struck or plucked`() {
        val synth = src("Synth.kt")
        assertFalse("SAX" in synth)
        for (v in listOf("PIANO(", "NYLON(", "STEEL(")) assertTrue(v in synth)
    }

    @Test fun `the speech model ships with its uuid marker`() {
        // Vosk reads <model>/uuid to decide whether its copy is stale. The
        // plain model zip has no such file, and without it the unpack fails
        // and the app silently has no recogniser — which is what the first
        // device build did.
        assertTrue("model-en-us/uuid" in read(".github/workflows/android.yml"))
    }
}
