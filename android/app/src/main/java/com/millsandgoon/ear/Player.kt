package com.millsandgoon.ear

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack

/** Plays a rendered buffer. One track per sound, released when it finishes. */
class Player {

    private var track: AudioTrack? = null

    /**
     * When the headset's microphone is in use its speaker is on the same
     * narrowband channel, and media audio does not go there — it goes to a
     * profile that Bluetooth has suspended. So the tones have to travel as
     * call audio too, or they are played to nobody.
     */
    @Volatile var viaHeadset = false

    fun play(samples: FloatArray): Int {
        stop()
        if (samples.isEmpty()) return 0
        val t = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(if (viaHeadset) AudioAttributes.USAGE_VOICE_COMMUNICATION
                              else AudioAttributes.USAGE_MEDIA)
                    .setContentType(if (viaHeadset) AudioAttributes.CONTENT_TYPE_SPEECH
                                    else AudioAttributes.CONTENT_TYPE_MUSIC)
                    .build()
            )
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_FLOAT)
                    .setSampleRate(Synth.SAMPLE_RATE)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                    .build()
            )
            .setTransferMode(AudioTrack.MODE_STATIC)
            .setBufferSizeInBytes(samples.size * 4)
            .build()
        t.write(samples, 0, samples.size, AudioTrack.WRITE_BLOCKING)
        t.play()
        track = t
        return (samples.size * 1000L / Synth.SAMPLE_RATE).toInt()
    }

    fun stop() {
        track?.let {
            try { it.pause(); it.flush(); it.stop() } catch (_: Exception) {}
            try { it.release() } catch (_: Exception) {}
        }
        track = null
    }
}
