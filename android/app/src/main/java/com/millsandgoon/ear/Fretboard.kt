package com.millsandgoon.ear

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.util.AttributeSet
import android.view.MotionEvent
import android.view.View
import androidx.core.content.ContextCompat
import kotlin.math.roundToInt

/**
 * A guitar neck: six strings, twelve frets, and nothing that is not one of
 * those. No wood, no shadow, no rosewood gradient — the drill is about hearing
 * and this is here to be read at a glance and hit with a thumb.
 *
 * It is always playable. After a wrong answer it also marks where the notes
 * you missed actually were, which is the one moment a picture beats a word.
 */
class Fretboard @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null, defStyle: Int = 0
) : View(context, attrs, defStyle) {

    var marks: List<Neck.Spot> = emptyList()
        set(value) { field = value; invalidate() }

    /** Called with the MIDI note under the finger. */
    var onPluck: ((Int) -> Unit)? = null

    private var lit: Neck.Spot? = null

    private val density = resources.displayMetrics.density
    private fun dp(v: Float) = v * density
    private fun colour(id: Int) = ContextCompat.getColor(context, id)

    private val fretPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = colour(R.color.line); strokeWidth = dp(1f)
    }
    private val nutPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = colour(R.color.ink_dim); strokeWidth = dp(3f)
    }
    private val stringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = colour(R.color.ink_faint)
    }
    private val inlayPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = colour(R.color.line); style = Paint.Style.FILL
    }
    private val markPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = colour(R.color.aqua); style = Paint.Style.FILL
    }
    private val rootPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = colour(R.color.ink); style = Paint.Style.FILL
    }
    private val openPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = colour(R.color.aqua); style = Paint.Style.STROKE; strokeWidth = dp(2f)
    }
    private val litPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = colour(R.color.aqua_soft); style = Paint.Style.FILL
    }

    // ---- geometry: the nut sits in from the left so open notes have room ----

    private val openLane get() = dp(20f)
    private val boardLeft get() = paddingLeft + openLane
    private val boardRight get() = width - paddingRight - dp(4f)
    private val fretWidth get() = (boardRight - boardLeft) / Neck.FRETS
    private val topY get() = paddingTop + dp(10f)
    private val bottomY get() = height - paddingBottom - dp(10f)
    private val gap get() = (bottomY - topY) / (Neck.OPEN.size - 1)

    /** String 0 is the fat E and belongs at the bottom. */
    private fun yOf(string: Int) = bottomY - string * gap

    private fun xOf(fret: Int) =
        if (fret == 0) boardLeft - openLane / 2f else boardLeft + (fret - 0.5f) * fretWidth

    private val radius get() = minOf(gap * 0.42f, fretWidth * 0.40f, dp(15f))

    override fun onDraw(canvas: Canvas) {
        val left = boardLeft
        val right = boardRight
        val top = topY
        val bottom = bottomY

        for (f in 1..Neck.FRETS) {
            val x = left + f * fretWidth
            canvas.drawLine(x, top, x, bottom, fretPaint)
        }
        canvas.drawLine(left, top, left, bottom, nutPaint)

        // The dots a player navigates by, dim enough to stay out of the way.
        val middle = (top + bottom) / 2f
        for (f in intArrayOf(3, 5, 7, 9)) {
            canvas.drawCircle(xOf(f), middle, dp(3.5f), inlayPaint)
        }
        canvas.drawCircle(xOf(12), middle - gap, dp(3.5f), inlayPaint)
        canvas.drawCircle(xOf(12), middle + gap, dp(3.5f), inlayPaint)

        // Wound strings are visibly fatter, which is also how you find them.
        for (s in Neck.OPEN.indices) {
            stringPaint.strokeWidth = dp(2.3f - 0.26f * s)
            val y = yOf(s)
            canvas.drawLine(left, y, right, y, stringPaint)
        }

        lit?.let { canvas.drawCircle(xOf(it.fret), yOf(it.string), radius, litPaint) }

        // The lowest note is the root, and it is the one to find first.
        for ((i, m) in marks.withIndex()) {
            val x = xOf(m.fret)
            val y = yOf(m.string)
            if (m.fret == 0) canvas.drawCircle(x, y, radius - dp(1f), openPaint)
            else canvas.drawCircle(x, y, radius, if (i == 0) rootPaint else markPaint)
        }
    }

    // ---- playing it ------------------------------------------------------

    private fun spotAt(x: Float, y: Float): Neck.Spot? {
        val string = ((bottomY - y) / gap).roundToInt()
        if (string !in Neck.OPEN.indices) return null
        val fret = if (x < boardLeft) 0
                   else ((x - boardLeft) / fretWidth).toInt() + 1
        if (fret !in 0..Neck.FRETS) return null
        return Neck.Spot(string, fret)
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_MOVE -> {
                val spot = spotAt(event.x, event.y)
                // Sliding across the neck sounds each new fret once, the way
                // dragging a finger along the strings would.
                if (spot != null && spot != lit) {
                    lit = spot
                    invalidate()
                    onPluck?.invoke(Neck.midiAt(spot.string, spot.fret))
                }
                if (event.actionMasked == MotionEvent.ACTION_DOWN) performClick()
                return true
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                lit = null
                invalidate()
                return true
            }
        }
        return super.onTouchEvent(event)
    }

    override fun performClick(): Boolean {
        super.performClick()
        return true
    }
}
