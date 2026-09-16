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
        fun onHeard(text: String)
        fun onSource(text: String)
        fun onTally(text: String)
        fun onTransport(label: String)
        fun onLog(line: String)
        fun onLesson(lesson: Lesson)
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

    private var seen = 0
    private var correct = 0
    private var firstHearing = 0
    private val session = HashMap<String, Pair<Int, Int>>()   // seen, correct

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
    private var modelReady = false
    private var startPending = false

    override fun onCreate() {
        super.onCreate()
        player = Player()
        progress = Progress(this) { k, m -> log(k, m) }
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
        listener.setPaused(false)
        ask()
    }

    fun pause() {
        if (!running || paused) return
        paused = true
        phase = Phase.IDLE
        loop?.cancel(); silence?.cancel()
        player.stop(); speaker.stop()
        listener.setPaused(true)              // pause stops listening too
        log("mic", "paused — microphone off")
        status("paused", TONE_PLAIN)
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
        log("mic", "session stopped")
        if (seen > 0) say("That's $correct of $seen. ${pct(correct, seen)} percent.") {}
        status("stopped", TONE_PLAIN)
        pushTransport(); notifyBar()
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    fun setLesson(next: Lesson) {
        if (next.n == lesson.n) return
        lesson = next
        observer?.onLesson(next)
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
            status("listening", TONE_PLAIN)
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
                val name = INTERVALS.getValue(q.id).display
                status(name, TONE_PLAIN)
                awaitSpeech(name)
                delay(500)
            }
        }
    }

    private fun nextQuestion(): Question {
        val id = weightedPick()
        val semis = INTERVALS.getValue(id).semitones
        var root: Int
        do { root = 50 + Random.nextInt(84 - semis - 50 + 1) } while (root == lastRoot)
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
        val label = q.voice.label + " · " + (if (lesson.mode == Mode.MELODIC)
            (if (q.descending) "descending" else "ascending") else "harmonic")
        observer?.onSource(label)
        log("play", label + if (q.replays > 0) " · replay ${q.replays}" else "")
        val buf = Synth.renderQuestion(q.voice, q.root, INTERVALS.getValue(q.id).semitones,
            lesson.mode == Mode.MELODIC, q.descending)
        player.play(buf)
    }

    private fun armSilence() {
        silence?.cancel()
        silence = scope.launch {
            delay(16000)
            if (phase != Phase.LISTENING || paused) return@launch
            current?.let { it.replays++; play(it) }
            phase = Phase.LISTENING
            delay(20000)
            if (phase == Phase.LISTENING && !paused) resolve(null, timedOut = true)
        }
    }

    // ---- hearing ---------------------------------------------------------

    private fun onHeard(text: String, isFinal: Boolean) {
        if (!running || paused || speaking) return
        observer?.onHeard(text)

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

        val heard = interpret(listOf(text), lesson) ?: return
        if (!isFinal && heard !is Heard.Answer) return       // only answers go early

        when (heard) {
            is Heard.Cmd -> command(heard.command)
            is Heard.Jump -> setLesson(heard.lesson)
            is Heard.Answer -> if (phase == Phase.LISTENING) {
                log("hear", (if (isFinal) "final " else "interim ") + lag() + " \"$text\"")
                resolve(heard.id)
            }
            is Heard.Ambiguous -> if (phase == Phase.LISTENING) confirm(heard)
        }
    }

    private fun confirm(a: Heard.Ambiguous) {
        silence?.cancel()
        phase = Phase.CONFIRMING
        pending = a.options
        val quality = INTERVALS.getValue(a.options[0]).quality
        status("was it $quality?", TONE_PLAIN)
        say("Was it $quality?") {
            scope.launch { delay(12000); if (phase == Phase.CONFIRMING) { phase = Phase.LISTENING; armSilence() } }
        }
    }

    private fun resolve(id: String?, timedOut: Boolean = false) {
        if (phase == Phase.FEEDBACK) return
        silence?.cancel()
        phase = Phase.FEEDBACK
        val q = current ?: return
        val truth = INTERVALS.getValue(q.id)
        val right = id == q.id

        seen++
        if (right) { correct++; if (q.replays == 0) firstHearing++ }
        val prior = session[q.id] ?: Pair(0, 0)
        session[q.id] = Pair(prior.first + 1, prior.second + if (right) 1 else 0)
        pushTally()
        log(if (right) "ok" else "bad",
            truth.display + (if (id != null && !right) " — you said ${INTERVALS.getValue(id).display}" else "") +
            (if (timedOut) " — timed out" else "") + "  " + lag())
        scope.launch { progress.record(q.id, right, right && q.replays == 0) }

        scope.launch {
            if (right) {
                status(truth.display, TONE_OK)
                player.play(Synth.chime())
                delay(1150)
            } else {
                player.stop()
                status(truth.display, TONE_BAD)
                awaitSpeech("Not quite. ${truth.display}.")
                delay(120)
                val ms = play(q)
                delay(ms + 550L)
            }
            if (running && !paused && !listenMode) ask()
        }
    }

    private fun command(c: Command) {
        when (c) {
            Command.REPEAT -> if (phase == Phase.LISTENING || phase == Phase.CONFIRMING) {
                current?.let { it.replays++; scope.launch { play(it) } }
                phase = Phase.LISTENING; armSilence()
            }
            Command.SKIP -> if (phase == Phase.LISTENING || phase == Phase.CONFIRMING) resolve(null)
            Command.SCORE -> say(
                if (seen == 0) "Nothing scored yet."
                else "$correct of $seen. That's ${pct(correct, seen)} percent, " +
                     "and ${pct(firstHearing, seen)} percent on first hearing.") {
                if (running && !paused && !listenMode) ask()
            }
            Command.PAUSE -> pause()
            Command.RESUME -> resume()
            Command.STOP -> stopSession()
            Command.HELP -> say("Just name what you hear. You can also say repeat, skip, score, or pause.") {}
            Command.LISTEN -> if (!listenMode) {
                listenMode = true; player.stop(); loop?.cancel()
                say("Just listening.") { listenLoop() }
            }
            Command.DRILL -> if (listenMode) {
                listenMode = false; player.stop(); loop?.cancel()
                say("Back to it.") { ask() }
            }
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
            .setContentTitle(if (paused) "Paused" else lesson.title)
            .setContentText(if (seen == 0) "Say the interval" else "$correct of $seen")
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
            if (seen == 0) "not started"
            else "$correct of $seen  ·  ${pct(correct, seen)}%  ·  ${pct(firstHearing, seen)}% on first hearing")
        notifyBar()
    }

    private fun pushTransport() {
        observer?.onTransport(if (!running) (if (seen > 0) "Resume" else "Start")
                              else if (paused) "Resume" else "Pause")
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
    }
}
