#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
probe_image_variants.py — 验证小红书图片 CDN 的原图获取规则

目标：确定「不压缩」的 URL 形式，并量化各变体画质差异
"""
import io
import json
import os
import struct
import sys

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

PROXY = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}
OUT = r"D:\PYxiangmu\WKnx\workspace\capture\imgtest"
os.makedirs(OUT, exist_ok=True)

BASE = ("https://sns-na-i2.xhscdn.com/notes_pre_post/"
        "1040g3k031v7ifpdn2q405pgl0ff0u9q19328mio")

# 抓包观察到的原始压缩 URL（作为画质基线）
COMPRESSED = (BASE + "?imageView2/2/w/576/format/heif/q/58|imageMogr2/strip"
                     "&redImage/frame/0/enhance/4&ap=5&sc=SRH_PRV")

VARIANTS = [
    ("00_compressed_baseline", COMPRESSED),
    ("01_orig_noparam",        BASE),
    ("02_fmt_png",             BASE + "?imageView2/2/format/png"),
    ("03_fmt_jpg",             BASE + "?imageView2/2/format/jpg"),
    ("04_w1080_jpg",           BASE + "?imageView2/2/w/1080/format/jpg"),
    ("05_w0_jpg_q100",         BASE + "?imageView2/2/w/0/format/jpg/q/100"),
    ("06_mogr2_jpg",           BASE + "?imageMogr2/format/jpg"),
    ("07_q100_only",           BASE + "?imageView2/2/q/100"),
    ("08_w2048",               BASE + "?imageView2/2/w/2048/format/jpg"),
]


def sniff(data: bytes):
    """仅靠文件头判断格式与尺寸，不依赖解码器"""
    if len(data) < 16:
        return "too_short", None, None

    # HEIC / HEIF: ....ftyp
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        # 从 meta box 里找 ispe 提取尺寸（简化扫描）
        w = h = None
        idx = data.find(b"ispe")
        if idx > 0 and idx + 16 <= len(data):
            try:
                w, h = struct.unpack(">II", data[idx + 8:idx + 16])
            except Exception:
                pass
        return "HEIC(%s)" % brand.decode("ascii", "replace"), w, h

    # JPEG
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return "JPEG", w, h
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg
        return "JPEG", None, None

    # PNG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return "PNG", w, h

    # WebP
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP", None, None

    return "UNKNOWN", None, None


def main():
    results = []
    for name, url in VARIANTS:
        try:
            r = requests.get(url, headers=UA, proxies=PROXY, timeout=45)
            data = r.content
            fmt, w, h = sniff(data)
            path = os.path.join(OUT, name + ".bin")
            with open(path, "wb") as f:
                f.write(data)
            rec = {
                "name": name,
                "status": r.status_code,
                "bytes": len(data),
                "ctype": r.headers.get("content-type", ""),
                "format": fmt,
                "width": w,
                "height": h,
                "url": url,
            }
        except Exception as e:
            rec = {"name": name, "status": "ERR", "error": str(e)[:100], "url": url}
        results.append(rec)
        print(json.dumps(rec, ensure_ascii=False))

    print("\n================ SUMMARY ================")
    base_bytes = None
    for r in results:
        if r.get("name") == "00_compressed_baseline":
            base_bytes = r.get("bytes")
    for r in results:
        if r.get("status") == 200:
            ratio = ""
            if base_bytes and r.get("bytes"):
                ratio = "  x%.2f vs compressed" % (r["bytes"] / base_bytes)
            print("%-24s %-12s %5sx%-5s %9d bytes%s" % (
                r["name"], r.get("format"), r.get("width"), r.get("height"),
                r.get("bytes"), ratio))
        else:
            print("%-24s FAILED: %s" % (r["name"], r.get("error", r.get("status"))))

    with open(os.path.join(OUT, "variants_report.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
