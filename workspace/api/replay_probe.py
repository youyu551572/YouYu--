#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
replay_probe.py — 签名必要性实验

从抓包中取出真实的搜索请求，逐层剥离签名头重放，
判定哪些头是服务端真正校验的。

结论将决定：
  * 若 shield 非必需  -> 搜索可完全脱离设备，纯 HTTP 实现
  * 若 shield 必需    -> 必须复刻 native 签名，或把设备服务化
"""
import io
import json
import os
import sys
import time

import requests

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

CAP = r"D:\PYxiangmu\WKnx\workspace\capture\traffic.jsonl"
PROXY = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}


def load_search_requests():
    out = []
    with open(CAP, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("phase") == "req" and "search/notes" in r.get("url", ""):
                out.append(r)
    return out


def try_request(url, headers, tag, timeout=25):
    try:
        r = requests.get(url, headers=headers, proxies=PROXY,
                         timeout=timeout, allow_redirects=False)
        body = r.text[:600]
        # 尝试解析为 JSON 判定业务结果
        verdict = ""
        try:
            j = r.json()
            code = j.get("code")
            ok = j.get("success")
            n = 0
            items = ((j.get("data") or {}).get("items") or [])
            n = len(items)
            verdict = "code=%s success=%s items=%d" % (code, ok, n)
            if j.get("msg"):
                verdict += " msg=%s" % str(j.get("msg"))[:40]
        except Exception:
            verdict = "(非 JSON) " + body[:100].replace("\n", " ")
        print("  [%-22s] HTTP %-4s  %s" % (tag, r.status_code, verdict))
        return r.status_code, verdict, body
    except Exception as e:
        print("  [%-22s] EXC %s" % (tag, str(e)[:100]))
        return None, str(e), ""


def main():
    reqs = load_search_requests()
    if not reqs:
        print("未找到 search/notes 请求记录")
        return
    r = reqs[-1]
    url = r["url"]
    full = dict(r.get("headers", {}))

    print("=" * 74)
    print("签名必要性实验")
    print("=" * 74)
    print("URL: %s" % url[:150])
    print("原始请求头 %d 个" % len(full))
    print()

    # 补上抓包可能遗漏的必要求头
    base = dict(full)
    base.setdefault("Accept", "application/json")

    print("--- 第一轮：完整请求（基线） ---")
    try_request(url, base, "full")

    print()
    print("--- 第二轮：单独剥离各类签名 ---")
    for key in ["shield", "x-mini-sig", "x-mini-s1", "x-mini-mua",
                "x-mini-gid", "xy-common-params", "xy-platform-info",
                "x-legacy-did", "x-legacy-sid"]:
        h = dict(base)
        h.pop(key, None)
        try_request(url, h, "no-" + key)

    print()
    print("--- 第三轮：极简头（只留 UA） ---")
    minimal = {"User-Agent": full.get("User-Agent", "Dalvik/2.1.0")}
    try_request(url, minimal, "minimal-ua")

    print()
    print("--- 第四轮：连 UA 都不要 ---")
    try_request(url, {}, "no-headers")

    print()
    print("--- 第五轮：换新 search_id/session_id（测试是否绑定时效） ---")
    import re
    rnd = str(int(time.time() * 1000))[-13:]
    u2 = re.sub(r"search_id=[^&]*", "search_id=2" + rnd, url)
    u2 = re.sub(r"session_id=[^&]*", "session_id=2" + rnd, u2)
    try_request(u2, base, "new-ids-full-hdr")
    try_request(u2, minimal, "new-ids-minimal")


if __name__ == "__main__":
    main()
