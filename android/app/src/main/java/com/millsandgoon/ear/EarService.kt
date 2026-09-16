package com.millsandgoon.ear

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Binder
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.random.Random

/**
 * The drill, running in a foreground service so it survives the screen going
 * off. That is the whole reason this app exists rather than the web one: a
 * service typed `microphone` keeps the microphone, which a browser cannot.
 */
class EarService : Service() {

    interface Observer {
        fun onStatus(text: String, tone: Int)
        fun onTally(right: String, wrong: String)
        fun onTransport(playing: Boolean)
        fun onLog(line: String)
        fun onLesson(lesson: Lesson)
        fun onScores()
    }

    inner class LocalBinder : Binder() { val service: EarService get() = this@EarService }

    private val binder = LocalBinder()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    var observer: Observer? = null
        set(value) { field = value; value?.let { replay(it) } }

    // ---- state ----
    var lesson: Lesson = LESSONS[0]; private set
    var running = false; private set
    var paused = false; private set
    private var listenMode = false
    private var phase = Phase.IDLE
    private var current: Question? = null
    private var pending: List<String>? = null
    private var lastRoot = -1
    private var loop: Job? = null
    private var silence: Job? = null
    private var listenSince = 0L
    private var speaking = false

    private val session = HashMap<String, Pair<Int, Int>>()   // seen, correct

    /**
     * The score is per exercise, everywhere it appears. Fifths answered right
     * say nothing about your sevenths, so one running total across a sitting
     * was a number that meant nothing — and it read as a second, different
     * score next to the per-exercise ones in the menu.
     */
    val perLesson = HashMap<Int, Pair<Int, Int>>()            // right, wrong

    private val hits get() = (perLesson[lesson.n] ?: Pair(0, 0)).first
    private val misses get() = (perLesson[lesson.n] ?: Pair(0, 0)).second
    private val asked get() = hits + misses

    private val logLines = ArrayList<String>()
    private val startedAt = System.currentTimeMillis()

    private enum class Phase { IDLE, PLAYING, LISTENING, CONFIRMING, FEEDBACK }
    private data class Question(val id: String, val root: Int, val voice: Synth.Voice,
                                val descending: Boolean, var replays: Int = 0)

    // ---- components ----
    private lateinit var player: Player
    private lateinit var speaker: Speaker
    private lateinit var listener: Listener
    private lateinit var progress: Progress
    private lateinit var routing: Routing
    private var modelReady = false
    private var startPending = false

    override fun onCreate() {
        super.onCreate()
        player = Player()
        progress = Progress(this) { k, m -> log(k, m) }
        // A headset takes the whole session — tones, prompts and answers — or
        // none of it. Half of each is what sounded broken: the earbuds played
        // and the phone in your pocket listened.
        // The callback lands on the main thread; re-opening capture waits on a
        // worker, so it does not belong there.
        routing = Routing(this, { k, m -> log(k, m) }) { scope.launch { adoptRoute() } }
        speaker = Speaker(this) { log("say", "voice ready") }
        listener = Listener(this,
            onLog = { k, m -> log(k, m) },
            onPartial = { t -> onHeard(t, false) },
            onFinal = { t -> onHeard(t, true) },
            onReady = {
                modelReady = true
                listener.start()
                if (startPending) { startPending = false; beginDrill() }
            },
            onFailed = { m ->
                log("warn", m)
                // Without a recogniser the drill cannot be answered, so fall
                // back to naming the intervals rather than asking questions
                // into a void — which is what a silent failure looked like.
                status("No recogniser — listening only", TONE_BAD)
                if (startPending) { startPending = false; listenMode = true; listenLoop() }
            })
        listener.load()
        scope.launch { progress.load() }
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_PAUSE -> pause()
            ACTION_RESUME -> resume()
            ACTION_SKIP -> if (phase == Phase.LISTENING || phase == Phase.CONFIRMING) resolve(null)
            ACTION_STOP -> stopSession()
            else -> foreground()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        loop?.cancel(); silence?.cancel()
        routing.shutdown()
        player.stop(); speaker.release(); listener.release()
        scope.cancel()
        super.onDestroy()
    }

    // ---- transport -------------------------------------------------------

    fun toggle() {
        when {
            !running -> startSession()
            paused -> resume()
            else -> pause()
        }
    }

    fun startSession() {
        if (running) return
        foreground()
        running = true
        paused = false
        listenMode = false
        log("mic", "session started")
        pushTransport()

        // Unpacking the model takes a moment on first launch. Starting the
        // drill before it is ready means asking questions nothing can hear.
        if (!modelReady) {
            startPending = true
            status("getting the recogniser ready…", TONE_PLAIN)
            log("mic", "waiting for the speech model")
            return
        }
        beginDrill()
    }

    private fun beginDrill() {
        routing.engage()
        adoptRoute()
        listener.setPaused(false)
        ask()
    }

    /**
     * Follow the route wherever it went. The recogniser has to be picked up
     * again by hand: its microphone is chosen when capture opens, so one that
     * started on the phone stays on the phone however the earbuds are routed.
     */
    private fun adoptRoute() {
        val headset = routing.onHeadset
        player.viaHeadset = headset
        speaker.setVoiceRoute(headset)
        listener.preferred = routing.micDevice()
        if (listener.listening) listener.restart()
    }

    fun pause() {
        if (!running || paused) return
        paused = true
        phase = Phase.IDLE
        loop?.cancel(); silence?.cancel()
        player.stop(); speaker.stop()
        listener.setPaused(true)              // pause stops listening too
        log("mic", "paused — microphone off")
        status("", TONE_PLAIN)             // the play icon is the whole message
        pushTransport(); notifyBar()
    }

    fun resume() {
        if (!running || !paused) return
        paused = false
        listener.setPaused(false)
        log("mic", "resumed")
        pushTransport(); notifyBar()
        if (listenMode) listenLoop() else ask()
    }

    fun stopSession() {
        running = false; paused = false
        loop?.cancel(); silence?.cancel()
        player.stop(); speaker.stop()
        listener.setPaused(true)
        routing.release()
        log("mic", "session stopped")
        if (asked > 0) say("That's $hits of $asked. ${pct(hits, asked)} percent.") {}
        status("", TONE_PLAIN)
        pushTransport(); notifyBar()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    /**
     * Wipe this exercise's score and keep going. The questions never run out,
     * and the other exercises keep the scores you earned in them.
     */
    fun resetScore() {
        perLesson.remove(lesson.n)
        lesson.set.forEach { session.remove(it) }
        pushTally()
        observer?.onScores()
        log("mic", "score reset — ${lesson.title}")
    }

    /**
     * A tap is an answer like any other: same scoring, same feedback. Speech
     * is still the point, but a recogniser that mishears you twice running
     * should not be the only way past the question.
     */
    fun tapAnswer(id: String) {
        if (!running || paused) return
        if (phase != Phase.LISTENING && phase != Phase.CONFIRMING) return
        log("tap", displayOf(id))
        resolve(id)
    }

    fun setLesson(next: Lesson) {
        if (next.n == lesson.n) return
        lesson = next
        observer?.onLesson(next)
        pushTally()                        // each exercise carries its own score
        log("mic", "lesson ${next.n}")
        if (running && !paused) { player.stop(); loop?.cancel(); ask() }
    }

    // ---- the drill -------------------------------------------------------

    private fun ask() {
        if (!running || paused) return
        loop?.cancel()
        loop = scope.launch {
            val q = nextQuestion()
            current = q
            pending = null
            play(q)
            phase = Phase.LISTENING
            listenSince = System.currentTimeMillis()
            log("mic", "awaiting answer")
            armSilence()
        }
    }

    private fun listenLoop() {
        if (!running || paused) return
        loop?.cancel()
        loop = scope.launch {
            while (running && !paused && listenMode) {
                val q = nextQuestion()
                current = q
                val ms = play(q)
                delay(ms + 150L)
                val name = displayOf(q.id)
                status(name, TONE_PLAIN)
                awaitSpeech(name)
                delay(500)
            }
        }
    }

    /** Generated fresh every time: a new item, a new root, a new instrument. */
    private fun nextQuestion(): Question {
        val id = weightedPick()
        val span = offsetsOf(id).max()
        var root: Int
        do { root = 50 + Random.nextInt(84 - span - 50 + 1) } while (root == lastRoot)
        lastRoot = root
        return Question(id, root, Synth.Voice.values().random(),
            lesson.mode == Mode.MELODIC && Random.nextBoolean())
    }

    /** Weighted by everything ever drilled, not just this session. */
    private fun weightedPick(): String {
        val weights = lesson.set.map { id ->
            val life = progress.lifetime[id]
            val now = session[id]
            val s = (life?.first ?: 0) + (now?.first ?: 0)
            val c = (life?.second ?: 0) + (now?.second ?: 0)
            if (s < 3) 1.3 else 1.0 + 2.2 * (1.0 - c.toDouble() / s)
        }
        var r = Random.nextDouble() * weights.sum()
        for (i in lesson.set.indices) { r -= weights[i]; if (r <= 0) return lesson.set[i] }
        return lesson.set.last()
    }

    private suspend fun play(q: Question): Int = withContext(Dispatchers.Default) {
        phase = Phase.PLAYING
        status("", TONE_PLAIN)
        // Logged but never shown: on a melodic lesson "ascending" is the
        // answer's other half.
        log("play", q.voice.label + " · " +
            (if (lesson.mode == Mode.MELODIC) (if (q.descending) "descending" else "ascending") else "harmonic") +
            if (q.replays > 0) " · replay ${q.replays}" else "")
        val buf = Synth.renderQuestion(q.voice, q.root, offsetsOf(q.id),
            lesson.mode == Mode.MELODIC, q.descending)
        player.play(buf)
    }

    /**
     * Nobody spoke. Play it again rather than marking it wrong — silence
     * usually means you did not hear it, not that you do not know. After
     * enough of those the phone is probably in a pocket, so stop asking.
     */
    private fun armSilence() {
        silence?.cancel()
        silence = scope.launch {
            while (running && !paused) {
                delay(ANSWER_WINDOW)
                if (phase != Phase.LISTENING || paused) return@launch
                val q = current ?: return@launch
                if (q.replays >= MAX_REPEATS) {
                    log("mic", "no answer after $MAX_REPEATS repeats — pausing")
                    pause()
                    return@launch
                }
                q.replays++
                play(q)
                phase = Phase.LISTENING
            }
        }
    }

    // ---- hearing ---------------------------------------------------------

    private fun onHeard(text: String, isFinal: Boolean) {
        if (!running || paused || speaking) return
        if (phase == Phase.CONFIRMING) {
            if (!isFinal) return
            val yn = interpretYesNo(listOf(text))
            val options = pending
            if (yn != null && options != null) {
                resolve(if (yn) options[0] else options[1]); return
            }
            (interpret(listOf(text), lesson) as? Heard.Answer)?.let { resolve(it.id) }
            return
        }

        if (phase != Phase.LISTENING) return
        when (val heard = interpret(listOf(text), lesson)) {
            is Heard.Answer -> {
                log("hear", (if (isFinal) "final " else "interim ") + lag() + " \"$text\"")
                resolve(heard.id)
            }
            // Only a clean answer is taken early; a question back waits for the
            // recogniser to finish making up its mind.
            is Heard.Ambiguous -> if (isFinal) confirm(heard)
            null -> Unit
        }
    }

    private fun confirm(a: Heard.Ambiguous) {
        silence?.cancel()
        phase = Phase.CONFIRMING
        pending = a.options
        val word = confirmWord(a.options[0])
        status("was it $word?", TONE_PLAIN)
        say("Was it $word?") {
            scope.launch { delay(12000); if (phase == Phase.CONFIRMING) { phase = Phase.LISTENING; armSilence() } }
        }
    }

    private fun resolve(id: String?) {
        if (phase == Phase.FEEDBACK) return
        silence?.cancel()
        phase = Phase.FEEDBACK
        val q = current ?: return
        val truthName = displayOf(q.id)
        val right = id == q.id

        val tally = perLesson[lesson.n] ?: Pair(0, 0)
        perLesson[lesson.n] = if (right) Pair(tally.first + 1, tally.second)
                              else Pair(tally.first, tally.second + 1)
        observer?.onScores()

        val prior = session[q.id] ?: Pair(0, 0)
        session[q.id] = Pair(prior.first + 1, prior.second + if (right) 1 else 0)
        pushTally()
        log(if (right) "ok" else "bad",
            truthName + (if (id != null && !right) " — you said ${displayOf(id)}" else "") +
            "  " + lag())
        scope.launch { progress.record(q.id, right, right && q.replays == 0) }

        scope.launch {
            if (right) {
                status(truthName, TONE_OK)
                player.play(Synth.chime())
                delay(1150)
            } else {
                player.stop()
                status(truthName, TONE_BAD)
                // Just the answer. The colour already says you missed it, and
                // being told so twenty times in a row is wearing.
                awaitSpeech(truthName)
                delay(120)
                val ms = play(q)
                delay(ms + 550L)
            }
            if (running && !paused && !listenMode) ask()
        }
    }

    // ---- speech ----------------------------------------------------------

    /**
     * The microphone is shut for the whole utterance and a beat afterwards.
     * The web version learned this the hard way: its own prompts came back
     * through the speaker and it took them as commands.
     */
    private fun say(text: String, whenDone: () -> Unit) {
        speaking = true
        listener.setPaused(true)
        log("say", "\"$text\"")
        speaker.say(text) {
            scope.launch {
                delay(400)
                speaking = false
                if (!paused) listener.setPaused(false)
                whenDone()
            }
        }
    }

    private suspend fun awaitSpeech(text: String) {
        val done = Job()
        say(text) { done.complete() }
        done.join()
    }

    // ---- notification ----------------------------------------------------

    private fun foreground() {
        val mgr = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            mgr.createNotificationChannel(
                NotificationChannel(CHANNEL, "Practice", NotificationManager.IMPORTANCE_LOW)
                    .also { it.setShowBadge(false) })
        }
        val type = ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE or
                   ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PLAYBACK
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(NOTE_ID, notification(), type)
        } else {
            startForeground(NOTE_ID, notification())
        }
    }

    private fun act(action: String): PendingIntent = PendingIntent.getService(
        this, action.hashCode(), Intent(this, EarService::class.java).setAction(action),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)

    private fun notification(): Notification {
        val open = PendingIntent.getActivity(this, 0,
            Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val b = NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle(lesson.title)
            .setContentText(if (asked == 0) "Say the interval" else "$hits of $asked")
            .setContentIntent(open)
            .setOngoing(true)
            .setSilent(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
        if (paused) b.addAction(0, "Resume", act(ACTION_RESUME))
        else {
            b.addAction(0, "Pause", act(ACTION_PAUSE))
            b.addAction(0, "Skip", act(ACTION_SKIP))
        }
        b.addAction(0, "Stop", act(ACTION_STOP))
        return b.build()
    }

    private fun notifyBar() {
        try { getSystemService(NotificationManager::class.java).notify(NOTE_ID, notification()) }
        catch (_: Exception) {}
    }

    // ---- view plumbing ---------------------------------------------------

    private fun lag() = "+" + (System.currentTimeMillis() - listenSince) + "ms"
    private fun pct(a: Int, b: Int) = if (b == 0) 0 else Math.round(100.0 * a / b).toInt()

    private fun status(text: String, tone: Int) { observer?.onStatus(text, tone) }

    private fun pushTally() {
        observer?.onTally(
            if (asked == 0) "" else "$hits \u2713",
            if (asked == 0) "" else "$misses \u2717")
        notifyBar()
    }

    private fun pushTransport() {
        observer?.onTransport(running && !paused)
    }

    private fun log(kind: String, message: String) {
        val t = "%6.1fs".format((System.currentTimeMillis() - startedAt) / 1000.0)
        val line = "$t ${kind.padEnd(5)} $message"
        synchronized(logLines) {
            logLines.add(line)
            while (logLines.size > 140) logLines.removeAt(0)
        }
        observer?.onLog(line)
    }

    fun logText(): String = synchronized(logLines) { logLines.joinToString("\n") }

    private fun replay(o: Observer) {
        o.onLesson(lesson)
        pushTransport(); pushTally()
        synchronized(logLines) { logLines.forEach { o.onLog(it) } }
    }

    companion object {
        const val CHANNEL = "practice"
        const val NOTE_ID = 1
        const val ACTION_PAUSE = "pause"
        const val ACTION_RESUME = "resume"
        const val ACTION_SKIP = "skip"
        const val ACTION_STOP = "stop"
        const val TONE_PLAIN = 0
        const val TONE_OK = 1
        const val TONE_BAD = 2
        /** How long to wait for a spoken answer before playing it again. */
        const val ANSWER_WINDOW = 12000L
        const val MAX_REPEATS = 5
    }
}
