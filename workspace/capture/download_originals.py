#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
download_originals.py — 小红书笔记图片原图批量下载器（画质不压缩）

用法:
  python download_originals.py                 # 默认 PNG 无损模式
  python download_originals.py --mode jpg100   # 最高质量 JPEG
  python download_originals.py --mode raw      # 原始字节(HEIC等)
  python download_originals.py --workers 8     # 并发数

设计要点:
  * 原图 = 剥离全部查询参数后重新附加 format/png（或 w/0/format/jpg/q/100）
    经实测：App 内搜索列表图仅 576x766/w576/heif/q58，
    剥离参数后可达 1924x2560，面积差 11.6 倍。
  * 以笔记为单位建目录，附带笔记元数据。
  * 默认跳过已存在且体积合理的文件，可安全重跑。
"""
import argparse
import concurrent.futures as futures
import io
import json
import os
import re
import sys
import threading
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

CAP = r"D:\PYxiangmu\WKnx\workspace\capture"
NOTES = os.path.join(CAP, "notes.json")
DEFAULT_OUT = r"D:\PYxiangmu\WKnx\workspace\downloads\xhs"

PROXY = {"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897"}
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

MODE_SUFFIX = {
    "png": ("png", "?imageView2/2/format/png"),
    "jpg100": ("jpg", "?imageView2/2/w/0/format/jpg/q/100"),
    "jpg": ("jpg", "?imageView2/2/format/jpg"),
    "raw": ("bin", ""),
}

_lock = threading.Lock()
_stats = {"ok": 0, "skip": 0, "fail": 0, "bytes": 0}

# 大图经代理传输容易中途断流，配置带退避的重试与连接池
_retry = Retry(
    total=5,
    connect=5,
    read=5,
    backoff_factor=1.2,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=frozenset(["GET"]),
)
_session = requests.Session()
_adapter = HTTPAdapter(max_retries=_retry, pool_connections=16, pool_maxsize=16)
_session.mount("http://", _adapter)
_session.mount("https://", _adapter)


def strip_query(u):
    return u.split("?", 1)[0]


def safe_name(s, maxlen=80):
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", s or "")
    return s[:maxlen] or "unnamed"


def imread_size(data):
    """不依赖解码器，从文件头读尺寸"""
    import struct
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return w, h
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            m = data[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            if m in (0xD8, 0xD9) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            i += 2 + seg
        return None, None
    if data[4:8] == b"ftyp":
        idx = data.find(b"ispe")
        if idx > 0 and idx + 16 <= len(data):
            try:
                w, h = struct.unpack(">II", data[idx + 8:idx + 16])
                return w, h
            except Exception:
                pass
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        if data[12:16] == b"VP8X" and len(data) >= 30:
            import struct
            w = int.from_bytes(data[24:27], "little") + 1
            h = int.from_bytes(data[27:30], "little") + 1
            return w, h
    return None, None


def download_one(task):
    url, path, declared = task
    tmp = path + ".part"
    try:
        # 已有完整文件则跳过；留下 .part 说明上次中断，需要重下
        if os.path.exists(path) and os.path.getsize(path) > 1024:
            with _lock:
                _stats["skip"] += 1
            return ("skip", path, os.path.getsize(path), None)

        r = _session.get(url, headers=UA, proxies=PROXY, timeout=(15, 180), stream=True)
        if r.status_code != 200:
            with _lock:
                _stats["fail"] += 1
            return ("fail", path, 0, "HTTP %s" % r.status_code)

        os.makedirs(os.path.dirname(path), exist_ok=True)
        n = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=256 * 1024):
                if chunk:
                    f.write(chunk)
                    n += len(chunk)

        if n < 512:
            os.remove(tmp)
            with _lock:
                _stats["fail"] += 1
            return ("fail", path, 0, "too small (%d bytes), likely error payload" % n)

        # 校验完整性：有 Content-Length 时必须完全一致，防止截断图被当成成功
        clen = r.headers.get("Content-Length")
        if clen and clen.isdigit() and int(clen) != n:
            os.remove(tmp)
            with _lock:
                _stats["fail"] += 1
            return ("fail", path, 0, "truncated: got %d / expected %s" % (n, clen))

        os.replace(tmp, path)
        with open(path, "rb") as f:
            head = f.read(65536)
        w, h = imread_size(head)
        with _lock:
            _stats["ok"] += 1
            _stats["bytes"] += n
        return ("ok", path, n, (w, h, declared))
    except Exception as e:
        # 清理残片，保证下次能重试
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        with _lock:
            _stats["fail"] += 1
        return ("fail", path, 0, str(e)[:120])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="png", choices=list(MODE_SUFFIX.keys()))
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    ext, suffix = MODE_SUFFIX[args.mode]

    with open(NOTES, "r", encoding="utf-8") as f:
        notes = json.load(f)

    tasks = []
    for n in notes:
        nid = safe_name(n.get("note_id") or "unknown", 40)
        title = safe_name(n.get("title"), 50)
        folder = os.path.join(args.out, "%s_%s" % (nid, title))
        for idx, im in enumerate(n.get("images") or [], 1):
            b = im.get("base")
            if not b:
                continue
            tail = b.rstrip("/").split("/")[-1]
            fname = "%02d_%s.%s" % (idx, tail, ext)
            tasks.append((
                b + suffix if suffix else b,
                os.path.join(folder, fname),
                (im.get("width"), im.get("height")),
            ))

    print("=" * 66)
    print("小红书原图下载器   mode=%s  workers=%d" % (args.mode, args.workers))
    print("笔记: %d   图片任务: %d" % (len(notes), len(tasks)))
    print("输出: %s" % args.out)
    print("=" * 66)

    t0 = time.time()
    results = []
    with futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for res in ex.map(download_one, tasks):
            results.append(res)
            st, path, size, extra = res
            rel = os.path.relpath(path, args.out)
            if st == "ok":
                if isinstance(extra, tuple):
                    w, h, dec = extra
                    print("  [OK  ] %-58s %8.1f KB  %sx%s (声明 %sx%s)" % (
                        rel[:58], size / 1024, w, h, dec[0] if dec else "?", dec[1] if dec else "?"))
                else:
                    print("  [OK  ] %-58s %8.1f KB" % (rel[:58], size / 1024))
            elif st == "skip":
                print("  [SKIP] %-58s (已存在)" % rel[:58])
            else:
                print("  [FAIL] %-58s %s" % (rel[:58], extra))

    dt = time.time() - t0
    print("\n" + "=" * 66)
    print("完成: 成功 %d  跳过 %d  失败 %d" % (_stats["ok"], _stats["skip"], _stats["fail"]))
    print("总下载: %.2f MB   耗时: %.1fs" % (_stats["bytes"] / 1024 / 1024, dt))

    # 汇总报告
    rep = os.path.join(args.out, "_download_report.json")
    os.makedirs(args.out, exist_ok=True)
    with open(rep, "w", encoding="utf-8") as f:
        json.dump({
            "mode": args.mode,
            "notes": len(notes),
            "tasks": len(tasks),
            "stats": _stats,
            "elapsed_sec": round(dt, 2),
            "results": [{"status": r[0], "path": r[1], "size": r[2]} for r in results],
        }, f, ensure_ascii=False, indent=2)
    print("报告: %s" % rep)


if __name__ == "__main__":
    main()
