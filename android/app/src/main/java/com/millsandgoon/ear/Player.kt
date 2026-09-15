package com.millsandgoon.ear

import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack

/** Plays a rendered buffer. One track per sound, released when it finishes. */
class Player {

    private var track: AudioTrack? = null

    fun play(samples: FloatArray): Int {
        stop()
        if (samples.isEmpty()) return 0
        val t = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
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
