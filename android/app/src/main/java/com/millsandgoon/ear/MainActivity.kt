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
import android.view.ViewGroup
import android.widget.ArrayAdapter
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.GravityCompat
import com.millsandgoon.ear.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity(), EarService.Observer {

    private lateinit var ui: ActivityMainBinding
    private var service: EarService? = null
    private var bound = false

    /** Nothing starts until an exercise is picked — there is no sensible default. */
    private var chosen = false
    private var chosenIndex = -1
    private var startWhenBound = false

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, b: IBinder?) {
            service = (b as EarService.LocalBinder).service
            service?.observer = this@MainActivity
            bound = true
            // A cold start binds only after the first tap, so the tap that
            // launched the service is what actually begins the drill.
            if (startWhenBound) {
                startWhenBound = false
                if (chosen) service?.setLesson(LESSONS[chosenIndex])
                service?.toggle()
            }
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

        ui.lessonList.adapter = object : ArrayAdapter<String>(
            this, R.layout.item_lesson, LESSONS.map { "${it.n}.   ${it.title}" }
        ) {
            override fun getView(pos: Int, convert: View?, parent: ViewGroup): View {
                val v = super.getView(pos, convert, parent) as TextView
                val on = pos == chosenIndex
                v.setTextColor(ContextCompat.getColor(
                    this@MainActivity, if (on) R.color.aqua else R.color.ink_dim))
                return v
            }
        }
        ui.lessonList.setOnItemClickListener { _, _, pos, _ -> choose(pos) }

        ui.menu.setOnClickListener { ui.drawer.openDrawer(GravityCompat.START) }

        ui.transport.setOnClickListener {
            if (!chosen) { openExercises(); return@setOnClickListener }
            val s = service
            if (s == null || !s.running) requestThenStart() else s.toggle()
        }

        ui.log.setOnLongClickListener {
            val cb = getSystemService(Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
            cb.setPrimaryClip(android.content.ClipData.newPlainText("ear log", service?.logText().orEmpty()))
            ui.heard.text = "log copied"
            true
        }

        openExercises()
    }

    private fun openExercises() {
        ui.drawer.openDrawer(GravityCompat.START)
        ui.status.text = "Pick an exercise to begin."
        ui.transport.alpha = 0.45f
    }

    private fun choose(pos: Int) {
        chosen = true
        chosenIndex = pos
        (ui.lessonList.adapter as ArrayAdapter<*>).notifyDataSetChanged()
        ui.drawer.closeDrawer(GravityCompat.START)
        ui.transport.alpha = 1f
        val lesson = LESSONS[pos]
        showLesson(lesson)
        ui.status.text = "Ready."
        service?.setLesson(lesson)
    }

    private fun showLesson(lesson: Lesson) {
        ui.lessonTitle.text = lesson.title
        ui.vocab.text = lesson.set.joinToString("   ·   ") { INTERVALS.getValue(it).display }
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
        val s = service
        if (s == null) {
            startWhenBound = true
            if (!bound) bindService(intent, connection, Context.BIND_AUTO_CREATE)
        } else {
            if (chosen) s.setLesson(LESSONS[chosenIndex])
            s.toggle()
        }
    }

    override fun onStart() {
        super.onStart()
        if (!bound) bindService(Intent(this, EarService::class.java), connection, 0)
    }

    override fun onStop() {
        super.onStop()
        // The service keeps running with the screen off — that is the point.
        service?.observer = null
        if (bound) { try { unbindService(connection) } catch (_: Exception) {}; bound = false }
    }

    // ---- observer ----

    override fun onStatus(text: String, tone: Int) = runOnUiThread {
        ui.status.text = text
        ui.status.setTextColor(ContextCompat.getColor(this, when (tone) {
            EarService.TONE_OK -> R.color.ok
            EarService.TONE_BAD -> R.color.bad
            else -> R.color.ink
        }))
    }

    override fun onHeard(text: String) = runOnUiThread { ui.heard.text = text }
    override fun onSource(text: String) = runOnUiThread { ui.source.text = text }
    override fun onTally(text: String) = runOnUiThread { ui.tally.text = text }
    override fun onTransport(label: String) = runOnUiThread { ui.transport.text = label }

    override fun onLog(line: String) = runOnUiThread {
        val lines = (ui.log.text.toString() + "\n" + line).trim().lines()
        ui.log.text = lines.takeLast(60).joinToString("\n")
    }

    override fun onLesson(lesson: Lesson) = runOnUiThread {
        // Until an exercise is picked the header keeps asking for one, rather
        // than quietly showing whatever the service happens to default to.
        if (chosen) showLesson(lesson)
    }
}
