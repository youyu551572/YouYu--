#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
xhs_inject.py — 保持 frida 会话常驻的注入器
用法:
  python xhs_inject.py spawn   # 冷启动注入
  python xhs_inject.py attach  # 附加到已运行进程
"""
import sys
import time
import signal
import io

import frida

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

DEVICE_ID = "127.0.0.1:27042"
PKG = "com.xingin.xhs"
SCRIPT_PATH = r"D:\PYxiangmu\WKnx\workspace\capture\xhs_capture.js"

running = True


def on_message(msg, data):
    t = msg.get("type")
    if t == "send":
        print("[SEND] " + str(msg.get("payload")))
    elif t == "log":
        print("[LOG ] " + str(msg.get("payload")))
    elif t == "error":
        print("[ERR ] " + str(msg.get("description")))
        print(msg.get("stack", ""))
    sys.stdout.flush()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "spawn"
    device = frida.get_device_manager().add_remote_device(DEVICE_ID)
    print("[*] device: %s" % device)

    if mode == "spawn":
        pid = device.spawn([PKG])
        print("[*] spawned pid=%s" % pid)
        session = device.attach(pid)
    else:
        session = device.attach(PKG)
        pid = session.pid
        print("[*] attached pid=%s" % pid)

    with open(SCRIPT_PATH, "r", encoding="utf-8") as f:
        code = f.read()

    script = session.create_script(code)
    script.on("message", on_message)
    script.load()
    print("[*] script loaded")

    if mode == "spawn":
        device.resume(pid)
        print("[*] resumed")

    def _stop(*_):
        global running
        running = False

    signal.signal(signal.SIGINT, _stop)
    print("[*] session alive — Ctrl+C to detach")
    while running:
        time.sleep(1)

    try:
        session.detach()
    except Exception:
        pass
    print("[*] detached")


if __name__ == "__main__":
    main()
