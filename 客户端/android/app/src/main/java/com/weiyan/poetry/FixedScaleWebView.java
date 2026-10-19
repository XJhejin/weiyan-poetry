package com.weiyan.poetry;

import android.content.Context;
import android.view.MotionEvent;
import android.webkit.WebView;

/** Cancel a multi-finger gesture before WebView or its page can zoom. */
final class FixedScaleWebView extends WebView {
    private boolean suppressGesture;

    FixedScaleWebView(Context context) { super(context); }

    @Override public boolean onTouchEvent(MotionEvent event) {
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_DOWN) suppressGesture = false;
        if (event.getPointerCount() > 1) {
            if (!suppressGesture) {
                MotionEvent cancel = MotionEvent.obtain(event);
                cancel.setAction(MotionEvent.ACTION_CANCEL);
                super.onTouchEvent(cancel);
                cancel.recycle();
            }
            suppressGesture = true;
            return true;
        }
        if (suppressGesture) {
            if (action == MotionEvent.ACTION_UP || action == MotionEvent.ACTION_CANCEL) suppressGesture = false;
            return true;
        }
        return super.onTouchEvent(event);
    }
}
