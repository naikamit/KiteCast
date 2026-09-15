package com.millsandgoon.ear

import android.content.Context
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import java.util.Locale

/**
 * Calm, unhurried, General American — the same register as the web app, and
 * the reason the rate sits well under 1.0.
 */
class Speaker(context: Context, private val onReady: () -> Unit) {

    private var tts: TextToSpeech? = null
    private var ready = false
    private var counter = 0
    private val done = HashMap<String, () -> Unit>()

    init {
        tts = TextToSpeech(context) { status ->
            if (status == TextToSpeech.SUCCESS) {
                tts?.language = Locale.US
                tts?.setSpeechRate(0.88f)
                tts?.setPitch(0.96f)
                tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(id: String?) {}
                    override fun onDone(id: String?) { finish(id) }
                    @Deprecated("kept for the older signature")
                    override fun onError(id: String?) { finish(id) }
                    override fun onError(id: String?, code: Int) { finish(id) }
                })
                ready = true
                onReady()
            }
        }
    }

    private fun finish(id: String?) {
        val cb = synchronized(done) { done.remove(id) }
        cb?.invoke()
    }

    /** Speaks, then calls back. Never blocks the caller waiting on the engine. */
    fun say(text: String, whenDone: () -> Unit) {
        val engine = tts
        if (!ready || engine == null) { whenDone(); return }
        val id = "u${counter++}"
        synchronized(done) { done[id] = whenDone }
        engine.speak(text, TextToSpeech.QUEUE_FLUSH, null, id)
    }

    fun stop() { try { tts?.stop() } catch (_: Exception) {} }

    fun release() {
        try { tts?.stop(); tts?.shutdown() } catch (_: Exception) {}
        tts = null
    }
}
