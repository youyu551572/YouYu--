#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
frida_run.py — 在已运行的小红书上跑一个脚本，收集输出后退出
用法: python frida_run.py <script.js> [duration_sec] [--spawn]
"""
import sys
import io
import time

import frida

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

DEVICE_ID = "127.0.0.1:27042"
PKG = "com.xingin.xhs"


def main():
    script_path = sys.argv[1]
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
    spawn = "--spawn" in sys.argv

    device = frida.get_device_manager().add_remote_device(DEVICE_ID)

    if spawn:
        pid = device.spawn([PKG])
        session = device.attach(pid)
    else:
        session = device.attach(PKG)
        pid = None

    with open(script_path, "r", encoding="utf-8") as f:
        code = f.read()

    def on_message(msg, data):
        t = msg.get("type")
        if t == "send":
            print(str(msg.get("payload")))
        elif t == "log":
            print(str(msg.get("payload")))
        elif t == "error":
            print("[ERR] " + str(msg.get("description")))
            print(msg.get("stack", ""))
        sys.stdout.flush()

    script = session.create_script(code)
    script.on("message", on_message)
    script.load()

    if spawn and pid is not None:
        device.resume(pid)

    time.sleep(duration)
    try:
        session.detach()
    except Exception:
        pass


if __name__ == "__main__":
    main()
