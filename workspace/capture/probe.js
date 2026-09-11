// probe.js — 侦察小红书进程 / frida 注入可行性
setTimeout(function () {
    var out = {
        arch: Process.arch,
        pointerSize: Process.pointerSize,
        platform: Process.platform,
        pageSize: Process.pageSize,
        runtime: Process.runtime ? Process.runtime : "n/a",
        mainModule: Process.mainModule ? Process.mainModule.name : "n/a"
    };
    send(JSON.stringify(out));
    try {
        send("modules_count=" + Process.enumerateModules().length);
        var mods = Process.enumerateModules()
            .filter(function (m) { return /\.so$/.test(m.name); })
            .map(function (m) { return m.name; });
        send("so_modules=" + JSON.stringify(mods));
    } catch (e) {
        send("module_enum_error=" + e.message);
    }
    // Java 层可用性
    if (typeof Java !== "undefined" && Java.available) {
        Java.perform(function () {
            send("java_available=true; vm=" + Java.vm.getName());
            try {
                var Build = Java.use("android.os.Build");
                send("Build.MODEL=" + Build.MODEL.value + " ABI=" + Build.SUPPORTED_ABIS.value);
            } catch (e2) { send("build_err=" + e2.message); }
            try {
                var Classes = Java.enumerateLoadedClassesSync().filter(function (c) {
                    return /okhttp3|TrustManager|CertificatePinner|X509/.test(c);
                });
                send("tls_classes=" + JSON.stringify(Classes.slice(0, 60)));
            } catch (e3) { send("cls_err=" + e3.message); }
        });
    } else {
        send("java_available=false");
    }
    send("PROBE_DONE");
}, 0);
