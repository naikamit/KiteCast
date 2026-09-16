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
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.BaseExpandableListAdapter
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.GravityCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import com.millsandgoon.ear.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity(), EarService.Observer {

    private lateinit var ui: ActivityMainBinding
    private lateinit var menu: LessonAdapter
    private var service: EarService? = null
    private var bound = false

    /** Nothing starts until an exercise is picked — there is no sensible default. */
    private var chosen: Lesson? = null
    private var startWhenBound = false

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, b: IBinder?) {
            service = (b as EarService.LocalBinder).service
            service?.observer = this@MainActivity
            bound = true
            menu.notifyDataSetChanged()
            if (startWhenBound) {
                startWhenBound = false
                chosen?.let { service?.setLesson(it) }
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
        goFullScreen()

        menu = LessonAdapter()
        ui.lessonList.setAdapter(menu)
        for (i in Group.values().indices) ui.lessonList.expandGroup(i)
        ui.lessonList.setOnChildClickListener { _, _, group, child, _ ->
            choose(lessonsIn(Group.values()[group])[child]); true
        }

        ui.menu.setOnClickListener { ui.drawer.openDrawer(GravityCompat.START) }
        ui.reset.setOnClickListener { service?.resetScore() }

        ui.transport.setOnClickListener {
            if (chosen == null) { openExercises(); return@setOnClickListener }
            val s = service
            if (s == null || !s.running) requestThenStart() else s.toggle()
        }

        ui.log.setOnLongClickListener {
            val cb = getSystemService(Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
            cb.setPrimaryClip(android.content.ClipData.newPlainText("ear log", service?.logText().orEmpty()))
            android.widget.Toast.makeText(this, "Log copied", android.widget.Toast.LENGTH_SHORT).show()
            true
        }

        openExercises()
    }

    /** No status bar, no battery, no clock. The drill is the only thing here. */
    private fun goFullScreen() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior =
                WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) goFullScreen()      // the bars come back after a swipe
    }

    private fun openExercises() {
        ui.drawer.openDrawer(GravityCompat.START)
        ui.status.text = "Pick an exercise to begin."
        ui.transport.alpha = 0.45f
    }

    private fun choose(lesson: Lesson) {
        chosen = lesson
        menu.notifyDataSetChanged()
        ui.drawer.closeDrawer(GravityCompat.START)
        ui.transport.alpha = 1f
        showLesson(lesson)
        ui.status.text = "Ready."
        service?.setLesson(lesson)
    }

    /** The vocabulary is also the keypad: every answer you may say is a key. */
    private fun showLesson(lesson: Lesson) {
        ui.lessonTitle.text = "${lesson.group.label} · ${lesson.title}"
        ui.vocab.removeAllViews()
        for (id in lesson.set) {
            val key = LayoutInflater.from(this)
                .inflate(R.layout.item_answer, ui.vocab, false) as TextView
            key.text = displayOf(id)
            key.setOnClickListener { service?.tapAnswer(id) }
            ui.vocab.addView(key)
        }
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
            chosen?.let { s.setLesson(it) }
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

    // ---- the two-level menu ----

    private inner class LessonAdapter : BaseExpandableListAdapter() {
        private val groups = Group.values()

        override fun getGroupCount() = groups.size
        override fun getChildrenCount(g: Int) = lessonsIn(groups[g]).size
        override fun getGroup(g: Int) = groups[g]
        override fun getChild(g: Int, c: Int) = lessonsIn(groups[g])[c]
        override fun getGroupId(g: Int) = g.toLong()
        override fun getChildId(g: Int, c: Int) = lessonsIn(groups[g])[c].n.toLong()
        override fun hasStableIds() = true
        override fun isChildSelectable(g: Int, c: Int) = true

        override fun getGroupView(g: Int, expanded: Boolean, convert: View?, parent: ViewGroup): View {
            val v = (convert ?: LayoutInflater.from(this@MainActivity)
                .inflate(R.layout.item_group, parent, false)) as TextView
            v.text = groups[g].label
            return v
        }

        override fun getChildView(g: Int, c: Int, last: Boolean, convert: View?, parent: ViewGroup): View {
            val v = convert ?: LayoutInflater.from(this@MainActivity)
                .inflate(R.layout.item_lesson, parent, false)
            val lesson = lessonsIn(groups[g])[c]
            val name = v.findViewById<TextView>(R.id.lessonName)
            name.text = lesson.title
            name.setTextColor(ContextCompat.getColor(this@MainActivity,
                if (lesson.n == chosen?.n) R.color.aqua else R.color.ink_dim))

            val tally = service?.perLesson?.get(lesson.n)
            v.findViewById<TextView>(R.id.scoreRight).text =
                if (tally != null && tally.first > 0) "${tally.first} ✓" else ""
            v.findViewById<TextView>(R.id.scoreWrong).text =
                if (tally != null && tally.second > 0) "${tally.second} ✗" else ""
            return v
        }
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

    override fun onTally(right: String, wrong: String) = runOnUiThread {
        ui.tallyRight.text = right
        ui.tallyWrong.text = wrong
    }

    /** One button, two states — the icon is the label. */
    override fun onTransport(playing: Boolean) = runOnUiThread {
        ui.transport.setImageResource(if (playing) R.drawable.ic_pause else R.drawable.ic_play)
        ui.transport.contentDescription = if (playing) "Pause" else "Start"
    }
    override fun onScores() = runOnUiThread { menu.notifyDataSetChanged() }

    override fun onLog(line: String) = runOnUiThread {
        val lines = (ui.log.text.toString() + "\n" + line).trim().lines()
        ui.log.text = lines.takeLast(60).joinToString("\n")
    }

    override fun onLesson(lesson: Lesson) = runOnUiThread {
        // Until an exercise is picked the header keeps asking for one, rather
        // than quietly showing whatever the service happens to default to.
        if (chosen != null) showLesson(lesson)
    }
}
