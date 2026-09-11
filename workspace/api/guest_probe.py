#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
guest_probe.py — 游客态（未登录）能力边界实测

目的：回答"必须登录吗"
方法：在不登录的浏览器上下文里访问各入口，统计哪些 XHR 真正返回了笔记数据
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROFILE = r"D:\PYxiangmu\WKnx\workspace\chrome-profile-guest"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

TARGETS = [
    ("首页推荐", "https://www.xiaohongshu.com/explore"),
    ("搜索页", "https://www.xiaohongshu.com/search_result?keyword=%E7%8C%AB%E5%92%AA"),
    ("笔记详情", "https://www.xiaohongshu.com/explore/6a91403c00000000280042c2"),
    ("用户主页", "https://www.xiaohongshu.com/user/profile/5f0b0d1c0000000001000000"),
]


def count_notes(obj):
    """递归统计 note 卡片数量"""
    n = 0
    if isinstance(obj, dict):
        if "note_card" in obj or "noteCard" in obj:
            n += 1
        for v in obj.values():
            n += count_notes(v)
    elif isinstance(obj, list):
        for v in obj:
            n += count_notes(v)
    return n


def main():
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=PROFILE, headless=True, channel="chrome",
            viewport={"width": 1440, "height": 900}, locale="zh-CN",
            user_agent=UA, proxy={"server": "http://127.0.0.1:7897"},
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])

        print("=" * 74)
        print("游客态能力边界实测")
        print("=" * 74)

        for name, url in TARGETS:
            hits = []
            page = ctx.new_page()

            def on_resp(r):
                if "/api/sns/web" in r.url:
                    try:
                        if "json" not in r.headers.get("content-type", ""):
                            return
                        t = r.text()
                        try:
                            j = json.loads(t)
                        except Exception:
                            return
                        hits.append({
                            "url": r.url.split("?")[0].replace("https://edith.xiaohongshu.com", ""),
                            "status": r.status,
                            "code": j.get("code"),
                            "msg": str(j.get("msg"))[:30],
                            "notes": count_notes(j),
                            "len": len(t),
                        })
                    except Exception:
                        pass

            page.on("response", on_resp)
            print("\n" + "-" * 74)
            print("【%s】 %s" % (name, url[:90]))
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                for i in range(6):
                    time.sleep(3)
                    try:
                        page.mouse.wheel(0, 2200)
                    except Exception:
                        pass
            except Exception as e:
                print("  访问异常: %s" % str(e)[:120])

            # 去重展示
            seen = set()
            print("  捕获 %d 个 API 响应：" % len(hits))
            for h in hits:
                k = h["url"]
                if k in seen:
                    continue
                seen.add(k)
                flag = "★有数据" if h["notes"] > 0 else ""
                print("    %-46s code=%-6s notes=%-3d %s %s" % (
                    h["url"][:46], h["code"], h["notes"], flag, h["msg"]))
            page.close()

        ctx.close()

    print("\n" + "=" * 74)
    print("结论要点：★标记的接口在未登录时也返回了笔记数据")
    print("=" * 74)


if __name__ == "__main__":
    main()
