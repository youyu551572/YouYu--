// launch_imgsearch.js — 在 xhs 进程内启动图片搜索页（绕过 exported 限制）
Java.perform(function () {
    var TAG = "[launch]";
    var PKG = "com.xingin.xhs";

    // 候选入口，按优先级尝试
    var CANDIDATES = [
        "com.xingin.imagesearch.ImageSearchActivity",
        "com.xingin.alioth.imagesearch.page.ImageSearchActivity",
        "com.xingin.imagesearch.active.container.ActiveImageSearchActivity",
        "com.xingin.alioth.imagesearch.active.container.ActiveImageSearchActivity",
        "com.xingin.alioth.imagesearch.active.scan.ImageSearchScanActivity",
        "com.xingin.alioth.search.GlobalSearchActivity"
    ];

    var target = null;
    for (var i = 0; i < CANDIDATES.length; i++) {
        try {
            Java.use(CANDIDATES[i]);
            target = CANDIDATES[i];
            send(TAG + " resolvable: " + CANDIDATES[i]);
            if (!target) target = CANDIDATES[i];
        } catch (e) {
            send(TAG + " NOT resolvable: " + CANDIDATES[i]);
        }
    }

    // 取当前进程的 Application 作为 context
    var ActivityThread = Java.use("android.app.ActivityThread");
    var app = ActivityThread.currentApplication();
    send(TAG + " app=" + app);

    var Intent = Java.use("android.content.Intent");
    var FLAG_NEW_TASK = 0x10000000;

    var launched = [];
    CANDIDATES.forEach(function (cls) {
        try {
            var it = Intent.$new();
            it.setClassName(PKG, cls);
            it.addFlags(FLAG_NEW_TASK);
            app.startActivity(it);
            launched.push(cls);
            send(TAG + " LAUNCHED: " + cls);
        } catch (e) {
            send(TAG + " launch failed " + cls + " -> " + e.message);
        }
    });

    send(TAG + " launched_count=" + launched.length);
});
