#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xhs_core.py — 小红书搜索/原图引擎（可独立 import 的库）

设计：
  * 设备驱动的搜索（deeplink 触发 + 抓包解析），无需复刻 native 签名
  * 原图 URL 转换（纯字符串变换，无需设备）
  * 单设备串行：内部持锁，保证并发调用安全
  * 环境自愈：自动拉起 mitmdump、重建 adb reverse、设置全局代理

其他项目可直接：
    from xhs_core import XhsEngine
    eng = XhsEngine()
    data = eng.search("猫咪")
    for u in data["images"]:
        open("x.png","wb").write(eng.fetch_image(u))
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _utf8_stdout():
    """非破坏性地让 stdout 支持中文；可安全 import、可重复调用"""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------- 默认配置

DEFAULT_ADB = r"C:\Users\YouYu\Desktop\platform-tools\adb.exe"
DEFAULT_DEVICE = "emulator-5554"
DEFAULT_CAPTURE = r"D:\PYxiangmu\WKnx\workspace\capture"
DEFAULT_PYTHON = r"C:\Users\YouYu\AppData\Local\Programs\Python\Python311\python.exe"
PKG = "com.xingin.xhs"

PROXY_PORT = 8080
DEVICE_PROXY = "127.0.0.1:8080"

# 上游 HTTP 代理（宿主机访问 xhscdn 需要）
UPSTREAM_PROXY = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}

MODE_SUFFIX = {
    "png":    ("png", "?imageView2/2/format/png"),
    "jpg100": ("jpg", "?imageView2/2/w/0/format/jpg/q/100"),
    "jpg":    ("jpg", "?imageView2/2/format/jpg"),
    "raw":    ("bin", ""),
}

IMG_URL_RE = re.compile(
    r"^https?://[A-Za-z0-9.\-]+\.(?:xhscdn\.com|xhscdn\.net|xiaohongshu\.com)/", re.I)
NON_IMG_RE = re.compile(
    r"/(api|formula-static|fe-platform|fe-platform-file|fe-static|as)/", re.I)


class XhsError(Exception):
    pass


# ---------------------------------------------------------------- 引擎

class XhsEngine:
    def __init__(self, adb=DEFAULT_ADB, device=DEFAULT_DEVICE,
                 capture_dir=DEFAULT_CAPTURE, python=DEFAULT_PYTHON,
                 manage_capture=True, upstream_proxy=True):
        self.adb = adb
        self.device = device
        self.capture = capture_dir
        self.python = python
        self.manage_capture = manage_capture
        self._lock = threading.RLock()
        self._mitm_proc = None
        self._cache = {}
        self._cache_ttl = 300.0

        # 图片下载会话
        retry = Retry(total=5, connect=5, read=5, backoff_factor=1.2,
                      status_forcelist=[429, 500, 502, 503, 504],
                      allowed_methods=frozenset(["GET"]))
        self._session = requests.Session()
        self._session.mount("http://", HTTPAdapter(max_retries=retry,
                                                   pool_connections=16, pool_maxsize=16))
        self._session.mount("https://", HTTPAdapter(max_retries=retry,
                                                    pool_connections=16, pool_maxsize=16))
        self.proxies = UPSTREAM_PROXY if upstream_proxy else None

    # -------------------------------------------------- 低层：adb

    def _sh(self, args, timeout=60):
        try:
            r = subprocess.run([self.adb, "-s", self.device] + args,
                               capture_output=True, timeout=timeout,
                               text=True, encoding="utf-8", errors="replace")
            return (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            return "ERR: timeout"
        except Exception as e:
            return "ERR: %s" % e

    def _port_open(self, host, port, timeout=1.5):
        import socket
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False

    # -------------------------------------------------- 环境管理

    def ensure_mitm(self):
        """确保 mitmdump 在 8080 监听（带 xhs_addon.py）"""
        if not self.manage_capture:
            return self._port_open("127.0.0.1", PROXY_PORT)
        if self._port_open("127.0.0.1", PROXY_PORT):
            return True
        addon = os.path.join(self.capture, "xhs_addon.py")
        if not os.path.exists(addon):
            raise XhsError("缺少 addon: %s" % addon)
        script_dir = os.path.dirname(self.python)
        env = dict(os.environ)
        env["PATH"] = script_dir + os.pathsep + env.get("PATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        logf = open(os.path.join(self.capture, "mitmdump.log"), "a", encoding="utf-8")
        self._mitm_proc = subprocess.Popen(
            [self.python, "-m", "mitmproxy.tools.dump",
             "--listen-host", "0.0.0.0", "--listen-port", str(PROXY_PORT),
             "--set", "block_global=false", "-s", addon, "--flow-detail", "0"],
            stdout=logf, stderr=subprocess.STDOUT, env=env,
            cwd=self.capture, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for _ in range(30):
            time.sleep(0.5)
            if self._port_open("127.0.0.1", PROXY_PORT):
                return True
        return False

    def ensure_tunnel(self):
        """确保 adb reverse 隧道与设备全局代理"""
        out = self._sh(["reverse", "--list"])
        if "tcp:%d" % PROXY_PORT not in out:
            self._sh(["reverse", "tcp:%d" % PROXY_PORT, "tcp:%d" % PROXY_PORT])
        cur = self._sh(["shell", "settings get global http_proxy"]).strip()
        if cur != DEVICE_PROXY:
            self._sh(["shell", "settings put global http_proxy %s" % DEVICE_PROXY])
        return True

    def app_alive(self):
        return PKG in self._sh(["shell", "ps -A | grep '%s$'" % PKG])

    def top_activity(self):
        out = self._sh(["shell", "dumpsys activity activities | grep mResumedActivity"])
        m = re.search(r"u0 (\S+)", out)
        return m.group(1) if m else "?"

    def ensure_app(self, restart=False):
        """确保小红书主进程在跑；崩溃是常态（houdini 转译问题），失败则重启"""
        if self.app_alive() and not restart:
            return True
        self._sh(["shell", "am force-stop %s" % PKG])
        time.sleep(1.5)
        self._sh(["shell", "monkey -p %s -c android.intent.category.LAUNCHER 1" % PKG], timeout=30)
        for _ in range(25):
            time.sleep(1)
            if self.app_alive():
                time.sleep(2)
                return True
        return False

    def health(self):
        return {
            "adb_ok": os.path.exists(self.adb),
            "device": self.device,
            "device_online": "device" in self._sh(["get-state"], timeout=15),
            "mitm_running": self._port_open("127.0.0.1", PROXY_PORT),
            "tunnel": "tcp:%d" % PROXY_PORT in self._sh(["reverse", "--list"], timeout=15),
            "device_proxy": self._sh(["shell", "settings get global http_proxy"], timeout=15).strip(),
            "app_alive": self.app_alive(),
            "top_activity": self.top_activity(),
        }

    def ensure_ready(self):
        """一键就绪：抓包器 + 隧道 + 代理 + App"""
        self.ensure_mitm()
        self.ensure_tunnel()
        self.ensure_app()
        return self.health()

    # -------------------------------------------------- 抓包产物

    def _capture_files(self):
        return sorted(glob.glob(os.path.join(self.capture, "api_*search*notes*.json")))

    def _clear_capture(self):
        for f in glob.glob(os.path.join(self.capture, "api_*.json")):
            try:
                os.remove(f)
            except OSError:
                pass
        t = os.path.join(self.capture, "traffic.jsonl")
        if os.path.exists(t):
            try:
                os.remove(t)
            except OSError:
                pass

    # -------------------------------------------------- 搜索

    def trigger_search(self, keyword):
        enc = urllib.parse.quote(keyword)
        d = "xhsdiscover://search/result?keyword=%s" % enc
        out = self._sh(["shell", "am start -a android.intent.action.VIEW -d '%s' %s" % (d, PKG)],
                       timeout=30)
        return "Starting:" in out or "Warning" in out

    def scroll(self, n=1, gap=3.5):
        for _ in range(n):
            self._sh(["shell", "input swipe 540 1500 540 500 300"], timeout=20)
            time.sleep(gap)

    @staticmethod
    def _walk_images(node, acc):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and XhsEngine.is_image_url(v):
                    acc.append(v)
                else:
                    XhsEngine._walk_images(v, acc)
        elif isinstance(node, list):
            for v in node:
                XhsEngine._walk_images(v, acc)

    @staticmethod
    def is_image_url(u):
        return bool(isinstance(u, str) and u.startswith("http")
                    and IMG_URL_RE.match(u) and not NON_IMG_RE.search(u))

    @staticmethod
    def to_original(url, mode="png"):
        """压缩图 URL → 原图 URL（剥离全部查询参数后附加格式指令）"""
        base = url.split("?", 1)[0]
        suffix = MODE_SUFFIX.get(mode, MODE_SUFFIX["png"])[1]
        return base + suffix if suffix else base

    def _parse_response_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return [], []
        if not isinstance(data, dict):
            return [], []
        d = data.get("data") or {}
        items = d.get("items") if isinstance(d, dict) else None
        notes, imgs = [], []
        if not items:
            return [], []

        for it in items:
            if not isinstance(it, dict):
                continue
            note = it.get("note") or {}
            if not isinstance(note, dict):
                note = {}
            nid = it.get("id") or note.get("id") or note.get("note_id")
            title = note.get("display_title") or note.get("title") or it.get("display_title") or ""
            user = ""
            u = note.get("user")
            if isinstance(u, dict):
                user = u.get("nickname") or u.get("nick_name") or ""

            images = []
            il = note.get("images_list") or note.get("image_list") or []
            if isinstance(il, list):
                for im in il:
                    if not isinstance(im, dict):
                        continue
                    fid = im.get("fileid") or im.get("trace_id")
                    raw = (im.get("url_size_large") or im.get("url")
                           or im.get("url_default") or "")
                    if not raw and fid:
                        raw = "https://sns-na-i4.xhscdn.com/" + fid
                    if not self.is_image_url(raw):
                        continue
                    images.append({
                        "base": raw.split("?", 1)[0],
                        "fileid": fid,
                        "width": im.get("width"),
                        "height": im.get("height"),
                    })

            cov = note.get("cover") or {}
            if not images and isinstance(cov, dict):
                for k in ("url_size_large", "url_default", "url"):
                    if self.is_image_url(cov.get(k, "")):
                        images.append({"base": cov[k].split("?", 1)[0], "fileid": None,
                                       "width": None, "height": None})
                        break

            if nid or images:
                notes.append({"note_id": nid, "title": title, "user": user,
                              "type": note.get("type"), "images": images})
                for im in images:
                    if im["base"] not in imgs:
                        imgs.append(im["base"])
        return notes, imgs

    def _attempt_search(self, keyword, pages, mode, timeout, restart_app):
        """单次搜索尝试（不含重试）"""
        t0 = time.time()
        self.ensure_mitm()
        self.ensure_tunnel()
        self._clear_capture()

        if not self.ensure_app(restart=restart_app):
            raise XhsError("小红书进程无法启动")

        if not self.trigger_search(keyword):
            raise XhsError("deeplink 下发失败")

        # 等待搜索响应落盘
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(1.0)
            if self._capture_files():
                break
            if not self.app_alive():
                # 进程崩溃，但可能已抓到数据
                time.sleep(1)
                break

        if pages > 0 and self.app_alive():
            self.scroll(pages)

        files = self._capture_files()
        notes, imgs = [], []
        seen_notes = set()
        for f in files:
            n, im = self._parse_response_file(f)
            for x in n:
                key = x.get("note_id") or x.get("title")
                if key and key not in seen_notes:
                    seen_notes.add(key)
                    notes.append(x)
            for b in im:
                if b not in imgs:
                    imgs.append(b)

        return {
            "keyword": keyword,
            "mode": mode,
            "notes": notes,
            "images": [self.to_original(b, mode) for b in imgs],
            "image_bases": imgs,
            "note_count": len(notes),
            "image_count": len(imgs),
            "elapsed": round(time.time() - t0, 2),
            "source_files": [os.path.basename(f) for f in files],
            "cached": False,
            "attempts": 1,
            "app_alive_after": self.app_alive(),
        }

    def search(self, keyword, pages=0, mode="png", timeout=45,
               use_cache=True, restart_app=False, retries=1):
        """
        执行搜索并返回结构化数据。

        小红书跑在 houdini 转译层下会不定时崩溃，首次尝试可能空手而归；
        因此无结果时默认自动重启 App 重试（retries 控制额外次数）。

        返回:
          {
            "keyword": ..., "notes": [...], "images": [...],   # images 为原图 URL
            "image_count": N, "note_count": N, "elapsed": sec,
            "source_files": [...], "cached": bool, "attempts": N
          }
        """
        if not keyword or not keyword.strip():
            raise XhsError("keyword 不能为空")
        keyword = keyword.strip()

        ck = "%s|%d|%s" % (keyword, pages, mode)
        if use_cache:
            hit = self._cache.get(ck)
            if hit and (time.time() - hit[0]) < self._cache_ttl:
                d = dict(hit[1])
                d["cached"] = True
                return d

        with self._lock:
            last = None
            for attempt in range(1, max(0, retries) + 2):
                last = self._attempt_search(
                    keyword, pages, mode, timeout,
                    restart_app=(restart_app or attempt > 1))
                last["attempts"] = attempt
                if last["note_count"] > 0:
                    break
                if attempt <= retries:
                    # 无结果多半是 App 已崩溃，重启后重试
                    time.sleep(1.0)
                    self.ensure_app(restart=True)

            self._cache[ck] = (time.time(), last)
            return last

    # -------------------------------------------------- 图片

    def fetch_image(self, url, mode=None):
        """下载图片字节（url 可以是压缩图或已是原图）"""
        if mode:
            url = self.to_original(url, mode)
        r = self._session.get(url, headers=UA, proxies=self.proxies,
                              timeout=(15, 180), stream=True)
        if r.status_code != 200:
            raise XhsError("HTTP %s for %s" % (r.status_code, url[:120]))
        clen = r.headers.get("Content-Length")
        buf = bytearray()
        for chunk in r.iter_content(chunk_size=256 * 1024):
            if chunk:
                buf.extend(chunk)
        if clen and clen.isdigit() and int(clen) != len(buf):
            raise XhsError("传输被截断: %d/%s" % (len(buf), clen))
        return bytes(buf), r.headers.get("Content-Type", "")

    def download(self, images, out_dir, mode="png", workers=6,
                 on_progress=None):
        """批量下载原图到目录，返回统计"""
        import concurrent.futures as futures
        ext = MODE_SUFFIX.get(mode, MODE_SUFFIX["png"])[0]
        os.makedirs(out_dir, exist_ok=True)
        stats = {"ok": 0, "skip": 0, "fail": 0, "bytes": 0, "files": [], "errors": []}

        def one(u):
            name = u.split("?", 1)[0].rstrip("/").split("/")[-1] + "." + ext
            path = os.path.join(out_dir, name)
            try:
                if os.path.exists(path) and os.path.getsize(path) > 1024:
                    return ("skip", path, os.path.getsize(path), None)
                data, _ = self.fetch_image(u)
                tmp = path + ".part"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, path)
                return ("ok", path, len(data), None)
            except Exception as e:
                try:
                    if os.path.exists(path + ".part"):
                        os.remove(path + ".part")
                except OSError:
                    pass
                return ("fail", path, 0, str(e)[:150])

        with futures.ThreadPoolExecutor(max_workers=workers) as ex:
            for st, path, size, err in ex.map(one, images):
                if st == "ok":
                    stats["ok"] += 1
                    stats["bytes"] += size
                    stats["files"].append(path)
                elif st == "skip":
                    stats["skip"] += 1
                    stats["files"].append(path)
                else:
                    stats["fail"] += 1
                    stats["errors"].append({"url": path, "error": err})
                if on_progress:
                    on_progress(st, path, size, err)
        return stats

    def close(self):
        if self._mitm_proc and self._mitm_proc.poll() is None:
            try:
                self._mitm_proc.terminate()
            except Exception:
                pass
        self._session.close()
