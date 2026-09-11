#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""test_api.py — API 端到端测试客户端（明确 UTF-8 编码）"""
import io
import json
import sys
import time

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

BASE = "http://127.0.0.1:8700"


def show(title):
    print("\n" + "=" * 66)
    print(title)
    print("=" * 66)


def t_root():
    show("GET /")
    r = requests.get(BASE + "/", timeout=20)
    print("HTTP", r.status_code)
    print(json.dumps(r.json(), ensure_ascii=False, indent=2)[:600])


def t_health():
    show("GET /health")
    r = requests.get(BASE + "/health", timeout=30)
    print("HTTP", r.status_code)
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))


def t_original():
    show("GET /v1/original  (纯转换，不需要设备)")
    u = ("https://sns-na-i4.xhscdn.com/notes_pre_post/"
         "1040g3k0324dh9cub6u004a41fjb19jtk14i1lj0"
         "?imageView2/2/w/1440/format/heif/q/45&sign=abc&t=123")
    r = requests.get(BASE + "/v1/original", params={"url": u, "mode": "png"}, timeout=20)
    print("HTTP", r.status_code)
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))

    show("POST /v1/original/batch")
    r = requests.post(BASE + "/v1/original/batch",
                      json={"urls": [u, u.replace("1040g3k0", "1040g3k1")],
                            "mode": "jpg100"}, timeout=20)
    print("HTTP", r.status_code)
    print(json.dumps(r.json(), ensure_ascii=False, indent=2))


def t_search(keyword, pages=0, mode="png"):
    show("POST /v1/search  keyword=%s pages=%d mode=%s" % (keyword, pages, mode))
    t0 = time.time()
    r = requests.post(BASE + "/v1/search",
                      json={"keyword": keyword, "pages": pages,
                            "mode": mode, "timeout": 60},
                      timeout=240)
    dt = time.time() - t0
    print("HTTP %s   墙钟耗时 %.1fs" % (r.status_code, dt))
    if r.status_code != 200:
        print("body:", r.text[:500])
        return None
    d = r.json()
    print("keyword        : %s" % d["keyword"])
    print("note_count     : %d" % d["note_count"])
    print("image_count    : %d" % d["image_count"])
    print("engine_elapsed : %s s" % d["elapsed"])
    print("cached         : %s" % d["cached"])
    print("source_files   : %s" % ", ".join(d["source_files"])[:150])
    print("\n--- 前 5 篇笔记 ---")
    for n in d["notes"][:5]:
        print("  [%s] %s  @%s  imgs=%d" % (
            n["note_id"], (n["title"] or "")[:36], (n["user"] or "")[:20],
            len(n["images"])))
    print("\n--- 前 3 条原图 URL ---")
    for u in d["images"][:3]:
        print("  " + u)
    return d


def t_search_async(keyword):
    show("POST /v1/search/async  keyword=%s" % keyword)
    r = requests.post(BASE + "/v1/search/async",
                      json={"keyword": keyword, "mode": "jpg100"}, timeout=30)
    print("HTTP", r.status_code)
    d = r.json()
    print(json.dumps(d, ensure_ascii=False, indent=2))
    tid = d["task_id"]
    for i in range(60):
        time.sleep(2)
        tr = requests.get("%s/v1/tasks/%s" % (BASE, tid), timeout=20).json()
        print("  [%02d] status=%s" % (i + 1, tr["status"]))
        if tr["status"] in ("done", "error"):
            if tr["status"] == "done":
                res = tr["result"]
                print("  -> keyword=%s notes=%d images=%d" % (
                    res["keyword"], res["note_count"], res["image_count"]))
            else:
                print("  -> error: %s" % tr.get("error"))
            break


def t_image():
    show("GET /v1/image  (原图字节流)")
    u = ("https://sns-na-i4.xhscdn.com/notes_pre_post/"
         "1040g3k0324dh9cub6u004a41fjb19jtk14i1lj0")
    r = requests.get(BASE + "/v1/image", params={"url": u, "mode": "png"}, timeout=180)
    print("HTTP", r.status_code)
    print("Content-Type : %s" % r.headers.get("Content-Type"))
    print("Bytes        : %d  (%.2f MB)" % (len(r.content), len(r.content) / 1024 / 1024))
    magic = r.content[:8].hex()
    print("Magic        : %s  %s" % (magic, "PNG" if magic.startswith("89504e47") else "?"))
    # PNG 尺寸
    if magic.startswith("89504e47"):
        import struct
        w, h = struct.unpack(">II", r.content[16:24])
        print("Dimensions   : %dx%d" % (w, h))


def t_window():
    show("GET /docs")
    r = requests.get(BASE + "/docs", timeout=20)
    print("HTTP %s   %d bytes   %s" % (r.status_code, len(r.content),
                                       r.headers.get("Content-Type")))


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    kw = sys.argv[2] if len(sys.argv) > 2 else "猫咪"
    try:
        if which in ("all", "basic"):
            t_root()
            t_health()
            t_original()
            t_window()
        if which in ("all", "image"):
            t_image()
        if which in ("all", "search"):
            t_search(kw)
        if which == "async":
            t_search_async(kw)
    except Exception as e:
        print("\nEXCEPTION: %r" % e)
