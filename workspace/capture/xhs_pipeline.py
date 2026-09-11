#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xhs_pipeline.py — 小红书搜索→抓包→解析→原图下载 端到端流水线

前置：
  1. mitmdump 已在 8080 运行（带 xhs_addon.py）
  2. adb reverse tcp:8080 tcp:8080 已建立
  3. 设备全局代理指向 127.0.0.1:8080

流程：
  触发 deeplink 搜索 → 滚动加载更多页 → 等待抓包 → 解析响应 → 下载原图

用法：
  python xhs_pipeline.py --keyword 猫咪 --pages 3 --mode png
  python xhs_pipeline.py --keyword "咖啡" --pages 1 --mode jpg100
"""
import argparse
import glob
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

CAP = r"D:\PYxiangmu\WKnx\workspace\capture"
ADB = r"C:\Users\YouYu\Desktop\platform-tools\adb.exe"
DEVICE = "emulator-5554"
PKG = "com.xingin.xhs"


def sh(args, timeout=60):
    try:
        r = subprocess.run([ADB, "-s", DEVICE] + args, capture_output=True,
                           timeout=timeout, text=True, encoding="utf-8", errors="replace")
        return (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return "ERR: %s" % e


def xhs_alive():
    out = sh(["shell", "ps -A | grep 'com.xingin.xhs$'"])
    return "com.xingin.xhs" in out


def restart_app():
    sh(["shell", "am force-stop %s" % PKG])
    time.sleep(2)
    sh(["shell", "monkey -p %s -c android.intent.category.LAUNCHER 1" % PKG], timeout=30)
    for _ in range(20):
        time.sleep(1)
        if xhs_alive():
            return True
    return False


def trigger_search(keyword):
    enc = urllib.parse.quote(keyword)
    d = "xhsdiscover://search/result?keyword=%s" % enc
    out = sh(["shell", "am start -a android.intent.action.VIEW -d '%s' %s" % (d, PKG)], timeout=30)
    return ("Starting:" in out) or ("Warning" in out)


def top_activity():
    out = sh(["shell", "dumpsys activity activities | grep mResumedActivity"])
    m = re.search(r"u0 (\S+)", out)
    return m.group(1) if m else "?"


def scroll_more(n=1):
    """向上滑动触发加载更多（不依赖精确坐标，滑屏幕中轴）"""
    for _ in range(n):
        sh(["shell", "input swipe 540 1500 540 500 300"])
        time.sleep(3.5)


def clear_capture():
    for f in glob.glob(os.path.join(CAP, "api_*.json")):
        try:
            os.remove(f)
        except Exception:
            pass
    t = os.path.join(CAP, "traffic.jsonl")
    if os.path.exists(t):
        try:
            os.remove(t)
        except Exception:
            pass


def count_new_responses():
    return len(glob.glob(os.path.join(CAP, "api_*search*notes*.json")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--pages", type=int, default=1, help="额外滚动次数(每次约加载一页)")
    ap.add_argument("--mode", default="png", choices=["png", "jpg100", "jpg", "raw"])
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--skip-download", action="store_true")
    args = ap.parse_args()

    print("=" * 70)
    print("小红书搜索→原图下载 流水线")
    print("  关键词: %s" % args.keyword)
    print("  翻页:   %d 次额外滚动" % args.pages)
    print("  模式:   %s" % args.mode)
    print("=" * 70)

    print("\n[1/5] 清理上次抓包产物…")
    clear_capture()
    print("      done")

    print("\n[2/5] 重启小红书…")
    if restart_app():
        print("      进程已就绪")
    else:
        print("      !! 进程未起来，继续尝试")
    time.sleep(2)

    print("\n[3/5] 触发搜索 deeplink…")
    if trigger_search(args.keyword):
        print("      已下发 deeplink")
    else:
        print("      !! deeplink 下发异常")
    time.sleep(12)

    act = top_activity()
    print("      当前 Activity: %s" % act)
    if "GlobalSearch" not in act:
        print("      !! 未进入搜索页，后续可能无数据")

    if args.pages > 0:
        print("\n[4/5] 滚动加载 %d 次…" % args.pages)
        for i in range(args.pages):
            scroll_more(1)
            print("      第 %d 次滚动后: %s (响应文件 %d)"
                  % (i + 1, top_activity(), count_new_responses()))
            if not xhs_alive():
                print("      !! 进程已崩溃（houdini 转译问题），停止滚动")
                break
    else:
        print("\n[4/5] 跳过滚动")

    if not xhs_alive():
        print("\n      !! 小红书进程已退出，但已抓到的数据仍可用")

    print("\n[5/5] 解析与下载…")
    time.sleep(2)
    files = glob.glob(os.path.join(CAP, "api_*search*notes*.json"))
    print("      搜索响应文件: %d" % len(files))

    rc = os.system('python "%s"' % os.path.join(CAP, "parse_search.py"))
    if rc != 0:
        print("      !! 解析失败")
        return

    if args.skip_download:
        print("\n已跳过下载（--skip-download）")
        return

    os.system('python "%s" --mode %s --workers %d' % (
        os.path.join(CAP, "download_originals.py"), args.mode, args.workers))


if __name__ == "__main__":
    main()
