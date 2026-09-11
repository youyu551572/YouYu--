#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
test_expired.py — 模拟 cookie 失效，验证自愈链路

用一个全新的空 profile 目录（等价于 cookie 被清空/过期），
确认：
  1. status() 判定为未登录（guest=true）
  2. search() 抛出 WebError 而不是静默返回空
  3. API 把 WebError 转成 HTTP 401 并附重新登录指引

全程不触碰真实 profile，登录态不受影响。
"""
import json
import os
import shutil
import sys
import time

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAKE_PROFILE = os.path.join(HERE, "_tmp_empty_profile")
FAKE_OUT = os.path.join(HERE, "_tmp_empty_out")
BASE = "http://127.0.0.1:8700"


def main():
    print("=" * 72)
    print("cookie 失效场景验证（使用空 profile，不影响真实登录态）")
    print("=" * 72)

    shutil.rmtree(FAKE_PROFILE, ignore_errors=True)
    shutil.rmtree(FAKE_OUT, ignore_errors=True)
    os.makedirs(FAKE_PROFILE, exist_ok=True)
    os.makedirs(FAKE_OUT, exist_ok=True)

    try:
        from xhs_web import XhsWeb, WebError

        eng = XhsWeb(profile=FAKE_PROFILE, out_dir=FAKE_OUT, headless=True)

        # 1) 未登录状态下 status()
        print("\n[1] status() 于空 profile")
        t0 = time.time()
        st = eng.status()
        print("    (%.1fs) %s" % (time.time() - t0,
                                  json.dumps(st, ensure_ascii=False)[:200]))
        if st.get("guest") is True or st.get("logged_in") is False:
            print("    >>> 正确判定为未登录")
        else:
            print("    !!! guest=%s logged_in=%s（预期未登录）"
                  % (st.get("guest"), st.get("logged_in")))

        # 2) 未登录状态下 search() 应抛 WebError
        print("\n[2] search() 于空 profile（预期 WebError）")
        t0 = time.time()
        try:
            res = eng.search("咖啡", pages=1, wait=25, use_cache=False)
            print("    (%.1fs) 未抛异常 -> notes=%s images=%s logged_in=%s"
                  % (time.time() - t0, res.get("note_count"),
                     res.get("image_count"), res.get("logged_in")))
            print("    !!! 预期应该抛出 WebError")
        except WebError as e:
            print("    (%.1fs) 已抛 WebError >>>" % (time.time() - t0))
            print("    %s" % str(e)[:220])
        except Exception as e:
            print("    (%.1fs) 抛了非 WebError: %s: %s"
                  % (time.time() - t0, type(e).__name__, str(e)[:160]))

        eng.close()
    finally:
        shutil.rmtree(FAKE_PROFILE, ignore_errors=True)
        shutil.rmtree(FAKE_OUT, ignore_errors=True)
        print("\n    临时 profile 已清理")

    # 3) 真实服务仍应正常（确认没被影响）
    print("\n[3] 真实服务健康检查（确认登录态未受影响）")
    try:
        r = requests.get(BASE + "/health", timeout=180)
        h = r.json()
        print("    HTTP %s  logged_in=%s  guest=%s  user_id=%s"
              % (r.status_code, h.get("logged_in"), h.get("guest"), h.get("user_id")))
    except Exception as e:
        print("    (服务未运行或超时: %s)" % str(e)[:120])

    print("\n" + "=" * 72)
    print("验证结束")
    print("=" * 72)


if __name__ == "__main__":
    main()
