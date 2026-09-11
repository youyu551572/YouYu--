/*
 * xhs_capture.js — 小红书 9.33.4 授权逆向：SSL unpinning + 代理强制 + 明文流量落盘
 * 目标：动态抓包侧（配 mitmproxy 使用），并可直接读取 OkHttp 层明文
 */
var TAG = "[xhs-cap]";
var PROXY_HOST = "127.0.0.1";
var PROXY_PORT = 8080;
var FORCE_PROXY = true;

function log(s) { console.log(TAG + " " + s); }
function safe(name, fn) {
    try { fn(); log("hook ok: " + name); }
    catch (e) { log("hook skip: " + name + " -> " + e.message); }
}

/* ---------- 1. Java 层 SSL unpinning ---------- */

function unpinJava() {
    var X509TrustManager = Java.use("javax.net.ssl.X509TrustManager");

    // 1.1 所有 X509TrustManager 实现 -> 放行
    safe("X509TrustManager.checkServerTrusted", function () {
        var Cls = Java.use("javax.net.ssl.X509TrustManager");
        Java.enumerateLoadedClassesSync().forEach(function (name) {
            if (!/TrustManager/.test(name)) return;
            try {
                var C = Java.use(name);
                if (C.checkServerTrusted) {
                    C.checkServerTrusted.overloads.forEach(function (ov) {
                        try { ov.implementation = function () { return; }; } catch (e) { }
                    });
                }
                if (C.checkClientTrusted) {
                    C.checkClientTrusted.overloads.forEach(function (ov) {
                        try { ov.implementation = function () { return; }; } catch (e) { }
                    });
                }
                if (C.getAcceptedIssuers) {
                    C.getAcceptedIssuers.overloads.forEach(function (ov) {
                        try { ov.implementation = function () { return Java.array("java.security.cert.X509Certificate", []); }; } catch (e) { }
                    });
                }
            } catch (e) { }
        });
    });

    // 1.2 Conscrypt 内部校验
    safe("TrustManagerImpl.verifyChain", function () {
        var TMI = Java.use("com.android.org.conscrypt.TrustManagerImpl");
        TMI.verifyChain.implementation = function (untrustedChain, trustAnchorChain, host, clientAuth, ocspData, tlsSctData) {
            log("bypass TrustManagerImpl.verifyChain host=" + host);
            return untrustedChain;
        };
    });

    safe("TrustManagerImpl.checkTrustedRecursive", function () {
        var TMI = Java.use("com.android.org.conscrypt.TrustManagerImpl");
        TMI.checkTrustedRecursive.implementation = function () {
            return Java.use("java.util.ArrayList").$new();
        };
    });

    // 1.3 OkHttp CertificatePinner
    safe("CertificatePinner.check", function () {
        var CP = Java.use("okhttp3.CertificatePinner");
        CP.check.overloads.forEach(function (ov) {
            ov.implementation = function () { return; };
        });
    });
    safe("CertificatePinner.check$okhttp", function () {
        var CP = Java.use("okhttp3.CertificatePinner");
        if (CP["check$okhttp"]) CP["check$okhttp"].implementation = function () { return; };
    });

    // 1.4 HostnameVerifier
    safe("HostnameVerifier.verify", function () {
        Java.enumerateLoadedClassesSync().forEach(function (name) {
            if (!/HostnameVerifier/.test(name)) return;
            try {
                var C = Java.use(name);
                if (C.verify) {
                    C.verify.overloads.forEach(function (ov) {
                        try {
                            ov.implementation = function (h, s) { return true; };
                        } catch (e) { }
                    });
                }
            } catch (e) { }
        });
    });

    // 1.5 SSLContext 用宽松 TrustManager
    safe("SSLContext.init", function () {
        var TM = Java.registerClass({
            name: "com.wknx.LooseTM",
            implements: [Java.use("javax.net.ssl.X509TrustManager")],
            methods: {
                checkClientTrusted: function () { },
                checkServerTrusted: function () { },
                getAcceptedIssuers: function () { return []; }
            }
        });
        var tmInst = TM.$new();
        var SSLContext = Java.use("javax.net.ssl.SSLContext");
        SSLContext.init.overload("[Ljavax.net.ssl.KeyManager;", "[Ljavax.net.ssl.TrustManager;", "java.security.SecureRandom").implementation =
            function (km, tm, sr) {
                return this.init(km, [tmInst], sr);
            };
    });

    // 1.6 允许明文
    safe("NetworkSecurityPolicy.isCleartextTrafficPermitted", function () {
        var NSP = Java.use("android.security.NetworkSecurityPolicy");
        NSP.isCleartextTrafficPermitted.overload().implementation = function () { return true; };
        NSP.isCleartextTrafficPermitted.overload("java.lang.String").implementation = function () { return true; };
    });
}

/* ---------- 2. 代理强制 ---------- */

function forceProxy() {
    safe("ProxySelector.getDefault", function () {
        var ProxySelector = Java.use("java.net.ProxySelector");
        var Proxy = Java.use("java.net.Proxy");
        var InetSocketAddress = Java.use("java.net.InetSocketAddress");
        var ArrayList = Java.use("java.util.ArrayList");
        var ProxyType = Java.use("java.net.Proxy$Type");
        var addr = InetSocketAddress.$new(PROXY_HOST, PROXY_PORT);
        ProxySelector.getDefault.implementation = function () {
            return ProxySelector.$new();
        };
        // 替换 select 返回我们的代理
        var Impl = Java.registerClass({
            name: "com.wknx.ForcedProxySelector",
            superclass: ProxySelector,
            methods: {
                select: function (uri) {
                    var list = ArrayList.$new();
                    list.add(Proxy.$new(ProxyType.HTTP.value, addr));
                    log("forced proxy for " + uri);
                    return list;
                },
                connectFailed: function () { }
            }
        });
        var inst = Impl.$new();
        ProxySelector.getDefault.implementation = function () { return inst; };
    });

    // OkHttp 层强制代理
    safe("OkHttpClient.Builder.proxy", function () {
        var Proxy = Java.use("java.net.Proxy");
        var ProxyType = Java.use("java.net.Proxy$Type");
        var InetSocketAddress = Java.use("java.net.InetSocketAddress");
        var addr = InetSocketAddress.$new(PROXY_HOST, PROXY_PORT);
        var B = Java.use("okhttp3.OkHttpClient$Builder");
        if (B.proxy) {
            B.proxy.overloads.forEach(function (ov) {
                ov.implementation = function (p) {
                    log("OkHttp proxy overridden");
                    return this.proxy(Proxy.$new(ProxyType.HTTP.value, addr));
                };
            });
        }
    });

    // 系统属性层
    safe("System.getProperty proxy", function () {
        var System = Java.use("java.lang.System");
        System.getProperty.overload("java.lang.String").implementation = function (k) {
            if (k === "http.proxyHost" || k === "https.proxyHost") return PROXY_HOST;
            if (k === "http.proxyPort" || k === "https.proxyPort") return String(PROXY_PORT);
            return this.getProperty(k);
        };
        System.getProperty.overload("java.lang.String", "java.lang.String").implementation = function (k, d) {
            if (k === "http.proxyHost" || k === "https.proxyHost") return PROXY_HOST;
            if (k === "http.proxyPort" || k === "https.proxyPort") return String(PROXY_PORT);
            return this.getProperty(k, d);
        };
    });
}

/* ---------- 3. native 层 unpinning ---------- */

function unpinNative() {
    var targets = [
        "SSL_CTX_set_verify",
        "SSL_CTX_set_custom_verify",
        "SSL_get_verify_result",
        "X509_verify_cert",
        "ssl_verify_cert_chain",
        "SSL_CTX_set_verify_depth",
        "SSL_CTX_set_cert_verify_callback"
    ];
    var libs = ["libssl.so", "libcrypto.so", "libconscrypt_jni.so", "libjavacrypto.so"];
    libs.forEach(function (lib) {
        var m = null;
        try { m = Process.findModuleByName(lib); } catch (e) { }
        if (!m) return;
        targets.forEach(function (sym) {
            var p = null;
            try { p = Module.findExportByName(lib, sym); } catch (e) { }
            if (!p) return;
            try {
                Interceptor.attach(p, {
                    onEnter: function (args) {
                        log("native " + lib + "!" + sym + " called");
                        if (sym === "SSL_CTX_set_verify") {
                            args[1] = ptr(0); // SSL_VERIFY_NONE
                            args[2] = NULL;
                        }
                    },
                    onLeave: function (ret) {
                        if (sym === "SSL_get_verify_result" || sym === "X509_verify_cert") {
                            ret.replace(ptr(1)); // X509_V_OK
                        }
                    }
                });
            } catch (e) { }
        });
    });

    // 兜底：全局搜索已加载模块中的校验符号
    Process.enumerateModules().forEach(function (mod) {
        if (!/\.so$/.test(mod.name)) return;
        ["SSL_CTX_set_custom_verify", "ssl_verify_cert_chain"].forEach(function (sym) {
            var p = null;
            try { p = Module.findExportByName(mod.name, sym); } catch (e) { }
            if (!p) return;
            try {
                Interceptor.attach(p, {
                    onEnter: function () { log("native " + mod.name + "!" + sym); },
                    onLeave: function (ret) { if (sym === "ssl_verify_cert_chain") ret.replace(ptr(1)); }
                });
            } catch (e) { }
        });
    });
}

/* ---------- 4. 启动 ---------- */

Java.perform(function () {
    log("=== xhs capture agent start ===");
    unpinJava();
    if (FORCE_PROXY) forceProxy();
    unpinNative();
    log("=== agent ready ===");
});
