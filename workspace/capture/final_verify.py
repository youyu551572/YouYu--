#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""final_verify.py — 下载产物完整性核验"""
import io
import os
import struct
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

APP_AREA = 576 * 766


def png_size(p):
    with open(p, "rb") as f:
        d = f.read(33)
    if d[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", d[16:24])
    return None, None


def jpg_size(p):
    with open(p, "rb") as f:
        d = f.read(65536)
    i = 2
    while i < len(d) - 9:
        if d[i] != 0xFF:
            i += 1
            continue
        m = d[i + 1]
        if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", d[i + 5:i + 9])
            return w, h
        if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
            i += 2
            continue
        i += 2 + struct.unpack(">H", d[i + 2:i + 4])[0]
    return None, None


def verify(root):
    if not os.path.isdir(root):
        print("missing: %s" % root)
        return
    imgs = []
    parts = []
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = os.path.join(dp, fn)
            if fn.endswith(".part"):
                parts.append(p)
            elif fn.lower().endswith((".png", ".jpg")):
                sz = os.path.getsize(p)
                w, h = png_size(p) if fn.lower().endswith(".png") else jpg_size(p)
                imgs.append((p, sz, w, h))

    total = sum(i[1] for i in imgs)
    notes = len([d for d in os.listdir(root)
                 if os.path.isdir(os.path.join(root, d))])

    print("=" * 68)
    print(os.path.basename(root))
    print("  笔记目录 : %d" % notes)
    print("  图片总数 : %d" % len(imgs))
    print("  残片.part: %d" % len(parts))
    if imgs:
        print("  总体积   : %.2f MB   平均 %.2f MB" % (
            total / 1024 / 1024, total / len(imgs) / 1024 / 1024))
        mx = max(imgs, key=lambda x: (x[2] or 0) * (x[3] or 0))
        print("  最高分辨率: %sx%s  (%.1f MP, %.2f MB)" % (
            mx[2], mx[3], ((mx[2] or 0) * (mx[3] or 0)) / 1e6, mx[1] / 1024 / 1024))
        small = [i for i in imgs if (i[2] or 0) * (i[3] or 0) < APP_AREA]
        print("  低于 App 列表图(576x766)的: %d 张" % len(small))
        ratios = [((i[2] or 0) * (i[3] or 0)) / APP_AREA for i in imgs if i[2]]
        if ratios:
            print("  相对列表图面积倍数: 最小 x%.1f  最大 x%.1f  中位 x%.1f" % (
                min(ratios), max(ratios), sorted(ratios)[len(ratios) // 2]))


for r in [r"D:\PYxiangmu\WKnx\workspace\downloads\xhs",
          r"D:\PYxiangmu\WKnx\workspace\downloads\xhs-coffee"]:
    verify(r)
