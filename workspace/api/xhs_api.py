#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xhs_api.py — 小红书搜索/原图 HTTP API 服务（Flask 实现）

选择 Flask 的原因：环境内 fastapi 0.121.1 与 starlette 1.6.0 不兼容
（starlette 1.x 移除了 on_startup 参数），Flask 3.0.0 独立无冲突。

启动:
  python xhs_api.py                        # 默认 0.0.0.0:8700
  python xhs_api.py --port 8700 --host 127.0.0.1

自述文档:
  http://127.0.0.1:8700/docs

端点:
  GET  /                         服务信息
  GET  /health                   环境健康检查
  GET  /docs                     接口文档
  POST /v1/search                同步搜索
  POST /v1/search/async          异步搜索 -> task_id
  GET  /v1/tasks/<task_id>       查询异步任务
  GET  /v1/original?url=&mode=   单 URL 转原图
  POST /v1/original/batch        批量转原图
  GET  /v1/image?url=&mode=      图片字节流
  POST /v1/download              搜索并下载原图
"""
import argparse
import glob
import os
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from flask import Flask, Response, jsonify, request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from xhs_core import XhsEngine, XhsError, MODE_SUFFIX  # noqa: E402
from xhs_web import XhsWeb, WebError  # noqa: E402

API_VERSION = "2.0.0"

# 引擎选择：
#   web    —— 浏览器签名（默认）。不需要模拟器/adb/抓包，只需一次扫码登录。
#   device —— 设备驱动。需要雷电模拟器 + adb + mitmproxy，但无需登录。
ENGINE_KIND = os.environ.get("XHS_ENGINE", "web").strip().lower()
if ENGINE_KIND not in ("web", "device"):
    ENGINE_KIND = "web"

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False

_engine: Optional[Any] = None
_engine_lock = threading.Lock()
_tasks: Dict[str, Dict[str, Any]] = {}
_tasks_lock = threading.Lock()
MAX_TASKS = 200


def get_engine():
    """按 ENGINE_KIND 惰性初始化引擎；两者对外接口保持一致"""
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                if ENGINE_KIND == "device":
                    _engine = XhsEngine()
                else:
                    _engine = XhsWeb()
    return _engine


# ------------------------------------------------------------------ 公共

@app.after_request
def _cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    return resp


@app.route("/v1/<path:_p>", methods=["OPTIONS"])
@app.route("/health", methods=["OPTIONS"])
def _preflight(_p=None):
    return ("", 204)


def err(msg, code=400):
    return jsonify({"detail": msg, "status": code}), code


def body() -> Dict[str, Any]:
    return request.get_json(silent=True) or {}


def arg_int(name, default, lo=None, hi=None):
    try:
        v = int(request.args.get(name, default))
    except (TypeError, ValueError):
        v = default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def mode_guard(mode):
    if mode not in MODE_SUFFIX:
        return err("mode 必须是 %s 之一" % "/".join(MODE_SUFFIX))
    return None


def resolve_keyword(b):
    """
    解析关键词，支持 keyword 与 keyword_b64 两种入口。

    非 UTF-8 客户端（如 PowerShell 5.1 的 Invoke-RestMethod、部分 curl 构建）
    发送中文时会把字符退化成 '?'，导致搜到错误内容却不报错。
    这里主动识别该事故并给出明确提示，同时提供 base64 入口彻底规避。
    """
    kw = (b.get("keyword") or "")
    if isinstance(kw, str):
        kw = kw.strip()
    else:
        kw = ""

    b64 = b.get("keyword_b64")
    if b64:
        import base64
        try:
            kw = base64.b64decode(b64).decode("utf-8").strip()
        except Exception:
            return None, err("keyword_b64 不是合法的 base64 编码 UTF-8 文本")

    if not kw:
        return None, err("keyword 不能为空")

    # 编码事故检测：内容退化为一串 '?' 说明客户端已丢失原文
    if len(kw) >= 2 and set(kw) == {"?"}:
        return None, err(
            "keyword 疑似编码丢失（服务端收到 %r）。"
            "客户端需以 UTF-8 发送 JSON；"
            "若无法保证，请改用 keyword_b64 = base64(UTF-8 关键词)" % kw, 400)

    return kw, None


# ------------------------------------------------------------------ 端点

@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "service": "xhs-search-api",
        "api_version": API_VERSION,
        "engine_kind": ENGINE_KIND,
        "engine": ("web-playwright" if ENGINE_KIND == "web"
                   else "device-adb-mitmproxy"),
        "endpoints": [
            "GET  /health",
            "GET  /docs",
            "POST /v1/search",
            "POST /v1/search/async",
            "GET  /v1/tasks/<task_id>",
            "GET  /v1/original?url=&mode=",
            "POST /v1/original/batch",
            "GET  /v1/image?url=&mode=",
            "POST /v1/download",
            "POST /v1/relogin",
            "GET  /v1/relogin/status",
            "GET  /v1/relogin/qr",
        ],
        "modes": list(MODE_SUFFIX.keys()),
        "note": ("web 引擎：浏览器自动签名，无需模拟器，需一次扫码登录；"
                 "device 引擎：需雷电模拟器与抓包代理，无需登录"),
    })


# ---------------------------------------------------------- 引擎适配层
# XhsEngine（设备）与 XhsWeb（浏览器）方法签名不同，这里统一收敛，
# 上层端点只调 do_search / do_download，与引擎无关。

def _auth_error(msg):
    """把登录失效转成带重新登录指引的错误响应"""
    return jsonify({
        "status": 401,
        "detail": msg,
        "relogin": "POST /v1/relogin 获取新二维码，或运行 python login_live.py 900",
    }), 401


def do_search(eng, keyword, pages=0, mode="png", timeout=45,
              use_cache=True, restart_app=False, retries=1):
    if ENGINE_KIND == "device":
        return eng.search(keyword, pages, mode, timeout,
                          use_cache, restart_app, retries)
    # web 引擎：pages 表示额外加载的滚动次数，至少加载首屏
    res = eng.search(keyword, pages=max(1, min(6, pages + 1)), mode=mode,
                     wait=max(12, min(int(timeout), 45)), use_cache=use_cache)
    res["engine"] = "web-playwright"
    return res


def do_download(eng, keyword=None, urls=None, pages=0, mode="png",
                out_dir=None, workers=6):
    if ENGINE_KIND == "device":
        return eng.download(keyword=keyword, urls=urls, pages=pages,
                            mode=mode, out_dir=out_dir, workers=workers)
    return eng.download(keyword=keyword, urls=urls, mode=mode,
                        pages=max(1, min(6, pages + 1)),
                        out_dir=out_dir, workers=workers)


def _sniff_ctype(blob):
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if blob[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return "image/webp"
    if blob[:4] == b"GIF8":
        return "image/gif"
    if b"ftyp" in blob[:32]:
        return "image/heic"
    return "application/octet-stream"


def fetch_image_compat(eng, url, mode):
    """XhsEngine 返回 (bytes, ctype)，XhsWeb 返回 bytes；统一成前者"""
    if ENGINE_KIND == "device":
        return eng.fetch_image(url, mode)
    blob = eng.fetch_image(url, mode)
    return blob, _sniff_ctype(blob)


@app.route("/health", methods=["GET"])
def health():
    try:
        h = get_engine().health()
        h["engine_kind"] = ENGINE_KIND
        h["api_version"] = API_VERSION
        return jsonify(h)
    except Exception as e:
        return err("健康检查失败: %s" % e, 500)


@app.route("/v1/search", methods=["POST"])
def search():
    b = body()
    keyword, e = resolve_keyword(b)
    if e:
        return e
    mode = b.get("mode", "png")
    g = mode_guard(mode)
    if g:
        return g
    eng = get_engine()
    try:
        res = do_search(
            eng, keyword,
            max(0, min(10, int(b.get("pages", 0) or 0))),
            mode,
            max(10, min(180, int(b.get("timeout", 45) or 45))),
            bool(b.get("use_cache", True)),
            bool(b.get("restart_app", False)),
            max(0, min(5, int(b.get("retries", 1) or 0))),
        )
        res["engine_kind"] = ENGINE_KIND
        return jsonify(res)
    except WebError as e:
        return _auth_error(str(e))
    except XhsError as e:
        return err(str(e), 502)
    except Exception as e:
        return err("搜索失败: %s" % e, 500)


def _prune_tasks():
    if len(_tasks) <= MAX_TASKS:
        return
    old = sorted(_tasks.values(), key=lambda t: t.get("created", 0))
    for t in old[:MAX_TASKS // 2]:
        _tasks.pop(t["task_id"], None)


def _spawn(kind, fn, *a, **kw):
    with _tasks_lock:
        _prune_tasks()
        tid = uuid.uuid4().hex[:16]
        _tasks[tid] = {"task_id": tid, "kind": kind, "status": "pending",
                       "created": time.time()}
    def run():
        _tasks[tid]["status"] = "running"
        _tasks[tid]["started"] = time.time()
        try:
            _tasks[tid]["result"] = fn(*a, **kw)
            _tasks[tid]["status"] = "done"
        except Exception as e:
            _tasks[tid]["status"] = "error"
            _tasks[tid]["error"] = str(e)
        finally:
            _tasks[tid]["finished"] = time.time()
    threading.Thread(target=run, daemon=True).start()
    return tid


@app.route("/v1/search/async", methods=["POST"])
def search_async():
    b = body()
    keyword, e = resolve_keyword(b)
    if e:
        return e
    mode = b.get("mode", "png")
    g = mode_guard(mode)
    if g:
        return g
    eng = get_engine()
    tid = _spawn("search", do_search, eng, keyword,
                 max(0, min(10, int(b.get("pages", 0) or 0))), mode,
                 max(10, min(180, int(b.get("timeout", 45) or 45))),
                 bool(b.get("use_cache", True)), bool(b.get("restart_app", False)),
                 max(0, min(5, int(b.get("retries", 1) or 0))))
    return jsonify({"task_id": tid, "status": "pending",
                    "poll": "/v1/tasks/%s" % tid}), 202


@app.route("/v1/tasks/<task_id>", methods=["GET"])
def get_task(task_id):
    t = _tasks.get(task_id)
    if not t:
        return err("task 不存在", 404)
    return jsonify(t)


@app.route("/v1/original", methods=["GET"])
def original():
    url = request.args.get("url", "")
    if not url:
        return err("缺少 url 参数")
    mode = request.args.get("mode", "png")
    g = mode_guard(mode)
    if g:
        return g
    return jsonify({"input": url, "mode": mode,
                    "original": get_engine().to_original(url, mode)})


@app.route("/v1/original/batch", methods=["POST"])
def original_batch():
    b = body()
    urls: List[str] = b.get("urls") or []
    if not isinstance(urls, list) or not urls:
        return err("urls 必须是非空数组")
    mode = b.get("mode", "png")
    g = mode_guard(mode)
    if g:
        return g
    eng = get_engine()
    return jsonify({"mode": mode, "count": len(urls),
                    "original": [eng.to_original(u, mode) for u in urls]})


@app.route("/v1/image", methods=["GET"])
def image():
    url = request.args.get("url", "")
    if not url:
        return err("缺少 url 参数")
    mode = request.args.get("mode")
    if mode:
        g = mode_guard(mode)
        if g:
            return g
    try:
        data, ctype = fetch_image_compat(get_engine(), url, mode)
    except XhsError as e:
        return err(str(e), 502)
    except Exception as e:
        return err("取图失败: %s" % e, 500)
    return Response(data, mimetype=ctype or "image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.route("/v1/download", methods=["POST"])
def download():
    b = body()
    keyword, e = resolve_keyword(b)
    if e:
        return e
    mode = b.get("mode", "png")
    g = mode_guard(mode)
    if g:
        return g
    eng = get_engine()
    pages = max(0, min(10, int(b.get("pages", 0) or 0)))
    workers = max(1, min(16, int(b.get("workers", 6) or 6)))
    out = b.get("out_dir") or os.path.join(
        r"D:\PYxiangmu\WKnx\workspace\downloads", "api-" + keyword)
    try:
        data = do_search(eng, keyword, pages, mode,
                         max(10, min(180, int(b.get("timeout", 45) or 45))),
                         True, False,
                         max(0, min(5, int(b.get("retries", 1) or 0))))
        if data["image_count"] == 0:
            return err("搜索未取得任何图片（keyword=%s）。web 引擎请查 /health "
                       "确认登录态；device 引擎请确认模拟器与抓包正常。" % keyword, 502)
        stats = do_download(eng, keyword=keyword, urls=data["images"],
                            pages=pages, mode=mode, out_dir=out, workers=workers)
    except WebError as e:
        return _auth_error(str(e))
    except XhsError as e:
        return err(str(e), 502)
    except Exception as e:
        return err("下载失败: %s" % e, 500)
    return jsonify({
        "keyword": keyword,
        "note_count": data["note_count"],
        "image_count": data["image_count"],
        "attempts": data.get("attempts"),
        "engine_kind": ENGINE_KIND,
        "out_dir": stats.get("out_dir", out),
        "ok": stats.get("ok", 0),
        "skip": stats.get("skip", 0),
        "fail": stats.get("fail", 0),
        "bytes": stats.get("bytes", 0),
        "errors": (stats.get("fail_detail") or stats.get("errors") or [])[:10],
    })


@app.route("/v1/relogin", methods=["POST"])
def relogin():
    """
    重新扫码登录（cookie 失效后使用）。

    返回二维码的 base64 PNG，前端 <img src="data:image/png;base64,..."> 即可展示。
    登录成功后再查 /v1/relogin/status 拿 user_id。
    """
    if ENGINE_KIND != "web":
        return err("当前为 device 引擎，无需扫码登录（App 自身已是登录态）", 400)
    b = body()
    timeout = max(60, min(900, int(b.get("timeout", 300) or 300)))
    try:
        st = get_engine().start_relogin(timeout=timeout)
    except Exception as e:
        return err("发起登录失败: %s" % e, 500)

    payload = {
        "status": st.get("status"),
        "qr_url": st.get("qr_url"),
        "has_qr": bool(st.get("qr_png")),
        "user_id": st.get("user_id"),
        "error": st.get("error"),
        "poll": "/v1/relogin/status",
        "hint": "用小红书 App 扫描 qr_png；随后轮询 /v1/relogin/status",
    }
    if b.get("include_image", True) and st.get("qr_png"):
        payload["qr_png"] = st["qr_png"]
        payload["qr_png_data_uri"] = "data:image/png;base64," + st["qr_png"]
    return jsonify(payload), (200 if st.get("status") != "error" else 502)


@app.route("/v1/relogin/status", methods=["GET"])
def relogin_status():
    if ENGINE_KIND != "web":
        return err("当前为 device 引擎，无扫码登录流程", 400)
    st = get_engine().relogin_status()
    # 不重复吐出大体积图片
    st.pop("qr_png", None)
    st["poll"] = "/v1/relogin/status"
    return jsonify(st)


@app.route("/v1/relogin/qr", methods=["GET"])
def relogin_qr():
    """二维码原始 PNG 字节流，可直接作 <img src>"""
    if ENGINE_KIND != "web":
        return err("当前为 device 引擎，无扫码登录流程", 400)
    import base64 as _b64
    st = get_engine().relogin_status()
    png = st.get("qr_png")
    if not png:
        # 若尚未发起，则自动发起一次
        st = get_engine().start_relogin()
        png = st.get("qr_png")
    if not png:
        return err("二维码尚未就绪（status=%s）" % st.get("status"), 409)
    return Response(_b64.b64decode(png), mimetype="image/png",
                    headers={"Cache-Control": "no-store"})


@app.route("/docs", methods=["GET"])
def docs():
    return Response(_DOCS_HTML, mimetype="text/html")


_DOCS_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>小红书搜索/原图 API</title>
<style>
 body{font:14px/1.7 -apple-system,"Segoe UI",sans-serif;max-width:960px;
      margin:40px auto;padding:0 20px;color:#222;background:#faf9f7}
 h1{border-bottom:2px solid #ff2442;padding-bottom:10px}
 h2{margin-top:34px;color:#ff2442}
 code{background:#f0eeea;padding:2px 6px;border-radius:4px;font-size:13px}
 pre{background:#282c34;color:#abb2bf;padding:14px;border-radius:8px;overflow:auto}
 table{border-collapse:collapse;width:100%;margin:12px 0}
 th,td{border:1px solid #ddd;padding:8px 10px;text-align:left}
 th{background:#f5f3f0}
 .m{font-weight:700;color:#0a7d28}
 .g{font-weight:700;color:#b8860b}
 .p{font-weight:700;color:#a11}
</style></head><body>
<h1>小红书搜索 / 原图 API</h1>
<p>双引擎服务，默认 <b>web 引擎</b>：由浏览器页面自行生成 x-s 签名，
<b>不需要模拟器、adb 或抓包代理</b>，只需一次扫码登录（cookie 持久保存）。</p>
<p>切换设备引擎：设置环境变量 <code>XHS_ENGINE=device</code>，
此时走 deeplink 触发 + 抓包解析，需雷电模拟器常驻，但无需登录。</p>

<h2>端点总览</h2>
<table>
<tr><th>方法</th><th>路径</th><th>说明</th></tr>
<tr><td class="g">GET</td><td><code>/health</code></td><td>环境健康检查</td></tr>
<tr><td class="p">POST</td><td><code>/v1/search</code></td><td>同步搜索（阻塞 20-40s）</td></tr>
<tr><td class="p">POST</td><td><code>/v1/search/async</code></td><td>异步搜索，返回 task_id</td></tr>
<tr><td class="g">GET</td><td><code>/v1/tasks/&lt;task_id&gt;</code></td><td>查询异步任务</td></tr>
<tr><td class="g">GET</td><td><code>/v1/original?url=&amp;mode=</code></td><td>单 URL 转原图</td></tr>
<tr><td class="m">POST</td><td><code>/v1/original/batch</code></td><td>批量转原图</td></tr>
<tr><td class="g">GET</td><td><code>/v1/image?url=&amp;mode=</code></td><td>图片字节流，可直接 img src</td></tr>
<tr><td class="p">POST</td><td><code>/v1/download</code></td><td>搜索并下载到服务端</td></tr>
<tr><td class="p">POST</td><td><code>/v1/relogin</code></td><td>重新扫码登录，返回二维码 base64</td></tr>
<tr><td class="g">GET</td><td><code>/v1/relogin/status</code></td><td>查询扫码登录进度</td></tr>
<tr><td class="g">GET</td><td><code>/v1/relogin/qr</code></td><td>二维码原始 PNG，可直接 img src</td></tr>
</table>

<h2>cookie 失效后如何重新登录</h2>
<p>搜索返回 <b>HTTP 401</b> 且 detail 提示"登录态已失效"时，说明 cookie 过期：</p>
<pre># 1) 取二维码
curl -X POST http://127.0.0.1:8700/v1/relogin -H "Content-Type: application/json" -d '{}'
# 响应里的 qr_png_data_uri 可直接塞进 &lt;img src&gt;

# 2) 扫码后查进度
curl http://127.0.0.1:8700/v1/relogin/status
# status=done 即成功，同时返回 user_id</pre>
<p>也可以直接在浏览器打开 <code>http://127.0.0.1:8700/v1/relogin/qr</code> 看二维码。</p>
<p>命令行方式：<code>python login_live.py 900</code>（打印 ASCII 二维码，每 90 秒自动刷新）。</p>

<h2>mode 参数</h2>
<table>
<tr><th>值</th><th>说明</th><th>体积比</th></tr>
<tr><td><code>png</code></td><td>无损 PNG，原始像素</td><td>~34x</td></tr>
<tr><td><code>jpg100</code></td><td>最高质量 JPEG</td><td>~21x</td></tr>
<tr><td><code>jpg</code></td><td>CDN 默认质量</td><td>~4.4x</td></tr>
<tr><td><code>raw</code></td><td>原始编码</td><td>不定</td></tr>
</table>

<h2>客户端编码（重要）</h2>
<p>中文关键词必须以 <b>UTF-8</b> 发送。部分客户端（PowerShell 5.1 的
<code>Invoke-RestMethod</code>、某些 curl 构建）默认用非 UTF-8 编码 body，
中文会退化成 <code>?</code>，导致搜到错误内容。</p>
<p>服务端会识别这种退化并返回 400 明确报错。若客户端编码不可控，
改用 <code>keyword_b64</code>（关键词的 base64(UTF-8)）：</p>
<pre># Python
requests.post(url, json={"keyword_b64": base64.b64encode("猫咪".encode()).decode()})

# PowerShell 正确写法
$b = [System.Text.Encoding]::UTF8.GetBytes('{"keyword":"猫咪"}')
Invoke-RestMethod -Body $b -ContentType "application/json; charset=utf-8"</pre>

<h2>示例</h2>
<pre>curl -X POST http://127.0.0.1:8700/v1/search \\
  -H "Content-Type: application/json" \\
  -d '{"keyword":"猫咪","pages":1,"mode":"png"}'</pre>

<pre>curl -X POST http://127.0.0.1:8700/v1/download \\
  -H "Content-Type: application/json" \\
  -d '{"keyword":"咖啡","mode":"jpg100","workers":8}'</pre>

<pre># 纯字符串转换，不需要设备
curl "http://127.0.0.1:8700/v1/original?mode=png&amp;url=https://sns-na-i4.xhscdn.com/notes_pre_post/xxx?imageView2/2/w/576"</pre>

<pre># 前端可直接引用
&lt;img src="http://127.0.0.1:8700/v1/image?mode=png&amp;url=..."&gt;</pre>

<h2>返回结构（/v1/search）</h2>
<pre>{
  "keyword": "猫咪",
  "notes": [
    {"note_id":"...","title":"...","user":"...","images":[{"base":"...","width":1920,"height":2560}]}
  ],
  "images": ["https://...?imageView2/2/format/png"],
  "note_count": 15,
  "image_count": 67,
  "elapsed": 24.3,
  "cached": false
}</pre>
</body></html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8700)
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    print("=" * 62)
    print("小红书搜索/原图 API  (Flask)")
    print("  监听: http://%s:%d" % (args.host, args.port))
    print("  文档: http://127.0.0.1:%d/docs" % args.port)
    print("=" * 62)

    # 预热：确保抓包器在跑
    try:
        get_engine().ensure_mitm()
        print("[init] mitm 抓包器就绪")
    except Exception as e:
        print("[init] mitm 启动告警: %s" % e)

    app.run(host=args.host, port=args.port, threaded=True,
            debug=args.debug, use_reloader=False)


if __name__ == "__main__":
    main()
