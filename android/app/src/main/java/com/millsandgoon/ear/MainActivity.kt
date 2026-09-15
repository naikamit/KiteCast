package com.millsandgoon.ear

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.view.View
import android.widget.AdapterView
import android.widget.ArrayAdapter
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import com.millsandgoon.ear.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity(), EarService.Observer {

    private lateinit var ui: ActivityMainBinding
    private var service: EarService? = null
    private var bound = false
    private var startWhenReady = false

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, b: IBinder?) {
            service = (b as EarService.LocalBinder).service
            service?.observer = this@MainActivity
            bound = true
            if (startWhenReady) { startWhenReady = false; service?.toggle() }
        }
        override fun onServiceDisconnected(name: ComponentName?) {
            service?.observer = null; service = null; bound = false
        }
    }

    private val askPermissions = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { granted ->
        if (granted[Manifest.permission.RECORD_AUDIO] == true) launchService()
        else ui.status.text = "Microphone permission is required."
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        ui = ActivityMainBinding.inflate(layoutInflater)
        setContentView(ui.root)

        ui.lessonPicker.adapter = ArrayAdapter(
            this, android.R.layout.simple_spinner_dropdown_item,
            LESSONS.map { "${it.n}. ${it.title}" })
        ui.lessonPicker.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(p: AdapterView<*>?, v: View?, pos: Int, id: Long) {
                service?.setLesson(LESSONS[pos])
            }
            override fun onNothingSelected(p: AdapterView<*>?) {}
        }

        ui.transport.setOnClickListener {
            if (service == null) { startWhenReady = true; requestThenStart() }
            else if (!service!!.running) requestThenStart()
            else service?.toggle()
        }

        ui.log.setOnLongClickListener {
            val text = service?.logText().orEmpty()
            val cb = getSystemService(Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
            cb.setPrimaryClip(android.content.ClipData.newPlainText("ear log", text))
            ui.heard.text = "log copied"
            true
        }

        onLesson(LESSONS[0])
    }

    private fun requestThenStart() {
        val need = ArrayList<String>()
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED) need.add(Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS)
            != PackageManager.PERMISSION_GRANTED) need.add(Manifest.permission.POST_NOTIFICATIONS)
        if (need.isEmpty()) launchService() else askPermissions.launch(need.toTypedArray())
    }

    private fun launchService() {
        val intent = Intent(this, EarService::class.java)
        ContextCompat.startForegroundService(this, intent)
        if (!bound) bindService(intent, connection, Context.BIND_AUTO_CREATE)
        else service?.toggle()
    }

    override fun onStart() {
        super.onStart()
        if (!bound) bindService(Intent(this, EarService::class.java), connection, 0)
    }

    override fun onStop() {
        super.onStop()
        // The service keeps running with the screen off — that is the point —
        // so only the view connection goes away.
        service?.observer = null
        if (bound) { try { unbindService(connection) } catch (_: Exception) {}; bound = false }
    }

    // ---- observer ----

    override fun onStatus(text: String, tone: Int) = runOnUiThread {
        ui.status.text = text
        ui.status.setTextColor(ContextCompat.getColor(this, when (tone) {
            EarService.TONE_OK -> R.color.patina
            EarService.TONE_BAD -> R.color.felt
            else -> R.color.ink
        }))
    }

    override fun onHeard(text: String) = runOnUiThread { ui.heard.text = text }
    override fun onSource(text: String) = runOnUiThread { ui.source.text = text }
    override fun onTally(text: String) = runOnUiThread { ui.tally.text = text }
    override fun onTransport(label: String) = runOnUiThread { ui.transport.text = label }

    override fun onLog(line: String) = runOnUiThread {
        val existing = ui.log.text.toString()
        val lines = (existing + "\n" + line).trim().lines()
        ui.log.text = lines.takeLast(60).joinToString("\n")
    }

    override fun onLesson(lesson: Lesson) = runOnUiThread {
        ui.lessonNum.text = "LESSON ${lesson.n} OF ${LESSONS.size}"
        ui.vocab.text = lesson.set.joinToString("  ·  ") { INTERVALS.getValue(it).display }
        if (ui.lessonPicker.selectedItemPosition != lesson.n - 1) {
            ui.lessonPicker.setSelection(lesson.n - 1)
        }
    }
}
