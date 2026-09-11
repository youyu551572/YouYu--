// ui_dump.js — 绕过 uiautomator 限制，从进程内 dump 当前 Activity 与可点击控件
Java.perform(function () {
    var TAG = "[ui]";

    function getActivityRecords() {
        var out = [];
        try {
            var ActivityThread = Java.use("android.app.ActivityThread");
            var thread = ActivityThread.currentActivityThread();
            var map = thread.mActivities.value;
            var size = map.size();
            for (var i = 0; i < size; i++) {
                var rec = map.valueAt(i);
                if (rec && rec.activity && rec.activity.value) {
                    out.push(rec.activity.value);
                }
            }
        } catch (e) {
            send(TAG + " activity enum error: " + e.message);
        }
        return out;
    }

    function viewInfo(v) {
        var cls = "", text = "", desc = "";
        try { cls = v.getClass().getName(); } catch (e) { }
        try {
            var TextView = Java.use("android.widget.TextView");
            if (Java.cast(v, Java.use("android.view.View")).getClass && cls.indexOf("TextView") >= 0) {
                text = String(TextView.cast(v).getText());
            }
        } catch (e) { }
        try {
            desc = String(Java.cast(v, Java.use("android.view.View")).getContentDescription());
        } catch (e) { }
        return { cls: cls, text: text, desc: desc };
    }

    function walk(v, depth, results) {
        if (!v) return;
        if (depth > 25 || results.length > 400) return;
        try {
            var view = Java.cast(v, Java.use("android.view.View"));
            var vis = view.getVisibility();
            if (vis !== 0) return;
            var loc = Java.array("int", [0, 0]);
            view.getLocationOnScreen(loc);
            var x = loc[0], y = loc[1];
            var w = view.getWidth(), h = view.getHeight();
            var info = viewInfo(view);
            var clickable = view.isClickable();
            if ((info.text && info.text !== "null") || (info.desc && info.desc !== "null") || clickable) {
                results.push({
                    d: depth, cls: info.cls.split(".").pop(),
                    text: info.text, desc: info.desc,
                    x: x, y: y, w: w, h: h,
                    cx: x + Math.floor(w / 2), cy: y + Math.floor(h / 2),
                    clickable: clickable
                });
            }
            var VG = Java.use("android.view.ViewGroup");
            if (Java.cast(view, VG).$instanceOf(VG)) {
                var g = Java.cast(view, VG);
                var n = g.getChildCount();
                for (var i = 0; i < n; i++) walk(g.getChildAt(i), depth + 1, results);
            }
        } catch (e) { }
    }

    var recs = getActivityRecords();
    recs.forEach(function (act) {
        var name = "";
        try { name = act.getClass().getName(); } catch (e) { }
        send(TAG + " ACTIVITY: " + name);
    });

    // dump 最上面那个 activity 的 view 树
    if (recs.length > 0) {
        var act = recs[0];
        try {
            var win = act.getWindow();
            var decor = win.getDecorView();
            var results = [];
            walk(decor, 0, results);
            send(TAG + " VIEWCOUNT=" + results.length);
            results.forEach(function (r) {
                send(TAG + " V|" + r.cls + "|txt=" + r.text + "|desc=" + r.desc +
                    "|center=" + r.cx + "," + r.cy + "|size=" + r.w + "x" + r.h +
                    "|click=" + r.clickable);
            });
        } catch (e) {
            send(TAG + " view walk error: " + e.message);
        }
    }
    send(TAG + " DUMP_DONE");
});
