#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xhs_web.py — 小红书 Web 引擎（Playwright，摆脱模拟器与签名逆向）

原理：
  网页 JS 会自行生成 x-s / x-t / x-s-common 签名，
  无需逆向即可通过服务端签名校验；只需一次扫码登录取得 cookie。
  之后所有请求由浏览器上下文自动签名，我们只拦截 XHR 结果。

相比模拟器方案：
  * 不需要雷电/ADB/抓包代理，资源占用低一个数量级
  * 部署到服务器后，调用方只需 HTTP，零前置依赖

用法：
    from xhs_web import XhsWeb
    w = XhsWeb()
    w.login()                 # 首次：打印二维码路径，扫码
    print(w.status())         # 检查登录态
    d = w.search("猫咪")       # 搜索
"""
import base64
import io
import json
import os
import re
import sys
import time
from contextlib import contextmanager

def _utf8_stdout():
    """非破坏性地让 stdout 支持中文；可安全 import、可重复调用"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


DEFAULT_PROFILE = r"D:\PYxiangmu\WKnx\workspace\chrome-profile-xhs"
DEFAULT_OUT = r"D:\PYxiangmu\WKnx\workspace\capture"
PROXY = "http://127.0.0.1:7897"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

IMG_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+\.(?:xhscdn\.com|xhscdn\.net)/", re.I)

# 原图 CDN 主机（可直接用 fileid 取原图，无需签名）
ORIGIN_HOST = "sns-na-i1.xhscdn.com"

# web 响应里图片 URL 的形态：
#   http://sns-webpic-qc.xhscdn.com/<时间戳>/<哈希>/<前缀>/<fileid>!nc_n_webp_mw_1
# 其中 <前缀>/<fileid> 才是真正的资源路径，! 后缀把资源钉在 webp 转码上，
# 必须剥离后才能通过 imageView2 取回原图。
_URL_PARTS_RE = re.compile(
    r"^https?://[^/]+/(?:[0-9]{12}/[0-9a-f]{32}/)?(.+?)(?:![^!]*)?(?:\?.*)?$", re.I)


def extract_fileid(url):
    """
    从 xhscdn 图片 URL 或裸 fileid 提取可用资源路径。

    URL 例：
      http://sns-webpic-qc.xhscdn.com/202609120210/<hash>/notes_pre_post/1040g3k0...!nc_n_webp_mw_1
      -> notes_pre_post/1040g3k0...

    裸 fileid 例：
      oss-ae/notes_pre_post/1040g3v8...   -> 原样返回
    """
    if not url:
        return ""
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        # 已经是 fileid，仅剥离转码后缀与 query
        return url.split("!")[0].split("?", 1)[0]
    m = _URL_PARTS_RE.match(url)
    return m.group(1) if m else ""


class WebError(Exception):
    pass


class XhsWeb:
    def __init__(self, profile=DEFAULT_PROFILE, out_dir=DEFAULT_OUT,
                 proxy=PROXY, headless=True, slow_mo=0, cache_ttl=300):
        self.profile = profile
        self.out = out_dir
        self.proxy = proxy
        self.headless = headless
        self.slow_mo = slow_mo
        # 每次搜索都要冷启动浏览器（约 15-25 秒），同关键词短期内直接命中缓存
        self.cache_ttl = cache_ttl
        self._cache = {}
        self._cache_lock = __import__("threading").Lock()
        # 浏览器实例无法安全并发复用，且并发冷启动会互相抢占资源，
        # 因此同一时刻只允许一个搜索在跑（Flask 默认为多线程）。
        self._run_lock = __import__("threading").Lock()
        # 后台重新登录状态（cookie 失效时用）
        self._login = {"status": "idle", "qr_png": None, "qr_url": None,
                       "user_id": None, "error": None, "started": None,
                       "finished": None}
        self._login_lock = __import__("threading").Lock()
        os.makedirs(self.profile, exist_ok=True)
        os.makedirs(self.out, exist_ok=True)

    # ------------------------------------------------ 缓存

    def _cache_get(self, key):
        if self.cache_ttl <= 0:
            return None
        with self._cache_lock:
            hit = self._cache.get(key)
        if not hit:
            return None
        ts, val = hit
        if time.time() - ts > self.cache_ttl:
            with self._cache_lock:
                self._cache.pop(key, None)
            return None
        return val

    def _cache_put(self, key, val):
        if self.cache_ttl <= 0:
            return
        with self._cache_lock:
            self._cache[key] = (time.time(), val)
            # 简单容量控制
            if len(self._cache) > 64:
                oldest = sorted(self._cache.items(), key=lambda kv: kv[1][0])
                for k, _ in oldest[:32]:
                    self._cache.pop(k, None)

    # ------------------------------------------------ 浏览器生命周期

    @contextmanager
    def _browser(self, blocking=True):
        """
        持久化上下文：cookie 自动落盘，跨进程复用登录态。

        blocking=False 时若已有搜索在跑则直接放弃（用于 /health 探活，
        避免被正在进行的搜索阻塞几十秒）。
        """
        from playwright.sync_api import sync_playwright

        if not self._run_lock.acquire(blocking=blocking):
            yield None
            return
        pw = None
        try:
            pw = sync_playwright().start()
            kwargs = dict(
                user_data_dir=self.profile,
                headless=self.headless,
                slow_mo=self.slow_mo,
                viewport={"width": 1440, "height": 900},
                locale="zh-CN",
                user_agent=UA,
                args=["--disable-blink-features=AutomationControlled",
                      "--no-sandbox", "--disable-dev-shm-usage"],
            )
            if self.proxy:
                kwargs["proxy"] = {"server": self.proxy}
            try:
                ctx = pw.chromium.launch_persistent_context(channel="chrome", **kwargs)
            except Exception:
                ctx = pw.chromium.launch_persistent_context(**kwargs)
            try:
                yield ctx
            finally:
                try:
                    ctx.close()
                except Exception:
                    pass
        finally:
            if pw is not None:
                try:
                    pw.stop()
                except Exception:
                    pass
            self._run_lock.release()

    # ------------------------------------------------ 登录态

    def status(self):
        """检查登录态：监听页面自然发出的 user/me 请求"""
        captured = {}

        with self._browser(blocking=False) as ctx:
            if ctx is None:
                return {"logged_in": False, "busy": True,
                        "error": "引擎正忙（有搜索进行中），稍后重试"}
            page = ctx.new_page()

            def on_resp(resp):
                if "/user/me" in resp.url and "user/me" not in captured:
                    try:
                        if "json" in resp.headers.get("content-type", ""):
                            captured["user/me"] = resp.text()
                    except Exception:
                        pass

            page.on("response", on_resp)
            try:
                page.goto("https://www.xiaohongshu.com/explore",
                          wait_until="domcontentloaded", timeout=60000)
                t0 = time.time()
                # 页面初始不主动请求 user/me，需要交互触发
                while time.time() - t0 < 25 and "user/me" not in captured:
                    time.sleep(2)
                    try:
                        page.mouse.wheel(0, 2500)
                    except Exception:
                        pass
                time.sleep(1)
            except Exception as e:
                return {"logged_in": False, "error": str(e)[:200]}
            finally:
                page.close()

        txt = captured.get("user/me")
        if not txt:
            return {"logged_in": False, "error": "未捕获到 user/me 响应"}
        try:
            j = json.loads(txt)
        except Exception as e:
            return {"logged_in": False, "error": "解析失败: %s" % e}
        data = j.get("data") or {}
        # 关键：游客态返回 guest=true 且无昵称，不能只看 code==0
        is_guest = bool(data.get("guest"))
        ok = bool(j.get("success")) and j.get("code") == 0 and not is_guest
        user = data.get("user") or {}
        return {
            "logged_in": ok,
            "guest": is_guest,
            "code": j.get("code"),
            "msg": j.get("msg"),
            "user_id": data.get("user_id") or user.get("user_id"),
            "nickname": user.get("nickname") or user.get("nick_name"),
        }

    def login(self, timeout=300, poll=2, on_qr=None):
        """
        扫码登录（有头模式最佳）：
        打开首页 -> 页面自动请求二维码 -> 取图保存 -> 轮询直至登录成功。
        登录态由持久化 profile 自动保存，后续无需再扫。

        on_qr(qr_url, qr_path) 会在二维码就绪时回调，供服务端把二维码
        交给调用方（用于 cookie 失效后的远程重新登录）。
        """
        captured = {}

        with self._browser() as ctx:
            page = ctx.new_page()

            def on_resp(resp):
                u = resp.url
                if "qrcode/create" in u and "qr_create" not in captured:
                    try:
                        if "json" in resp.headers.get("content-type", ""):
                            captured["qr_create"] = resp.text()
                    except Exception:
                        pass
                if "qrcode/status" in u:
                    try:
                        if "json" in resp.headers.get("content-type", ""):
                            captured["qr_status"] = resp.text()
                    except Exception:
                        pass
                if "/user/me" in u:
                    try:
                        if "json" in resp.headers.get("content-type", ""):
                            captured["user_me"] = resp.text()
                    except Exception:
                        pass

            page.on("response", on_resp)

            page.goto("https://www.xiaohongshu.com/explore",
                      wait_until="domcontentloaded", timeout=60000)

            # 触发登录框
            t0 = time.time()
            while time.time() - t0 < 25 and "qr_create" not in captured:
                time.sleep(1)
                try:
                    if page.locator("text=登录").count() > 0:
                        page.locator("text=登录").first.click(timeout=3000)
                except Exception:
                    pass

            qr_path = None
            qr_txt = captured.get("qr_create")
            qr_url = ""
            if qr_txt:
                try:
                    j = json.loads(qr_txt)
                    d = j.get("data") or {}
                    qr_url = d.get("url") or ""
                    if qr_url:
                        # 接口只给二维码内容，图片由本地生成，最可靠
                        import qrcode
                        img = qrcode.make(qr_url)
                        qr_path = os.path.join(self.out, "web_qrcode.png")
                        img.save(qr_path)
                        print("  二维码内容: %s" % qr_url[:120])
                except Exception as e:
                    print("  [login] 生成二维码失败: %s" % str(e)[:120])

            # 兜底：截取页面上的二维码元素
            if not qr_path:
                try:
                    page.locator("img[src*='data:image']").last.screenshot(
                        path=os.path.join(self.out, "web_qrcode.png"))
                    qr_path = os.path.join(self.out, "web_qrcode.png")
                except Exception:
                    try:
                        qr_path = os.path.join(self.out, "web_qrcode_full.png")
                        page.screenshot(path=qr_path)
                    except Exception:
                        qr_path = None

            print("  二维码已保存: %s" % qr_path)
            print("  请用小红书 App 扫码（等待 %d 秒）..." % timeout)

            if on_qr:
                try:
                    on_qr(qr_url if qr_txt else "", qr_path)
                except Exception:
                    pass

            # 轮询登录结果：以 guest 字段为准
            while time.time() - t0 < timeout:
                time.sleep(poll)
                um = captured.get("user_me")
                if um:
                    try:
                        j = json.loads(um)
                        data = j.get("data") or {}
                        if j.get("success") and j.get("code") == 0 and not data.get("guest"):
                            user = data.get("user") or {}
                            nick = user.get("nickname") or user.get("nick_name")
                            print("  登录成功: %s (user_id=%s)" % (nick, data.get("user_id")))
                            time.sleep(2)
                            return {"ok": True, "qrcode": qr_path, "nickname": nick,
                                    "user_id": data.get("user_id"),
                                    "elapsed": round(time.time() - t0, 1)}
                    except Exception:
                        pass
                st = captured.get("qr_status")
                if st:
                    try:
                        sj = json.loads(st)
                        cs = (sj.get("data") or {}).get("code_status")
                        if cs not in (None, 0, "0"):
                            print("  [login] 扫码状态 code_status=%s (%ds)"
                                  % (cs, int(time.time() - t0)))
                    except Exception:
                        pass
            return {"ok": False, "error": "登录超时", "qrcode": qr_path,
                    "elapsed": round(time.time() - t0, 1)}

    # ------------------------------------------------ 后台重新登录

    def start_relogin(self, timeout=300, wait_qr=90):
        """
        在后台线程发起扫码登录，并把二维码交给调用方。

        cookie 失效后由服务端自动/手动触发，调用方拿到 qr_png(base64)
        展示给用户扫描即可，无需登录服务器操作终端。

        返回 {status, qr_png, qr_url, user_id, error, elapsed}
        """
        import threading

        with self._login_lock:
            if self._login.get("status") in ("starting", "running"):
                snap = dict(self._login)
                snap["reused"] = True
                if snap.get("qr_png") or snap.get("status") == "done":
                    return snap
            else:
                self._login = {
                    "status": "starting", "qr_png": None, "qr_url": None,
                    "user_id": None, "error": None, "cached": None,
                    "started": time.time(), "finished": None,
                }
                threading.Thread(target=self._relogin_worker,
                                 args=(timeout,), daemon=True).start()

        # 等二维码就绪（或流程结束）
        t0 = time.time()
        while time.time() - t0 < wait_qr:
            time.sleep(1)
            with self._login_lock:
                snap = dict(self._login)
            if snap.get("qr_png") or snap.get("status") in ("done", "error"):
                return snap
        with self._login_lock:
            return dict(self._login)

    def _relogin_worker(self, timeout):
        with self._login_lock:
            self._login["status"] = "running"

        def on_qr(qr_url, qr_path):
            png_b64 = None
            try:
                if qr_path and os.path.exists(qr_path):
                    with open(qr_path, "rb") as f:
                        png_b64 = base64.b64encode(f.read()).decode()
            except Exception:
                pass
            with self._login_lock:
                self._login["qr_url"] = qr_url
                self._login["qr_png"] = png_b64

        try:
            res = self.login(timeout=timeout, on_qr=on_qr)
            with self._login_lock:
                if res.get("ok"):
                    self._login["status"] = "done"
                    self._login["user_id"] = res.get("user_id")
                else:
                    self._login["status"] = "error"
                    self._login["error"] = res.get("error") or "登录未完成"
        except Exception as e:
            with self._login_lock:
                self._login["status"] = "error"
                self._login["error"] = str(e)[:300]
        finally:
            with self._login_lock:
                self._login["finished"] = time.time()
            # 登录成功后清掉搜索缓存（旧登录态的搜索结果不再可信）
            with self._cache_lock:
                self._cache.clear()

    def relogin_status(self):
        with self._login_lock:
            snap = dict(self._login)
        if snap.get("started"):
            snap["elapsed"] = round(
                (snap.get("finished") or time.time()) - snap["started"], 1)
        return snap

    # ------------------------------------------------ 搜索

    @staticmethod
    def to_original(url, mode="png"):
        """
        把任意 xhscdn 图片链接转成原图链接。

        实测结论：webpic 域带 !nc_n_webp_mw_1 后缀时参数全部失效（只有 webp 缩略图），
        必须剥离后缀并换到原图 CDN 主机，才能用 imageView2 取回原始分辨率。
        """
        fid = extract_fileid(url)
        if not fid:
            return url
        suffix = {
            "png": "?imageView2/2/format/png",
            "jpg100": "?imageView2/2/w/0/format/jpg/q/100",
            "jpg": "?imageView2/2/format/jpg",
            "raw": "",
        }.get(mode, "?imageView2/2/format/png")
        return "https://%s/%s%s" % (ORIGIN_HOST, fid, suffix)

    def search(self, keyword, pages=1, mode="png", wait=20, scroll_pause=2.5,
               use_cache=True):
        """
        在真实浏览器里搜索并拦截 XHR 结果。

        浏览器自动完成签名，我们只取数据。
        """
        ckey = (keyword, pages, mode)
        if use_cache:
            hit = self._cache_get(ckey)
            if hit:
                out = dict(hit)
                out["cached"] = True
                return out

        captured = []
        auth = {}

        with self._browser() as ctx:
            page = ctx.new_page()

            def on_resp(resp):
                u = resp.url
                # 顺带捕获 user/me：cookie 失效时页面会返 guest=true，
                # 借此零成本判定登录态，无需额外再开一次浏览器
                if "/user/me" in u and "me" not in auth:
                    try:
                        if "json" in resp.headers.get("content-type", ""):
                            auth["me"] = resp.text()
                    except Exception:
                        pass
                if "/api/sns/web" in u and "search" in u and "recommend" not in u:
                    try:
                        if "json" in resp.headers.get("content-type", ""):
                            captured.append({"url": u, "status": resp.status,
                                             "text": resp.text()})
                    except Exception:
                        pass

            page.on("response", on_resp)
            url = ("https://www.xiaohongshu.com/search_result"
                   "?keyword=%s&source=web_explore_feed" % keyword)
            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            t0 = time.time()
            while time.time() - t0 < wait:
                time.sleep(1)
                if captured:
                    break
            # 滚动加载更多页
            for _ in range(max(0, pages)):
                page.mouse.wheel(0, 4000)
                time.sleep(scroll_pause)

            page.close()

        # 登录态判定（供 cookie 失效时给出明确指引）
        auth_state = {"logged_in": None, "guest": None, "user_id": None}
        if auth.get("me"):
            try:
                aj = json.loads(auth["me"])
                ad = aj.get("data") or {}
                auth_state["guest"] = bool(ad.get("guest"))
                auth_state["user_id"] = ad.get("user_id")
                auth_state["logged_in"] = bool(
                    aj.get("success") and aj.get("code") == 0
                    and not ad.get("guest"))
            except Exception:
                pass

        # 无任何结果且确认未登录 -> 明确报错，而不是静默返回空
        if not captured and auth_state.get("logged_in") is False:
            raise WebError(
                "登录态已失效（cookie 过期或被登出）。"
                "请重新扫码：运行 python login_live.py 900，"
                "或在 API 上调用 POST /v1/relogin 获取新二维码。")

        notes, bases = [], []
        seen = set()
        raw_files = []
        for i, c in enumerate(captured):
            try:
                j = json.loads(c["text"])
            except Exception:
                continue
            if not (j.get("success") and j.get("code") == 0):
                continue
            items = ((j.get("data") or {}).get("items") or [])
            for it in items:
                if not isinstance(it, dict):
                    continue
                nc = it.get("note_card") or it.get("note") or {}
                nid = it.get("id") or nc.get("note_id")
                title = nc.get("display_title") or nc.get("title") or ""
                user = ""
                if isinstance(nc.get("user"), dict):
                    user = nc["user"].get("nickname") or nc["user"].get("nick_name") or ""
                images = []
                for im in (nc.get("image_list") or nc.get("images_list") or []):
                    if not isinstance(im, dict):
                        continue
                    # web 端结构：image_list[].info_list[] 内含 WB_DFT / WB_PRV 两套 URL
                    raw = ""
                    infos = im.get("info_list") or []
                    if isinstance(infos, list) and infos:
                        for info in infos:
                            if isinstance(info, dict) and info.get("image_scene") == "WB_DFT":
                                raw = info.get("url") or ""
                                break
                        if not raw:
                            for info in infos:
                                if isinstance(info, dict) and info.get("url"):
                                    raw = info["url"]
                                    break
                    if not raw:
                        raw = (im.get("url_default") or im.get("url_pre")
                               or im.get("url") or im.get("url_size_large") or "")
                    fid = extract_fileid(raw)
                    if fid:
                        images.append({"fileid": fid,
                                       "width": im.get("width"),
                                       "height": im.get("height")})
                        if fid not in seen:
                            seen.add(fid)
                            bases.append(fid)
                if nid or images:
                    notes.append({"note_id": nid, "title": title,
                                  "user": user, "images": images})
            fn = os.path.join(self.out, "web_search_%d.json" % i)
            with open(fn, "w", encoding="utf-8") as f:
                f.write(c["text"])
            raw_files.append(os.path.basename(fn))

        result = {
            "keyword": keyword,
            "mode": mode,
            "notes": notes,
            "images": [self.to_original(b, mode) for b in bases],
            "image_bases": bases,
            "note_count": len(notes),
            "image_count": len(bases),
            "xhr_count": len(captured),
            "source_files": raw_files,
            "engine": "web-playwright",
            "cached": False,
            "logged_in": auth_state.get("logged_in"),
            "guest": auth_state.get("guest"),
            "user_id": auth_state.get("user_id"),
        }
        if notes or bases:
            self._cache_put(ckey, result)
        return result


    def health(self):
        """与 XhsEngine.health() 对齐的探活接口"""
        t0 = time.time()
        st = self.status()
        return {
            "engine": "web-playwright",
            "ready": bool(st.get("logged_in")),
            "logged_in": bool(st.get("logged_in")),
            "guest": st.get("guest"),
            "user_id": st.get("user_id"),
            "msg": st.get("msg") or st.get("error"),
            "profile": self.profile,
            "probe_ms": int((time.time() - t0) * 1000),
        }

    def close(self):
        """本引擎按需启停浏览器，无常驻状态"""
        return None

    def fetch_image(self, url_or_fid, mode="png", timeout=120):
        """下载单张原图，返回 bytes"""
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        url = self.to_original(url_or_fid, mode)
        s = requests.Session()
        s.mount("https://", HTTPAdapter(max_retries=Retry(
            total=4, backoff_factor=1.2,
            status_forcelist=[429, 500, 502, 503, 504])))
        r = s.get(url, headers={"User-Agent": UA}, timeout=timeout)
        r.raise_for_status()
        return r.content

    def download(self, keyword=None, urls=None, out_dir=None, mode="png",
                 pages=2, workers=6, timeout=180):
        """
        搜索并下载原图（或直接下载给定链接）。

        返回 {"out_dir", "files", "ok", "fail", "bytes", "total"}
        """
        import concurrent.futures as cf

        out_dir = out_dir or os.path.join(os.path.dirname(self.out), "downloads",
                                         "xhs-web")
        os.makedirs(out_dir, exist_ok=True)

        if urls is None:
            if not keyword:
                raise WebError("需要 keyword 或 urls")
            data = self.search(keyword, pages=pages, mode=mode)
            fids = data["image_bases"]
        else:
            fids = [extract_fileid(u) for u in urls]
            fids = [f for f in fids if f]

        if not fids:
            return {"out_dir": out_dir, "files": [], "ok": 0, "fail": 0,
                    "bytes": 0, "total": 0}

        results = []

        def one(fid):
            url = self.to_original(fid, mode)
            name = re.sub(r"[^A-Za-z0-9._-]", "_", fid.split("/")[-1])[:60]
            ext = {"png": ".png", "jpg100": ".jpg", "jpg": ".jpg",
                   "raw": ".bin"}.get(mode, ".png")
            path = os.path.join(out_dir, name + ext)
            if os.path.exists(path) and os.path.getsize(path) > 1024:
                return ("skip", path, os.path.getsize(path))
            tmp = path + ".part"
            try:
                blob = self.fetch_image(url, mode, timeout=timeout)
                if len(blob) < 1024:
                    return ("fail", path, 0)
                with open(tmp, "wb") as f:
                    f.write(blob)
                os.replace(tmp, path)
                return ("ok", path, len(blob))
            except Exception as e:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
                return ("fail", "%s: %s" % (fid[:40], str(e)[:60]), 0)

        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(one, fids):
                results.append(r)

        ok = [r for r in results if r[0] in ("ok", "skip")]
        fail = [r for r in results if r[0] == "fail"]
        total = sum(r[2] for r in ok)
        return {
            "out_dir": out_dir,
            "files": [r[1] for r in ok],
            "ok": len(ok),
            "fail": len(fail),
            "fail_detail": [r[1] for r in fail][:10],
            "bytes": total,
            "total": len(fids),
            "mode": mode,
        }


if __name__ == "__main__":
    _utf8_stdout()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    w = XhsWeb(headless=(cmd != "login"))
    if cmd == "status":
        print(json.dumps(w.status(), ensure_ascii=False, indent=2))
    elif cmd == "login":
        print("=" * 66)
        print("扫码登录：二维码已保存，请用小红书 App 扫描")
        print("=" * 66)
        r = w.login()
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif cmd == "search":
        kw = sys.argv[2] if len(sys.argv) > 2 else "猫咪"
        d = w.search(kw)
        print("notes=%d images=%d xhr=%d" % (
            d["note_count"], d["image_count"], d["xhr_count"]))
        for n in d["notes"][:5]:
            print("  [%s] %s  @%s" % (n["note_id"], n["title"][:40], n["user"]))
        for u in d["images"][:3]:
            print("  " + u)
    elif cmd == "download":
        kw = sys.argv[2] if len(sys.argv) > 2 else "猫咪"
        mode = sys.argv[3] if len(sys.argv) > 3 else "png"
        d = w.download(keyword=kw, mode=mode)
        print(json.dumps({k: v for k, v in d.items() if k != "files"},
                         ensure_ascii=False, indent=2))
        print("目录: %s" % d["out_dir"])
