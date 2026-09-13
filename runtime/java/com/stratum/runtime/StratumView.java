package com.stratum.runtime;

import android.content.Context;
import android.graphics.Canvas;
import android.view.MotionEvent;
import android.view.ViewGroup;

/**
 * Universal native View/ViewGroup bridge for Stratum. One Java class
 * covers custom 2D drawing (onDraw), custom layout (onMeasure/onLayout),
 * and custom touch handling (onTouchEvent), all routed to Python via
 * StratumInvocationHandler.nativeDispatch(key, method, args).
 *
 * Extends ViewGroup (not View) so the same class can host child views
 * as well as pure canvas drawing; if you never call addView() it
 * behaves exactly like a plain View. Pure android.jar.
 */
public class StratumView extends ViewGroup {

    private final String key_;

    public StratumView(Context context, String key) {
        super(context);
        this.key_ = key;
        setWillNotDraw(false);
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        StratumInvocationHandler.nativeDispatch(key_, "onDraw", new Object[]{ canvas });
    }

    @Override
    protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        Object res = StratumInvocationHandler.nativeDispatch(key_, "onMeasure",
                new Object[]{ widthMeasureSpec, heightMeasureSpec });
        if (res instanceof int[]) {
            int[] dims = (int[]) res;
            if (dims.length == 2) {
                setMeasuredDimension(dims[0], dims[1]);
                return;
            }
        }
        super.onMeasure(widthMeasureSpec, heightMeasureSpec);
    }

    @Override
    protected void onLayout(boolean changed, int l, int t, int r, int b) {
        StratumInvocationHandler.nativeDispatch(key_, "onLayout",
                new Object[]{ changed, l, t, r, b });
    }

    @Override
    public boolean onTouchEvent(MotionEvent event) {
        Object res = StratumInvocationHandler.nativeDispatch(key_, "onTouchEvent", new Object[]{ event });
        if (res instanceof Boolean) {
            return (Boolean) res;
        }
        return super.onTouchEvent(event);
    }

    public String getStratumKey() {
        return key_;
    }
}