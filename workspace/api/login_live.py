#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
login_live.py — 可持续扫码登录（二维码自动刷新 + ASCII 输出）

解决的问题：二维码有效期短、窗口不易看到。
做法：
  * 每 90 秒自动重新申请二维码，避免过期
  * 同时输出 ASCII 二维码（可复制到任意等宽环境查看）
  * 把二维码内容写入 qr_url.txt，方便自行生成
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT = r"D:\PYxiangmu\WKnx\workspace\capture"
PROFILE = r"D:\PYxiangmu\WKnx\workspace\chrome-profile-xhs"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

TOTAL = int(sys.argv[1]) if len(sys.argv) > 1 else 900


def ascii_qr(data, border=2):
    """把字符串渲染成 ASCII 二维码（紧凑半块风格）"""
    import qrcode
    qr = qrcode.QRCode(border=border, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(data)
    qr.make(fit=True)
    m = qr.get_matrix()
    lines = []
    # 两个模块一行，用 ▀▄█ 表示，行数减半
    for y in range(0, len(m), 2):
        row = []
        for x in range(len(m)):
            top = m[y][x]
            bot = m[y + 1][x] if y + 1 < len(m) else False
            if top and bot:
                row.append("█")
            elif top and not bot:
                row.append("▀")
            elif not top and bot:
                row.append("▄")
            else:
                row.append(" ")
        lines.append("".join(row))
    return "\n".join(lines)


def main():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        try:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=PROFILE, headless=True, channel="chrome",
                viewport={"width": 1440, "height": 900}, locale="zh-CN",
                user_agent=UA, proxy={"server": "http://127.0.0.1:7897"},
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"])
        except Exception:
            ctx = pw.chromium.launch_persistent_context(
                user_data_dir=PROFILE, headless=True,
                viewport={"width": 1440, "height": 900}, locale="zh-CN",
                user_agent=UA, proxy={"server": "http://127.0.0.1:7897"},
                args=["--no-sandbox"])

        page = ctx.new_page()
        state = {"qr": None, "qr_text": None, "user_me": None}

        def on_resp(r):
            u = r.url
            try:
                if "json" not in r.headers.get("content-type", ""):
                    return
                if "qrcode/create" in u:
                    state["qr_text"] = r.text()
                elif "/user/me" in u:
                    state["user_me"] = r.text()
            except Exception:
                pass

        page.on("response", on_resp)
        page.goto("https://www.xiaohongshu.com/explore",
                  wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        try:
            page.locator("text=登录").first.click(timeout=5000)
        except Exception:
            pass

        t0 = time.time()
        last_qr = 0
        qr_url = None

        print("=" * 70)
        print("可持续扫码登录  总等待 %d 秒  二维码每 90 秒自动刷新" % TOTAL)
        print("=" * 70)

        while time.time() - t0 < TOTAL:
            # 已登录？
            if state["user_me"]:
                try:
                    j = json.loads(state["user_me"])
                    d = j.get("data") or {}
                    if j.get("success") and j.get("code") == 0 and not d.get("guest"):
                        print("\n>>> 登录成功！ user_id=%s" % d.get("user_id"))
                        with open(os.path.join(OUT, "login_ok.json"), "w", encoding="utf-8") as f:
                            json.dump({"ok": True, "user_id": d.get("user_id"),
                                       "elapsed": round(time.time() - t0, 1)},
                                      f, ensure_ascii=False, indent=2)
                        ctx.close()
                        return
                except Exception:
                    pass

            # 生成/刷新二维码
            if time.time() - last_qr > 90 and state["qr_text"]:
                try:
                    j = json.loads(state["qr_text"])
                    d = j.get("data") or {}
                    u = d.get("url")
                    if u and u != qr_url:
                        qr_url = u
                        last_qr = time.time()
                        with open(os.path.join(OUT, "qr_url.txt"), "w", encoding="utf-8") as f:
                            f.write(u)
                        import qrcode
                        img = qrcode.make(u)
                        img.save(os.path.join(OUT, "web_qrcode.png"))
                        print("\n" + "=" * 70)
                        print("【第 %d 次二维码】 %s" % (
                            int((time.time() - t0) // 90) + 1,
                            time.strftime("%H:%M:%S")))
                        print("=" * 70)
                        print(ascii_qr(u))
                        print("=" * 70)
                        print("内容: %s" % u[:130])
                        print("请用小红书 App 扫码；本二维码有效期约 2 分钟")
                        # 刷新一次触发新状态
                        state["qr_text"] = None
                        try:
                            page.reload(wait_until="domcontentloaded", timeout=30000)
                            time.sleep(3)
                            try:
                                page.locator("text=登录").first.click(timeout=4000)
                            except Exception:
                                pass
                        except Exception:
                            pass
                except Exception as e:
                    print("  二维码处理异常: %s" % str(e)[:100])

            time.sleep(2)

        print("\n等待超时，未检测到登录。")
        result = {"ok": False, "error": "timeout"}
        with open(os.path.join(OUT, "login_ok.json"), "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        ctx.close()


if __name__ == "__main__":
    main()
