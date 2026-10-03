#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 用：把构建好的 dist/PDFtoTXT（aarch64 单文件）上传到 Release，
并把它的地址 / sha256 / 体积写进 manifest.json 的 linux.binary 段，让 UOS 版也能自动更新。
纯标准库，不依赖任何第三方包（CI 容器里只有系统 python3）。"""
import os
import sys
import json
import base64
import hashlib
import urllib.request
import urllib.error

TOKEN = os.environ["GH_TOKEN"]
REPO = os.environ.get("REPO", "wang1357441/pdf2txt-dist")
TAG = os.environ["TAG"]
API = "https://api.github.com"
UP = "https://uploads.github.com"
H = {"Authorization": "Bearer " + TOKEN, "User-Agent": "uos-build",
     "Accept": "application/vnd.github+json"}


def api(method, path, body=None, base=API, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers=H)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        return json.loads(raw.decode()) if raw.strip() else {}
    except urllib.error.HTTPError as e:
        print("API %s %s -> %s" % (method, path, e.code))
        raise


def main():
    binpath = "dist/PDFtoTXT"
    if not os.path.isfile(binpath):
        print("!! 找不到 %s，构建可能失败了" % binpath)
        sys.exit(1)

    sha = hashlib.sha256()
    sz = 0
    with open(binpath, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            sha.update(blk)
            sz += len(blk)
    sha = sha.hexdigest()
    print("binary sha256=%s size=%d" % (sha, sz))

    # 体积守卫：正常成品约 100MB+；若远小于此，说明构建只出了半成品，
    # 不能再上传（否则 Release 上挂的是坏文件）。直接报错退出，让 CI 显式失败。
    if sz < 100 * 1024 * 1024:
        print("!! 二进制只有 %d 字节（<100MB），疑似半成品，拒绝上传。" % sz)
        sys.exit(1)

    rel = api("GET", "/repos/%s/releases/tags/%s" % (REPO, TAG))
    rid = rel["id"]
    for a in rel.get("assets", []):
        if a["name"] == "PDFtoTXT_linux_aarch64":
            api("DELETE", "/repos/%s/releases/assets/%s" % (REPO, a["id"]))
            print("  已删旧附件 PDFtoTXT_linux_aarch64")

    with open(binpath, "rb") as f:
        data = f.read()
    up = "%s/repos/%s/releases/%s/assets?name=PDFtoTXT_linux_aarch64" % (UP, REPO, rid)
    req = urllib.request.Request(up, data=data, method="POST",
                                 headers={**H, "Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=600) as r:
        j = json.loads(r.read().decode())
    print("  已上传附件：%s (id=%s)" % (j.get("name"), j.get("id")))

    # 注意：不再由 CI 改写 manifest.json 的 linux.binary。
    # 原因：本机/CI 一旦构建出“半成品”二进制，就会把错误的 sha256/体积写进清单，
    # 导致 UOS 自动更新校验失败。清单由 publish_update.py（作者本地，用真实上传后的
    # 附件核对）统一负责，CI 只负责把二进制传到 Release 当附件即可。
    print("  （CI 不改动 manifest.json；linux.binary 由发布脚本统一维护）")


if __name__ == "__main__":
    main()
