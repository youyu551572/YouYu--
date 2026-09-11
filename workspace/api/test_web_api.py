#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_web_api.py — Web 引擎 API 全端点验证

注意：不要用 PowerShell 的 Invoke-RestMethod 发送中文，
      其 body 编码不是 UTF-8，服务端会收到 ??（见 README 的说明）。
"""
import json
import os
import sys
import time

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "http://127.0.0.1:8700"
S = requests.Session()


def show(tag, obj, limit=800):
    print("\n" + "=" * 72)
    print(tag)
    print("=" * 72)
    t = json.dumps(obj, ensure_ascii=False, indent=2)
    print(t[:limit] + ("\n...(截断)" if len(t) > limit else ""))


def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else "手冲咖啡"

    print("=" * 72)
    print("Web 引擎 API 验证  base=%s" % BASE)
    print("=" * 72)

    # 1. 根信息
    r = S.get(BASE + "/", timeout=30)
    print("\n[1] GET /  ->  %s" % r.status_code)
    j = r.json()
    print("    api_version=%s  engine=%s" % (j.get("api_version"), j.get("engine_kind")))

    # 2. 健康
    t0 = time.time()
    r = S.get(BASE + "/health", timeout=180)
    print("\n[2] GET /health  ->  %s  (%ds)" % (r.status_code, time.time() - t0))
    h = r.json()
    for k in ("engine", "engine_kind", "logged_in", "guest", "ready", "user_id"):
        print("    %-12s %s" % (k, h.get(k)))

    # 3. 同步搜索
    t0 = time.time()
    r = S.post(BASE + "/v1/search", json={"keyword": kw, "pages": 1, "mode": "png"},
               timeout=300)
    dt = time.time() - t0
    print("\n[3] POST /v1/search  ->  %s  (%.1fs)" % (r.status_code, dt))
    if r.status_code != 200:
        print("    %s" % r.text[:300])
        return
    d = r.json()
    print("    keyword=%s  notes=%d  images=%d  xhr=%d  engine_kind=%s" % (
        d.get("keyword"), d.get("note_count"), d.get("image_count"),
        d.get("xhr_count"), d.get("engine_kind")))
    for n in (d.get("notes") or [])[:5]:
        print("      [%s] %-34s @%s imgs=%d" % (
            str(n.get("note_id"))[:10], (n.get("title") or "")[:34],
            (n.get("user") or "")[:12], len(n.get("images") or [])))
    for u in (d.get("images") or [])[:3]:
        print("      ORIG %s" % u[:120])

    if not d.get("image_count"):
        print("\n搜索无图片，后续跳过")
        return
    fid = d["image_bases"][0]

    # 4. 单图转原图
    r = S.get(BASE + "/v1/original", params={"url": fid, "mode": "png"}, timeout=30)
    print("\n[4] GET /v1/original  ->  %s" % r.status_code)
    print("    %s" % json.dumps(r.json(), ensure_ascii=False)[:220])

    # 5. 批量转原图
    r = S.post(BASE + "/v1/original/batch",
               json={"urls": d["image_bases"][:4], "mode": "jpg100"}, timeout=30)
    print("\n[5] POST /v1/original/batch  ->  %s  count=%s" % (
        r.status_code, r.json().get("count")))

    # 6. 图片字节流
    t0 = time.time()
    r = S.get(BASE + "/v1/image", params={"url": fid, "mode": "png"},
              timeout=180, stream=True)
    blob = next(r.iter_content(1 << 20), b"")
    r.close()
    print("\n[6] GET /v1/image  ->  %s  ct=%s  首个MB=%d 字节  (%.1fs)" % (
        r.status_code, r.headers.get("Content-Type"), len(blob), time.time() - t0))
    print("    magic: %s" % blob[:8].hex())

    # 7. 异步搜索
    r = S.post(BASE + "/v1/search/async", json={"keyword": "咖啡拉花", "pages": 1},
               timeout=30)
    print("\n[7] POST /v1/search/async  ->  %s" % r.status_code)
    tid = r.json().get("task_id")
    print("    task_id=%s" % tid)
    for _ in range(40):
        time.sleep(3)
        rr = S.get(BASE + "/v1/tasks/" + tid, timeout=30).json()
        st = rr.get("status")
        print("    轮询: %s" % st)
        if st in ("done", "error"):
            if st == "done":
                res = rr.get("result") or {}
                print("    -> notes=%s images=%s" % (
                    res.get("note_count"), res.get("image_count")))
            else:
                print("    -> error=%s" % rr.get("error"))
            break

    # 8. 下载
    print("\n[8] POST /v1/download ...")
    r = S.post(BASE + "/v1/download",
               json={"keyword": kw, "pages": 0, "mode": "png", "workers": 8},
               timeout=900)
    print("    HTTP %s" % r.status_code)
    if r.status_code == 200:
        j = r.json()
        print("    notes=%s images=%s ok=%s fail=%s bytes=%.1f MB" % (
            j.get("note_count"), j.get("image_count"), j.get("ok"),
            j.get("fail"), (j.get("bytes") or 0) / 1048576))
        print("    out_dir=%s" % j.get("out_dir"))
    else:
        print("    %s" % r.text[:300])

    print("\n" + "=" * 72)
    print("验证结束")
    print("=" * 72)


if __name__ == "__main__":
    main()
