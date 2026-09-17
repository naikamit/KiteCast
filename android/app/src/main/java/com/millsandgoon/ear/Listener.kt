package com.millsandgoon.ear

import android.media.AudioDeviceInfo
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.content.Context
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer
import org.vosk.android.StorageService
import kotlin.concurrent.thread

/**
 * Offline recognition, constrained to the only things you could sensibly say.
 *
 * This is the whole reason for a native build. The web recogniser chose between
 * your answer and the entire English language, over a network round trip, and
 * was torn down after every utterance. Vosk streams continuously, offline,
 * against a twelve-word grammar.
 *
 * The capture loop is ours rather than Vosk's own SpeechService because the
 * microphone has to be *chosen*. With a Bluetooth headset connected, leaving
 * the pick to the framework got the phone's own microphone while the earbuds
 * played the tones — which is no use at all with the phone in a pocket.
 * Pinning the device is the only way to be sure.
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
    @Volatile private var paused = false
    @Volatile private var capturing = false
    @Volatile private var stale = false
    private var worker: Thread? = null
    private var record: AudioRecord? = null

    /** Set before start() to pin capture to a headset; null means the phone. */
    @Volatile var preferred: AudioDeviceInfo? = null

    /** Whether capture is open, which is not the same as being un-paused. */
    val listening: Boolean get() = capturing

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
        if (capturing) return
        val rate = Synth.SAMPLE_RATE_RECOGNITION.toInt()
        try {
            val min = AudioRecord.getMinBufferSize(
                rate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            if (min <= 0) { onFailed("no microphone at $rate Hz"); return }

            val rec = AudioRecord.Builder()
                // VOICE_COMMUNICATION follows the call route, which is where a
                // Bluetooth headset's microphone lives. VOICE_RECOGNITION does
                // not reliably follow it.
                .setAudioSource(MediaRecorder.AudioSource.VOICE_COMMUNICATION)
                .setAudioFormat(
                    AudioFormat.Builder()
                        .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                        .setSampleRate(rate)
                        .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                        .build())
                .setBufferSizeInBytes(min * 4)
                .build()

            preferred?.let {
                if (!rec.setPreferredDevice(it)) onLog("warn", "could not pin the microphone")
            }
            if (rec.state != AudioRecord.STATE_INITIALIZED) {
                rec.release(); onFailed("microphone would not open"); return
            }

            val recognizer = Recognizer(m, Synth.SAMPLE_RATE_RECOGNITION, voskGrammar())
            recognizer.setMaxAlternatives(3)

            record = rec
            capturing = true
            rec.startRecording()
            onLog("mic", "listening offline on ${routeName(rec)}")

            worker = thread(name = "ear-capture", isDaemon = true) {
                val buf = ShortArray(min / 2)
                try {
                    while (capturing) {
                        val n = rec.read(buf, 0, buf.size)
                        // A read that keeps failing would spin this thread at
                        // the speed of the processor rather than the speed of
                        // the audio clock, which is a flat battery in an hour.
                        if (n < 0) { onLog("warn", "microphone read failed ($n)"); break }
                        if (n == 0) continue

                        // Decoding is the expensive half, and while the drill
                        // is talking nobody wants the answer. Skipping it here
                        // is the difference between a warm phone and a cold
                        // one; the microphone stays open so the next word is
                        // never clipped.
                        if (paused) continue
                        if (stale) { stale = false; recognizer.reset() }

                        val complete = recognizer.acceptWaveForm(buf, n)
                        if (complete) text(recognizer.result, "text")?.let(onFinal)
                        else text(recognizer.partialResult, "partial")?.let(onPartial)
                    }
                } catch (e: Exception) {
                    if (capturing) onLog("warn", "recognition: ${e.message}")
                } finally {
                    try { recognizer.close() } catch (_: Exception) {}
                }
            }
        } catch (e: SecurityException) {
            onFailed("microphone permission is missing")
        } catch (e: Exception) {
            onFailed("recogniser would not start: ${e.message}")
        }
    }

    /** Pick the microphone up again on the other device. */
    fun restart() {
        val wasPaused = paused
        stop()
        start()
        setPaused(wasPaused)
    }

    /**
     * Go quiet for a moment without letting the microphone go.
     *
     * This is the beat while the drill speaks, not the pause button. Tearing
     * capture down and building it up for every prompt would cost more than it
     * saves and would re-acquire the device each time; decoding stops, which
     * is the half that costs. Skipped audio leaves the recogniser mid-word, so
     * it is reset before the next one.
     *
     * For the pause button, and for anything longer, use stop(): it releases
     * the microphone too.
     */
    fun setPaused(value: Boolean) {
        if (paused == value) return
        paused = value
        if (!value) stale = true
    }

    fun stop() {
        capturing = false
        val rec = record
        record = null
        // Stop before joining: the worker is asleep inside read() and only
        // stopping wakes it. Joining first would time out and then release the
        // recorder from under a thread still reading it.
        try { rec?.stop() } catch (_: Exception) {}
        worker?.let { try { it.join(1000) } catch (_: Exception) {} }
        worker = null
        try { rec?.release() } catch (_: Exception) {}
        paused = false
    }

    fun release() {
        stop()
        try { model?.close() } catch (_: Exception) {}
        model = null
    }

    private fun routeName(rec: AudioRecord): String {
        val d = rec.routedDevice ?: return "the phone"
        return when (d.type) {
            AudioDeviceInfo.TYPE_BLUETOOTH_SCO -> "the headset"
            AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_USB_HEADSET -> "the wired headset"
            AudioDeviceInfo.TYPE_BUILTIN_MIC -> "the phone"
            else -> "device ${d.type}"
        }
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
