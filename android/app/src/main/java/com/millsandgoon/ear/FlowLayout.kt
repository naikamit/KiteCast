package com.millsandgoon.ear

import android.content.Context
import android.util.AttributeSet
import android.view.ViewGroup

/**
 * Wraps its children onto as many rows as they need.
 *
 * The answers are words of wildly different lengths — "minor second" next to
 * "fifth" — so a grid leaves either gaps or truncation, and a single scrolling
 * row puts the far end out of reach of the thumb holding the phone.
 */
class FlowLayout @JvmOverloads constructor(
    context: Context, attrs: AttributeSet? = null, defStyle: Int = 0
) : ViewGroup(context, attrs, defStyle) {

    private val gap = (10 * resources.displayMetrics.density).toInt()

    /** Runs the same walk for measuring and for laying out. */
    private fun walk(limit: Int, place: (android.view.View, Int, Int) -> Unit): Int {
        var x = 0; var y = 0; var row = 0
        for (i in 0 until childCount) {
            val child = getChildAt(i)
            if (child.visibility == GONE) continue
            if (x > 0 && x + child.measuredWidth > limit) { x = 0; y += row + gap; row = 0 }
            place(child, x, y)
            x += child.measuredWidth + gap
            row = maxOf(row, child.measuredHeight)
        }
        return y + row
    }

    override fun onMeasure(widthSpec: Int, heightSpec: Int) {
        val limit = MeasureSpec.getSize(widthSpec) - paddingLeft - paddingRight
        for (i in 0 until childCount) {
            val child = getChildAt(i)
            if (child.visibility == GONE) continue
            measureChild(child,
                MeasureSpec.makeMeasureSpec(limit, MeasureSpec.AT_MOST),
                MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED))
        }
        val tall = walk(limit) { _, _, _ -> }
        setMeasuredDimension(MeasureSpec.getSize(widthSpec),
            paddingTop + paddingBottom + tall)
    }

    override fun onLayout(changed: Boolean, l: Int, t: Int, r: Int, b: Int) {
        walk(r - l - paddingLeft - paddingRight) { child, x, y ->
            child.layout(paddingLeft + x, paddingTop + y,
                paddingLeft + x + child.measuredWidth,
                paddingTop + y + child.measuredHeight)
        }
    }
}
