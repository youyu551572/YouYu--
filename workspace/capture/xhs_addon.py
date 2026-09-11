#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xhs_addon.py — mitmproxy 结构化抓包插件（小红书专用）

职责：
  1. 全量请求落 JSONL（含请求头/响应头/关键 body 片段）
  2. 控制台只打印高价值线索：搜索接口 / 图片 CDN / 签名头
  3. 单独抽取图片 URL 到 images.txt，供原图下载器消费
"""
import json
import os
import re
import time

from mitmproxy import http

OUT_DIR = r"D:\PYxiangmu\WKnx\workspace\capture"
JSONL = os.path.join(OUT_DIR, "traffic.jsonl")
IMAGES = os.path.join(OUT_DIR, "images.txt")
SUMMARY = os.path.join(OUT_DIR, "summary.txt")

os.makedirs(OUT_DIR, exist_ok=True)

IMG_HOST_RE = re.compile(r"(xhscdn\.com|xhscdn\.net|ci\.xiaohongshu\.com)", re.I)
IMG_EXT_RE = re.compile(r"\.(jpg|jpeg|png|webp|heic|avif)(\?|$)", re.I)
API_HOT_RE = re.compile(r"(search|image|sns/v|note|feed|shield)", re.I)
IMG_PARAM_RE = re.compile(r"(imageView2|imageMogr2|imageView|thumbnail|format|quality|w=|h=|watermark)", re.I)

_seen_img = set()
_fh = None
_ih = None
_sh = None


def _open():
    global _fh, _ih, _sh
    if _fh is None:
        _fh = open(JSONL, "a", encoding="utf-8")
    if _ih is None:
        _ih = open(IMAGES, "a", encoding="utf-8")
    if _sh is None:
        _sh = open(SUMMARY, "a", encoding="utf-8")


def _ts():
    return time.strftime("%H:%M:%S")


def request(flow: http.HTTPFlow) -> None:
    _open()
    req = flow.request
    url = req.pretty_url
    host = req.host

    rec = {
        "t": _ts(),
        "phase": "req",
        "method": req.method,
        "url": url,
        "host": host,
        "path": req.path,
        "headers": dict(req.headers),
    }

    # 请求体（可能是 gzip/二进制，做安全截断）
    try:
        body = req.get_text(strict=False)
        if body:
            rec["body"] = body[:4000]
    except Exception:
        pass

    _fh.write(json.dumps(rec, ensure_ascii=True) + "\n")
    _fh.flush()

    # 高价值请求打印
    if API_HOT_RE.search(req.path) or IMG_HOST_RE.search(host):
        shield = req.headers.get("shield", "")
        line = "[REQ ] %s %s %s" % (req.method, host, req.path[:160])
        if shield:
            line += "  shield=%s..." % shield[:20]
        print(line, flush=True)
        _sh.write(line + "\n")
        _sh.flush()

    # 图片 URL 抽取（含参数，用于原图推导）
    if IMG_HOST_RE.search(host) and (
        IMG_EXT_RE.search(req.path) or IMG_PARAM_RE.search(url) or "/spectrum/" in url
    ):
        if url not in _seen_img:
            _seen_img.add(url)
            _ih.write(url + "\n")
            _ih.flush()
            print("[IMG ] %s" % url[:200], flush=True)


def response(flow: http.HTTPFlow) -> None:
    _open()
    resp = flow.response
    req = flow.request

    rec = {
        "t": _ts(),
        "phase": "resp",
        "status": resp.status_code,
        "method": req.method,
        "url": req.pretty_url,
        "headers": dict(resp.headers),
    }

    ctype = resp.headers.get("content-type", "")
    if "json" in ctype or "text" in ctype:
        try:
            txt = resp.get_text(strict=False)
            if txt:
                rec["body"] = txt[:2000000]
                # 搜索类响应单独完整落盘，便于后续结构化解析
                if "search" in req.path:
                    safe = re.sub(r"[^A-Za-z0-9._-]", "_", req.path)[:120]
                    fn = os.path.join(OUT_DIR, "api_%s_%d.json" % (safe, int(time.time() * 1000)))
                    with open(fn, "w", encoding="utf-8") as sf:
                        sf.write(txt)
                    rec["saved_file"] = fn
                    print("[SAVE] %s" % fn, flush=True)
        except Exception:
            pass

    _fh.write(json.dumps(rec, ensure_ascii=True) + "\n")
    _fh.flush()

    if resp.status_code >= 400 or API_HOT_RE.search(req.path):
        print("[RESP] %s %s -> %s (%s bytes)" % (
            req.method, req.path[:120], resp.status_code, len(resp.content or b"")), flush=True)
