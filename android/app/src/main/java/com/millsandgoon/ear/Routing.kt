package com.millsandgoon.ear

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.Looper

/**
 * Which headset the drill talks to, and which microphone it listens on.
 *
 * A Bluetooth headset is two devices pretending to be one. A2DP carries good
 * stereo audio and has no microphone at all; the microphone lives on a second
 * profile, SCO, which is a telephone line — one narrowband channel, both ways,
 * and it suspends A2DP while it is up. You cannot have the earbud's microphone
 * and the earbud's music quality at the same time. That is Bluetooth, not
 * Android, and no app setting escapes it.
 *
 * So the rule is: if a headset with a microphone is connected, the whole drill
 * goes over it — tones, prompts and answers alike — because a drill you can
 * answer while the phone is in your pocket is worth more than a drill with
 * better tone that cannot hear you. With nothing connected, the phone keeps
 * both its microphone and its full sample rate.
 */
class Routing(
    context: Context,
    private val onLog: (String, String) -> Unit,
    private val onChanged: () -> Unit
) {

    private val am = context.getSystemService(AudioManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private val appContext = context.applicationContext

    /** True once the headset's microphone is actually live, not merely paired. */
    @Volatile var onHeadset = false
        private set

    private var wanted = false
    private var scoReceiver: BroadcastReceiver? = null

    private val devices = object : AudioDeviceCallback() {
        override fun onAudioDevicesAdded(added: Array<out AudioDeviceInfo>?) = reconsider()
        override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>?) = reconsider()
    }

    init {
        am.registerAudioDeviceCallback(devices, main)
    }

    /** A connected headset that can also hear you. Earbuds without a
     *  microphone, and speakers, are not candidates. */
    private fun headsetMic(): AudioDeviceInfo? {
        val types = mutableListOf(AudioDeviceInfo.TYPE_BLUETOOTH_SCO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            types.add(AudioDeviceInfo.TYPE_BLE_HEADSET)
        }
        return am.getDevices(AudioManager.GET_DEVICES_INPUTS)
            .firstOrNull { it.type in types }
    }

    /** The device the recogniser should be pinned to. Null means the phone. */
    fun micDevice(): AudioDeviceInfo? = if (onHeadset) headsetMic() else null

    fun engage() {
        wanted = true
        reconsider()
    }

    fun release() {
        wanted = false
        stopSco()
    }

    fun shutdown() {
        release()
        try { am.unregisterAudioDeviceCallback(devices) } catch (_: Exception) {}
    }

    /** Called on every device change, so plugging in mid-drill just works. */
    private fun reconsider() {
        val available = headsetMic() != null
        if (wanted && available && !onHeadset) startSco()
        else if ((!wanted || !available) && onHeadset) stopSco()
    }

    private fun startSco() {
        try {
            am.mode = AudioManager.MODE_IN_COMMUNICATION
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                val device = am.availableCommunicationDevices.firstOrNull {
                    it.type == AudioDeviceInfo.TYPE_BLUETOOTH_SCO ||
                    it.type == AudioDeviceInfo.TYPE_BLE_HEADSET
                }
                if (device == null) { am.mode = AudioManager.MODE_NORMAL; return }
                if (am.setCommunicationDevice(device)) settle("headset")
                else { am.mode = AudioManager.MODE_NORMAL; onLog("warn", "headset refused the route") }
            } else {
                // Older Androids answer asynchronously, so wait to be told.
                listenForSco()
                @Suppress("DEPRECATION")
                am.startBluetoothSco()
                @Suppress("DEPRECATION")
                am.setBluetoothScoOn(true)
            }
        } catch (e: Exception) {
            onLog("warn", "headset routing failed: ${e.message}")
            try { am.mode = AudioManager.MODE_NORMAL } catch (_: Exception) {}
        }
    }

    private fun stopSco() {
        val was = onHeadset
        onHeadset = false
        try {
            unlistenForSco()
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                am.clearCommunicationDevice()
            } else {
                @Suppress("DEPRECATION")
                am.setBluetoothScoOn(false)
                @Suppress("DEPRECATION")
                am.stopBluetoothSco()
            }
            am.mode = AudioManager.MODE_NORMAL
        } catch (_: Exception) {}
        if (was) {
            onLog("mic", "headset gone — phone microphone, full resolution")
            onChanged()
        }
    }

    private fun settle(what: String) {
        onHeadset = true
        onLog("mic", "$what: microphone and tones both over Bluetooth")
        onChanged()
    }

    private fun listenForSco() {
        if (scoReceiver != null) return
        val r = object : BroadcastReceiver() {
            override fun onReceive(c: Context?, intent: Intent?) {
                @Suppress("DEPRECATION")
                val state = intent?.getIntExtra(AudioManager.EXTRA_SCO_AUDIO_STATE, -1) ?: -1
                @Suppress("DEPRECATION")
                if (state == AudioManager.SCO_AUDIO_STATE_CONNECTED && !onHeadset) settle("headset")
            }
        }
        @Suppress("DEPRECATION")
        val filter = IntentFilter(AudioManager.ACTION_SCO_AUDIO_STATE_UPDATED)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            appContext.registerReceiver(r, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            appContext.registerReceiver(r, filter)
        }
        scoReceiver = r
    }

    private fun unlistenForSco() {
        scoReceiver?.let { try { appContext.unregisterReceiver(it) } catch (_: Exception) {} }
        scoReceiver = null
    }
}
