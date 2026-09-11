#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
suffix_test.py — 验证去掉 !nc_n_webp_mw_1 后缀能否拿到原图

Web 搜索返回的图片 URL 形如：
  .../notes_pre_post/<fileid>!nc_n_webp_mw_1
该后缀把资源钉在 webp 转码上，导致 imageView2 参数全部失效。
本脚本测试剥离后缀后的原图可获取性。
"""
import os
import struct
import sys

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

P = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}


def probe(url, tag, timeout=45):
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"},
                         proxies=P, timeout=timeout, stream=True)
        chunk = next(r.iter_content(131072), b"")
        clen = r.headers.get("Content-Length", "?")
        r.close()
        fmt, wh = "", ""
        if chunk[:8] == b"\x89PNG\r\n\x1a\n":
            fmt = "PNG"
            if chunk[12:16] == b"IHDR":
                w, h = struct.unpack(">II", chunk[16:24])
                wh = "%dx%d" % (w, h)
        elif chunk[:2] == b"\xff\xd8":
            fmt = "JPEG"
            i = 2
            while i < len(chunk) - 9:
                if chunk[i] != 0xFF:
                    i += 1
                    continue
                m = chunk[i + 1]
                if m in (0xC0, 0xC1, 0xC2):
                    h, w = struct.unpack(">HH", chunk[i + 5:i + 9])
                    wh = "%dx%d" % (w, h)
                    break
                if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                    i += 2
                else:
                    i += 2 + struct.unpack(">H", chunk[i + 2:i + 4])[0]
        elif chunk[:4] == b"RIFF" and chunk[8:12] == b"WEBP":
            fmt = "WEBP"
        elif b"ftyp" in chunk[:32]:
            fmt = "HEIC"
        print("  [%-30s] %-4s %-6s bytes=%-9s %s" % (tag, r.status_code, fmt, clen, wh))
        return r.status_code, fmt, wh
    except Exception as e:
        print("  [%-30s] EXC %s" % (tag, str(e)[:80]))
        return None, None, None


TS = "202609120210"
H = "45962049e493259ffae257075110704d"
FID = "notes_pre_post/1040g3k031ut13e443m4g5nroa75gbnaqq24oh1o"

print("=" * 78)
print("A. 保留后缀（对照）")
print("=" * 78)
probe("http://sns-webpic-qc.xhscdn.com/%s/%s/%s!nc_n_webp_mw_1" % (TS, H, FID), "带后缀 原样")
probe("http://sns-webpic-qc.xhscdn.com/%s/%s/%s!nc_n_webp_mw_1?imageView2/2/format/png" % (TS, H, FID), "带后缀 + PNG")

print()
print("=" * 78)
print("B. 剥离后缀 —— 关键验证")
print("=" * 78)
probe("http://sns-webpic-qc.xhscdn.com/%s/%s/%s" % (TS, H, FID), "裸 fileid 无参数")
probe("http://sns-webpic-qc.xhscdn.com/%s/%s/%s?imageView2/2/format/png" % (TS, H, FID), "裸 + PNG 无损")
probe("http://sns-webpic-qc.xhscdn.com/%s/%s/%s?imageView2/2/w/0/format/jpg/q/100" % (TS, H, FID), "裸 + JPG q100")

print()
print("=" * 78)
print("C. 原图 CDN 主机 + 裸 fileid")
print("=" * 78)
for h in ["sns-na-i1.xhscdn.com", "sns-na-i2.xhscdn.com",
          "sns-na-i4.xhscdn.com", "sns-na-i6.xhscdn.com"]:
    probe("https://%s/%s?imageView2/2/format/png" % (h, FID), h + " PNG")

print()
print("=" * 78)
print("D. 纯路径（不加任何参数，取原始文件）")
print("=" * 78)
for h in ["sns-na-i1.xhscdn.com", "sns-webpic-qc.xhscdn.com"]:
    probe("https://%s/%s" % (h, FID), h + " 纯路径")
probe("https://sns-na-i1.xhscdn.com/%s?imageView2/2/w/0/format/jpg/q/100" % FID, "i1 JPG q100")
probe("https://sns-na-i1.xhscdn.com/%s?imageView2/2/format/webp" % FID, "i1 WEBP")
