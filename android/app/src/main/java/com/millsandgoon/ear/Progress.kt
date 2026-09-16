package com.millsandgoon.ear

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject

/**
 * Your record against every interval and chord, kept on the phone.
 *
 * It used to live on a server so the phone and the desk could share it. There
 * is no desk any more, and a record that needed a connection was a record that
 * quietly lost every answer you gave on a train — the POST failed, nothing
 * retried, and the drill went on weighting questions off a stale copy. This
 * cannot fail that way: the write is local and it happens before the next
 * question is asked.
 */
class Progress(context: Context, private val onLog: (String, String) -> Unit) {

    private val prefs = context.getSharedPreferences("ear", Context.MODE_PRIVATE)

    /** id → seen, correct, right-on-first-hearing. Replaced whole, never
     *  mutated, so the drill loop can read it without locking. */
    @Volatile var lifetime: Map<String, Triple<Int, Int, Int>> = emptyMap()
        private set

    fun load() {
        // A leftover from the server days. Harmless, but it named this phone
        // to something that no longer exists.
        if (prefs.contains(LEGACY_LEARNER)) prefs.edit().remove(LEGACY_LEARNER).apply()

        val raw = prefs.getString(KEY, null) ?: return
        try {
            val o = JSONObject(raw)
            val out = HashMap<String, Triple<Int, Int, Int>>()
            for (id in o.keys()) {
                val a = o.getJSONArray(id)
                out[id] = Triple(a.getInt(0), a.getInt(1), a.getInt(2))
            }
            lifetime = out
            val seen = out.values.sumOf { it.first }
            val correct = out.values.sumOf { it.second }
            if (seen > 0) onLog("mic", "all time: $seen answers, ${pct(correct, seen)}%")
        } catch (e: Exception) {
            // A half-written file is worth less than an empty one — starting
            // over costs a few questions of weighting, nothing else.
            onLog("warn", "record unreadable, starting fresh: ${e.message}")
            prefs.edit().remove(KEY).apply()
        }
    }

    @Synchronized
    fun record(interval: String, correct: Boolean, firstListen: Boolean) {
        val prior = lifetime[interval] ?: Triple(0, 0, 0)
        val next = HashMap(lifetime)
        next[interval] = Triple(
            prior.first + 1,
            prior.second + if (correct) 1 else 0,
            prior.third + if (firstListen) 1 else 0)
        lifetime = next
        save(next)
    }

    private fun save(map: Map<String, Triple<Int, Int, Int>>) {
        val o = JSONObject()
        for ((id, t) in map) {
            o.put(id, JSONArray().put(t.first).put(t.second).put(t.third))
        }
        prefs.edit().putString(KEY, o.toString()).apply()
    }

    private fun pct(a: Int, b: Int) = if (b == 0) 0 else Math.round(100.0 * a / b).toInt()

    companion object {
        private const val KEY = "lifetime"
        private const val LEGACY_LEARNER = "learner"
    }
}
