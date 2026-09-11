// minimal.js — 最小化 frida 探针，判断小红书是否对 frida 有硬性反制
setTimeout(function () {
    send("MINIMAL_OK arch=" + Process.arch + " pid=" + Process.id);
}, 0);
