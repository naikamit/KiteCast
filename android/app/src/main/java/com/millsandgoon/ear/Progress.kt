package com.millsandgoon.ear

import android.content.Context
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

/**
 * Mastery lives on the same server the web app uses, so progress follows you
 * between the phone and the desk. Everything here is best-effort: losing a
 * tally must never interrupt a drill.
 */
class Progress(context: Context, private val onLog: (String, String) -> Unit) {

    private val prefs = context.getSharedPreferences("ear", Context.MODE_PRIVATE)
    @Volatile private var cookie: String? = prefs.getString("learner", null)
    @Volatile var lifetime: Map<String, Triple<Int, Int, Int>> = emptyMap()   // seen, correct, first
        private set

    private fun open(path: String, method: String): HttpURLConnection {
        val c = URL(BASE + path).openConnection() as HttpURLConnection
        c.requestMethod = method
        c.connectTimeout = 8000
        c.readTimeout = 8000
        cookie?.let { c.setRequestProperty("Cookie", "ear_learner=$it") }
        return c
    }

    /** The server hands out the learner id on the page itself, so ask once. */
    private fun ensureLearner() {
        if (cookie != null) return
        try {
            val c = open("/ear/", "GET")
            c.inputStream.use { it.readBytes() }
            val set = c.headerFields["Set-Cookie"]?.firstOrNull { it.startsWith("ear_learner=") }
            val token = set?.substringAfter("ear_learner=")?.substringBefore(";")
            if (!token.isNullOrBlank()) {
                cookie = token
                prefs.edit().putString("learner", token).apply()
                onLog("mic", "registered with the server")
            }
            c.disconnect()
        } catch (e: Exception) {
            onLog("warn", "no server: ${e.message}")
        }
    }

    fun load() {
        try {
            ensureLearner()
            val c = open("/ear/api/progress", "GET")
            val body = c.inputStream.bufferedReader().use(BufferedReader::readText)
            c.disconnect()
            val o = JSONObject(body)
            val by = o.optJSONObject("intervals") ?: return
            val out = HashMap<String, Triple<Int, Int, Int>>()
            for (id in by.keys()) {
                val r = by.getJSONObject(id)
                out[id] = Triple(r.optInt("seen"), r.optInt("correct"), r.optInt("first"))
            }
            lifetime = out
            val seen = o.optInt("seen")
            if (seen > 0) onLog("mic", "all time: $seen answers, ${Math.round(o.optDouble("accuracy") * 100)}%")
        } catch (e: Exception) {
            onLog("warn", "progress unavailable: ${e.message}")
        }
    }

    fun record(interval: String, correct: Boolean, firstListen: Boolean) {
        try {
            ensureLearner()
            val c = open("/ear/api/answer", "POST")
            c.doOutput = true
            c.setRequestProperty("Content-Type", "application/json")
            val body = JSONObject()
                .put("interval", interval)
                .put("correct", correct)
                .put("first_listen", firstListen)
                .toString()
            c.outputStream.use { it.write(body.toByteArray()) }
            c.inputStream.use { it.readBytes() }
            c.disconnect()
        } catch (_: Exception) {
            // Offline on a train is the normal case, not an error.
        }
    }

    companion object {
        const val BASE = "https://millsandgoon.com"
    }
}
