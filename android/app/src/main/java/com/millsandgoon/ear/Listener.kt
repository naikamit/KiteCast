package com.millsandgoon.ear

import android.content.Context
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.RecognitionListener
import org.vosk.android.SpeechService
import org.vosk.android.StorageService

/**
 * Offline recognition, constrained to the only things you could sensibly say.
 *
 * This is the whole reason for a native build. The web recogniser chose between
 * your answer and the entire English language, over a network round trip, and
 * was torn down after every utterance. Vosk streams continuously, offline,
 * against a twelve-word grammar.
 */
class Listener(
    private val context: Context,
    private val onLog: (String, String) -> Unit,
    private val onPartial: (String) -> Unit,
    private val onFinal: (String) -> Unit,
    private val onReady: () -> Unit,
    private val onFailed: (String) -> Unit
) {

    private var model: Model? = null
    private var speech: SpeechService? = null
    private var paused = false

    fun load() {
        StorageService.unpack(context, MODEL_ASSET, "model",
            { m ->
                model = m
                onLog("mic", "model ready")
                onReady()
            },
            { e -> onFailed("model failed to unpack: ${e.message}") })
    }

    fun start() {
        val m = model ?: return
        if (speech != null) return
        try {
            val rec = Recognizer(m, Synth.SAMPLE_RATE_RECOGNITION, voskGrammar())
            rec.setMaxAlternatives(3)
            val service = SpeechService(rec, Synth.SAMPLE_RATE_RECOGNITION)
            service.startListening(object : RecognitionListener {
                override fun onPartialResult(hypothesis: String?) {
                    text(hypothesis, "partial")?.let { if (!paused) onPartial(it) }
                }
                override fun onResult(hypothesis: String?) {
                    text(hypothesis, "text")?.let { if (!paused) onFinal(it) }
                }
                override fun onFinalResult(hypothesis: String?) {}
                override fun onError(e: Exception?) { onLog("warn", "recognition: ${e?.message}") }
                override fun onTimeout() { onLog("mic", "recogniser timed out") }
            })
            speech = service
            onLog("mic", "listening offline")
        } catch (e: Exception) {
            onFailed("recogniser would not start: ${e.message}")
        }
    }

    /** True mute: Vosk keeps streaming but we stop feeding on its results. */
    fun setPaused(value: Boolean) {
        paused = value
        try { speech?.setPause(value) } catch (_: Exception) {}
    }

    fun stop() {
        try { speech?.stop() } catch (_: Exception) {}
        try { speech?.shutdown() } catch (_: Exception) {}
        speech = null
    }

    fun release() {
        stop()
        try { model?.close() } catch (_: Exception) {}
        model = null
    }

    private fun text(hypothesis: String?, key: String): String? {
        if (hypothesis.isNullOrBlank()) return null
        return try {
            val o = JSONObject(hypothesis)
            // With "[unk]" in the grammar, out-of-grammar audio — humming —
            // comes back as unk and is dropped here rather than scored.
            val t = o.optString(key, "").replace("[unk]", " ").trim()
            if (t.isEmpty()) null else t
        } catch (_: Exception) { null }
    }

    companion object {
        const val MODEL_ASSET = "model-en-us"
    }
}
