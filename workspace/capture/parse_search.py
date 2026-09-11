#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
parse_search.py — 从抓包产物提取小红书笔记与图片原图 URL（v2，适配真实响应结构）

真实结构：
  {"code":0,"success":true,"data":{"items":[
      {"model_type":"note","id":"...","note":{"type":"normal","images_list":[
          {"url_size_large":"https://sns-na-i4.xhscdn.com/notes_pre_post/xxx?imageView2/...",
           "fileid":"notes_pre_post/xxx","width":1600,"height":2753}
      ]}}
  ]}}
"""
import glob
import io
import json
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

CAP = r"D:\PYxiangmu\WKnx\workspace\capture"
OUT_NOTES = os.path.join(CAP, "notes.json")
OUT_PNG = os.path.join(CAP, "originals_png.txt")
OUT_JPG = os.path.join(CAP, "originals_jpg.txt")
OUT_MANIFEST = os.path.join(CAP, "download_manifest.json")

# 修正：host 允许包含点号
IMG_URL_RE = re.compile(
    r"^https?://[A-Za-z0-9.\-]+\.(?:xhscdn\.com|xhscdn\.net|xiaohongshu\.com)/", re.I)
NON_IMG_RE = re.compile(r"/(api|formula-static|fe-platform|fe-platform-file|fe-static|as)/", re.I)


def is_image_url(u):
    if not isinstance(u, str) or not u.startswith("http"):
        return False
    if not IMG_URL_RE.match(u):
        return False
    if NON_IMG_RE.search(u):
        return False
    return True


def base_of(u):
    return u.split("?", 1)[0]


def orig_png(u):
    """无损原图：保留原始像素，PNG 编码"""
    return base_of(u) + "?imageView2/2/format/png"


def orig_jpg(u):
    """最高质量 JPEG 原图"""
    return base_of(u) + "?imageView2/2/w/0/format/jpg/q/100"


def orig_raw(u):
    """原始字节（HEIC/原编码，无任何处理）"""
    return base_of(u)


def collect_images(node, acc):
    """递归收集所有图片 URL，记录来源字段名"""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and is_image_url(v):
                acc.append((v, k))
            else:
                collect_images(v, acc)
    elif isinstance(node, list):
        for v in node:
            collect_images(v, acc)


def main():
    files = sorted(glob.glob(os.path.join(CAP, "api_*search*.json")))
    print("search response files: %d" % len(files))

    notes = []
    seen_urls = set()
    ordered = []

    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            print("parse fail %s: %s" % (os.path.basename(f), e))
            continue

        if not isinstance(data, dict):
            continue

        d = data.get("data") or {}
        items = d.get("items") if isinstance(d, dict) else None
        if not items:
            continue

        for it in items:
            if not isinstance(it, dict):
                continue
            note = it.get("note") or {}
            if not isinstance(note, dict):
                note = {}

            nid = it.get("id") or note.get("id") or note.get("note_id")
            title = (note.get("display_title") or note.get("title")
                     or it.get("display_title") or "")
            user = ""
            u = note.get("user") or {}
            if isinstance(u, dict):
                user = u.get("nickname") or u.get("nick_name") or ""

            imgs = []
            il = note.get("images_list") or note.get("image_list") or []
            if isinstance(il, list):
                for im in il:
                    if not isinstance(im, dict):
                        continue
                    # 优先 fileid 构造，最干净
                    fid = im.get("fileid") or im.get("trace_id")
                    raw = im.get("url_size_large") or im.get("url") or im.get("url_default") or ""
                    if not raw and fid:
                        raw = "https://sns-na-i4.xhscdn.com/" + fid
                    if not is_image_url(raw):
                        continue
                    imgs.append({
                        "base": base_of(raw),
                        "fileid": fid,
                        "width": im.get("width"),
                        "height": im.get("height"),
                    })

            # cover 兜底
            cov = note.get("cover") or {}
            if not imgs and isinstance(cov, dict):
                for k in ("url_size_large", "url_default", "url"):
                    if is_image_url(cov.get(k, "")):
                        imgs.append({"base": base_of(cov[k]), "fileid": None,
                                     "width": None, "height": None})
                        break

            if nid or imgs:
                notes.append({
                    "note_id": nid,
                    "title": title,
                    "user": user,
                    "type": note.get("type"),
                    "image_count": len(imgs),
                    "images": imgs,
                })
                for im in imgs:
                    b = im["base"]
                    if b not in seen_urls:
                        seen_urls.add(b)
                        ordered.append((b, im.get("width"), im.get("height")))

    with open(OUT_NOTES, "w", encoding="utf-8") as f:
        json.dump(notes, f, ensure_ascii=False, indent=2)

    with open(OUT_PNG, "w", encoding="utf-8") as f:
        for b, _, _ in ordered:
            f.write(b + "?imageView2/2/format/png\n")

    with open(OUT_JPG, "w", encoding="utf-8") as f:
        for b, _, _ in ordered:
            f.write(b + "?imageView2/2/w/0/format/jpg/q/100\n")

    manifest = [{"base": b, "w": w, "h": h} for b, w, h in ordered]
    with open(OUT_MANIFEST, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("\n================ RESULT ================")
    print("notes parsed:      %d" % len(notes))
    print("unique images:     %d" % len(ordered))
    print("notes.json         -> %s" % OUT_NOTES)
    print("originals_png.txt  -> %s   (PNG 无损)" % OUT_PNG)
    print("originals_jpg.txt  -> %s   (JPEG q100)" % OUT_JPG)
    print("download_manifest  -> %s" % OUT_MANIFEST)

    if notes:
        print("\n---- sample notes ----")
        for n in notes[:6]:
            print("  [%s] imgs=%d  %s  @%s" % (
                n["note_id"], n["image_count"], (n["title"] or "")[:38], n["user"]))
        print("\n---- sample original URL ----")
        if ordered:
            print("  " + ordered[0][0] + "?imageView2/2/format/png")
            print("  声明原图尺寸: %s x %s" % (ordered[0][1], ordered[0][2]))


if __name__ == "__main__":
    main()
