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

    m = api("GET", "/repos/%s/contents/manifest.json?ref=main" % REPO)
    mtxt = base64.b64decode(m["content"]).decode()
    man = json.loads(mtxt)
    man.setdefault("linux", {})
    man["linux"]["binary"] = {
        "url": "https://github.com/%s/releases/download/%s/PDFtoTXT_linux_aarch64" % (REPO, TAG),
        "sha256": sha,
        "size": sz,
    }
    newtxt = json.dumps(man, ensure_ascii=False, indent=2) + "\n"
    api("PUT", "/repos/%s/contents/manifest.json" % REPO, {
        "message": "ci: add UOS aarch64 binary to manifest (%s)" % TAG,
        "content": base64.b64encode(newtxt.encode()).decode(),
        "sha": m["sha"],
    })
    print("  manifest 已补上 linux.binary（UOS 自动更新可用）。")


if __name__ == "__main__":
    main()
