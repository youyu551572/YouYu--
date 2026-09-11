#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
parse_deeplinks.py — 精确解析 AndroidManifest 的 intent-filter，找出 image_search / search 的完整 deeplink 组合
"""
import io
import re
import sys
import xml.etree.ElementTree as ET

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

MF = r"D:\PYxiangmu\WKnx\workspace\xhs-jadx\resources\AndroidManifest.xml"
AND = "http://schemas.android.com/apk/res/android"


def an(elem, name):
    return elem.get("{%s}%s" % (AND, name))


def main():
    tree = ET.parse(MF)
    root = tree.getroot()
    app = root.find("application")
    if app is None:
        print("no application node")
        return

    print("=== deeplink 组合 (activity + scheme + host + path) ===\n")
    hits = []
    for act in app.iter("activity"):
        name = an(act, "name") or ""
        exported = an(act, "exported")
        for flt in act.findall("intent-filter"):
            datas = flt.findall("data")
            if not datas:
                continue
            # 收集该 filter 下的 scheme/host/path 组合
            schemes = set()
            hosts = set()
            paths = set()
            for d in datas:
                s = an(d, "scheme")
                h = an(d, "host")
                p = an(d, "pathPrefix") or an(d, "pathPattern") or an(d, "path")
                if s:
                    schemes.add(s)
                if h:
                    hosts.add(h)
                if p:
                    paths.add(p)
            interesting = hosts & {"image_search", "search", "instore_search", "note", "topic"}
            if interesting or "image_search" in hosts:
                hits.append((name, exported, schemes, hosts, paths))

    for name, exported, schemes, hosts, paths in hits:
        print("ACTIVITY: %s" % name)
        print("  exported : %s" % exported)
        print("  schemes  : %s" % ", ".join(sorted(schemes)))
        ih = sorted(hosts & {"image_search", "search", "instore_search"})
        print("  hot hosts: %s" % ", ".join(ih))
        if paths:
            pl = sorted(paths)
            print("  paths    : %s" % ", ".join(pl[:25]))
            if len(pl) > 25:
                print("             ... (+%d more)" % (len(pl) - 25))
        print()

    # 专门打印 image_search 相关的全部组合
    print("=== image_search 专向 ===")
    for act in app.iter("activity"):
        name = an(act, "name") or ""
        for flt in act.findall("intent-filter"):
            for d in flt.findall("data"):
                if an(d, "host") == "image_search":
                    print("  activity=%s scheme=%s host=%s path=%s pathPrefix=%s pathPattern=%s" % (
                        name, an(d, "scheme"), an(d, "host"),
                        an(d, "path"), an(d, "pathPrefix"), an(d, "pathPattern")))


if __name__ == "__main__":
    main()
