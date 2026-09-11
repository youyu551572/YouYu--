#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
post_login_verify.py — 登录后端到端验证

验证项：
  1) 登录态（guest 必须为 false）
  2) 关键词搜索是否真正返回笔记
  3) 搜索拿到的是否为原图（对比缩略图与 imageView2 原图）
"""
import io
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import requests
from xhs_web import XhsWeb

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT = r"D:\PYxiangmu\WKnx\workspace\capture"
PROXY = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}


def head(url, tag):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, proxies=PROXY,
                         timeout=40, stream=True)
        chunk = next(r.iter_content(65536), b"")
        r.close()
        # PNG 尺寸
        wh = ""
        if chunk[:8] == b"\x89PNG\r\n\x1a\n" and chunk[12:16] == b"IHDR":
            import struct
            w, h = struct.unpack(">II", chunk[16:24])
            wh = "%dx%d" % (w, h)
        cl = r.headers.get("Content-Length")
        print("  [%-12s] HTTP %-4s  %-6s bytes=%-9s %s" % (
            tag, r.status_code, chunk[:4].hex()[:8],
            cl if cl else "?", wh))
        return r.status_code, cl, wh
    except Exception as e:
        print("  [%-12s] EXC %s" % (tag, str(e)[:90]))
        return None, None, None


def main():
    w = XhsWeb(headless=True)

    print("=" * 70)
    print("1) 登录态")
    print("=" * 70)
    st = w.status()
    print(json.dumps(st, ensure_ascii=False, indent=2))
    if not st.get("logged_in"):
        print("\n尚未登录成功，验证终止。")
        return

    print("\n" + "=" * 70)
    print("2) 关键词搜索")
    print("=" * 70)
    kw = sys.argv[1] if len(sys.argv) > 1 else "咖啡"
    d = w.search(kw, pages=2)
    print("关键词     : %s" % d["keyword"])
    print("笔记数     : %d" % d["note_count"])
    print("图片数     : %d" % d["image_count"])
    print("XHR 响应   : %d" % d["xhr_count"])
    print("引擎       : %s" % d["engine"])
    for n in d["notes"][:6]:
        print("  [%s] %-36s @%s imgs=%d" % (
            str(n["note_id"])[:10], (n["title"] or "")[:36],
            (n["user"] or "")[:12], len(n["images"])))

    if not d["image_count"]:
        print("\n搜索未返回图片，保存原始响应供排查。")
        return

    print("\n" + "=" * 70)
    print("3) 原图质量对比（同一图源）")
    print("=" * 70)
    fid = d["image_bases"][0]
    print("fileid: %s" % fid)
    print()
    orig = XhsWeb.to_original(fid, "png")
    head("https://sns-na-i1.xhscdn.com/%s" % fid, "原始HEIC")
    head(orig, "PNG无损")
    head(XhsWeb.to_original(fid, "jpg100"), "JPG q100")
    head(XhsWeb.to_original(fid, "jpg"), "JPG默认")
    head(XhsWeb.to_original(fid, "raw"), "纯路径")

    with open(os.path.join(OUT, "verify_search.json"), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print("\n结果已保存 verify_search.json")


if __name__ == "__main__":
    main()
