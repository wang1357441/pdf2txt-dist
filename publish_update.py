# -*- coding: utf-8 -*-
"""一键发布更新（作者自用，别让程序里 import 它）。

做的事：
  1. 从 pdf2txt.py 读出 APP_VERSION（唯一版本来源，不手抄）；
  2. 算 dist/PDFtoTXT.exe 的 sha256；
  3. 生成 manifest.json（清单里同时写「镜像站」和「官方源」两个下载地址，
     程序下载时镜像站优先、官方兜底）；
  4. 用 GitHub Git Data API 把 manifest.json 提交到 main 分支
     （这样 raw.githubusercontent.com 和 jsDelivr 镜像都能拿到清单）；
  5. 建一个 vX.Y.Z 的 Release，并把 exe / 源码包作为附件传上去
     （这样「官方源」和「镜像站」两个地址都有东西可下）；
  6. 最后自己复核一下两条清单地址、两个 exe 地址都通；
  7. 发版会触发 build_uos.yml 在云端把 UOS(aarch64) 单文件打出来并挂到 Release，
     本脚本会等到它挂上并明确告诉你（自动触发没生效就再派发一次兜底）。

安全规矩：
  * 令牌只从环境变量 GITHUB_TOKEN 读（运行时内存），绝不写进任何文件、绝不打印。
  * 不联网下载任何「自动执行」的东西；exe 是你自己刚构建出来的。
  * 重跑安全：同名 Release 已存在就复用，同名附件先删再传；清单没变就不重复提交。

用法：
  python publish_update.py                 # 用代码里的版本号发布
  python publish_update.py --notes "修了xxx"   # 自定义更新说明
  python publish_update.py --dry-run       # 只生成 manifest.json 打印，不碰远端
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tarfile
import sys
import time
import urllib.error
import urllib.request
import winreg

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = "wang1357441/pdf2txt-dist"          # 跟 pdf2txt.py 里的 UPDATE_MANIFEST_URL 对应
OWNER, NAME = REPO.split("/")
EXE = os.path.join(HERE, "dist", "PDFtoTXT.exe")
SRC = os.path.join(HERE, "pdf2txt.py")
MIRROR_HOST = "https://ghproxy.net/"      # 镜像站前缀；后面直接拼官方地址即可

API = "https://api.github.com"
UPLOAD = "https://uploads.github.com"


class ApiError(Exception):
    def __init__(self, code, body):
        super().__init__("GH API %s" % code)
        self.code = code
        self.body = body


def get_token():
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        return tok.strip()
    # 兜底：从注册表 HKCU\Environment 读（和本机其它脚本一致）
    try:
        tok = winreg.QueryValueEx(winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                                 r"Environment"), "GITHUB_TOKEN")[0]
        return str(tok).strip()
    except Exception:
        return ""


def headers():
    return {"Authorization": "Bearer " + TOKEN, "User-Agent": "pdf2txt-publish",
            "Accept": "application/vnd.github+json"}


def api(method, path, body=None, timeout=60, base=API, retry=True):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    url = base + path
    req = urllib.request.Request(url, data=data, method=method, headers=headers())
    if data:
        req.add_header("Content-Type", "application/json")
    for attempt in range(3 if retry else 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read(1 << 22)
            return json.loads(raw.decode("utf-8")) if raw.strip() else {}
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "replace")[:400]
            if attempt == (2 if retry else 0):
                raise ApiError(e.code, msg)
            print("  （%s %s 返回 %s，重试）" % (method, path, e.code))
            time.sleep(2)
        except Exception as e:
            if attempt == (2 if retry else 0):
                raise ApiError(-1, str(e)[:200])
            time.sleep(2)


def read_version():
    with open(SRC, "r", encoding="utf-8") as f:
        txt = f.read()
    m = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', txt)
    if not m:
        raise SystemExit("在 pdf2txt.py 里找不到 APP_VERSION")
    return m.group(1)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def build_manifest(version, sha, size, direct_url, mirror_url, mirror2_url, release_page, notes, linux=None):
    m = {
        "version": version,
        "notes": notes,
        "size": size,                       # 整个安装包体积（字节）
        "url": release_page,                # 源码 / Linux / 统信 UOS 用户打开发布页
        "windows": {
            "urls": [mirror_url, mirror2_url],  # 多个镜像站，程序自动挑最快的
            "url": mirror_url,                  # 兼容老客户端（单地址）
            "fallback": direct_url,             # 官方源兜底
            "sha256": sha,                      # 下载后校验，防篡改
            "size": size,
        },
    }
    if linux:
        m["linux"] = linux                  # 统信 UOS / Linux 自动更新用
    return m


def ensure_uos_binary_on_release(tag):
    """发版后确认 UOS(aarch64) 单文件已被 CI 打出来并挂到 Release 上。

    本脚本建 Release 会触发 build_uos.yml（on: release: published），它会自动
    构建并上传；这里等到它挂上并给作者明确确认。兜底：若自动触发 60 秒后仍未见
    CI 跑起来，就手动再派发一次构建任务。"""
    name = "PDFtoTXT_linux_aarch64"
    print("\n[4/4] 确认 UOS（aarch64）单文件已发布到 GitHub…")
    start = time.time()
    start_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start))
    deadline = start + 480  # 最多等 8 分钟
    dispatched = False
    while time.time() < deadline:
        try:
            rel = api("GET", "/repos/%s/releases/tags/%s" % (REPO, tag), timeout=30, retry=False)
            for a in rel.get("assets", []):
                if a["name"] == name:
                    print("       ✓ 已发布：%s（%d 字节）" % (name, a["size"]))
                    print("       直链：%s" % a["browser_download_url"])
                    return True
        except Exception:
            pass
        # 看有没有「这次」正在跑的 UOS 构建（排除更早的陈旧 run）
        running = False
        try:
            runs = api("GET", "/repos/%s/actions/workflows/build_uos.yml/runs?per_page=5" % REPO,
                       timeout=30, retry=False)
            for r in runs.get("workflow_runs", []):
                if r["status"] == "in_progress" and r.get("created_at", "") >= start_iso:
                    running = True
                    break
        except Exception:
            pass
        elapsed = time.time() - start
        if running:
            print("       CI 正在打 UOS 单文件…（再等等）")
        elif not dispatched and elapsed > 60:
            try:
                api("POST", "/repos/%s/actions/workflows/build_uos.yml/dispatches" % REPO,
                    {"ref": "main", "inputs": {"tag": tag}}, timeout=30, retry=False)
                dispatched = True
                print("       （自动触发未见 CI，已手动再派发构建任务）")
            except Exception as e:
                print("       手动派发失败：%s" % e)
        else:
            print("       等 CI 触发构建…")
        time.sleep(15)
    print("       ⚠ 8 分钟内 UOS 单文件仍未出现在 Release。")
    print("         发布本身已成功（Windows 版不受影响）；UOS 二进制只是还没挂上。")
    print("         可稍后到 GitHub Actions 看构建状态，或手动在 Actions 页面点一下 workflow_dispatch。")
    return False


def main():
    global TOKEN
    dry = "--dry-run" in sys.argv
    TOKEN = get_token()
    if not TOKEN:
        raise SystemExit("找不到 GITHUB_TOKEN：请先把它放到环境变量（或 HKCU\\Environment），别写进文件。")

    version = read_version()
    tag = "v" + version
    release_page = "https://github.com/%s/releases/tag/%s" % (REPO, tag)
    direct_url = "https://github.com/%s/releases/download/%s/PDFtoTXT.exe" % (REPO, tag)
    mirror_url = MIRROR_HOST + direct_url    # ghproxy 格式：前缀 + 官方完整地址
    mirror2_url = "https://gh-proxy.com/" + direct_url  # 备用镜像站，程序会挑最快的
    size = os.path.getsize(EXE)             # 安装包体积，写进清单便于探测与进度显示

    if not os.path.isfile(EXE):
        raise SystemExit("找不到 %s，先构建出 exe 再发布（用 build.py 或 PyInstaller）。" % EXE)

    sha = sha256_file(EXE)
    print("[版本] %s  (tag %s)" % (version, tag))
    print("[exe]  %s  (%d 字节)" % (EXE, os.path.getsize(EXE)))
    print("[sha256] %s" % sha)

    # ---- 统信 UOS / Linux 自动更新用的源码包（在 Windows 这边就能打，纯打包不编译）----
    SRC_FILES = ["pdf2txt.py", "feedback.py", "requirements.txt",
                 "app.png", "app.ico", "make_icon.py"]
    missing = [f for f in SRC_FILES if not os.path.isfile(os.path.join(HERE, f))]
    if missing:
        raise SystemExit("打 UOS 源码包缺少文件：" + ", ".join(missing))
    SRC_TARBALL = os.path.join(HERE, "pdf2txt_source.tar.gz")
    print("\n[打包] 生成 UOS / Linux 源码更新包 pdf2txt_source.tar.gz …")
    with tarfile.open(SRC_TARBALL, "w:gz") as tf:
        for f in SRC_FILES:
            tf.add(os.path.join(HERE, f), arcname=f)
    src_sha = sha256_file(SRC_TARBALL)
    src_size = os.path.getsize(SRC_TARBALL)
    src_url = "https://github.com/%s/releases/download/%s/pdf2txt_source.tar.gz" % (REPO, tag)
    # 可选：UOS 上预先打好的 Linux 二进制（存在就一起发；否则只发源码包）
    BIN = os.path.join(HERE, "dist_uos", "PDFtoTXT_linux_aarch64")
    linux = {"source": {"url": src_url, "sha256": src_sha, "size": src_size}}
    if os.path.isfile(BIN):
        bin_sha = sha256_file(BIN)
        bin_size = os.path.getsize(BIN)
        bin_url = "https://github.com/%s/releases/download/%s/PDFtoTXT_linux_aarch64" % (REPO, tag)
        linux["binary"] = {"url": bin_url, "sha256": bin_sha, "size": bin_size}
        print("       发现预编译 Linux 二进制 dist_uos/PDFtoTXT_linux_aarch64，一并发布（binary 自动更新可用）")
    else:
        print("       没发现 dist_uos/PDFtoTXT_linux_aarch64，只发源码包（源码模式自动更新可用）。")
        print("       想让『打包单文件』也自动更新，请在你的 UOS 上 bash build_uos.sh，")
        print("       把 dist/PDFtoTXT 改名 PDFtoTXT_linux_aarch64 放到 dist_uos/ 再发一次。")

    # 保留线上清单里已有的「预编译二进制」信息（CI 自动打的那份），别被覆盖了
    try:
        cur = api("GET", "/repos/%s/contents/manifest.json?ref=main" % REPO, timeout=30, retry=False)
        if "content" in cur:
            cur_man = json.loads(base64.b64decode(cur["content"]).decode())
            cur_bin = (cur_man.get("linux") or {}).get("binary")
            if cur_bin and "binary" not in linux:
                linux["binary"] = cur_bin
                print("       保留线上已有的 UOS 预编译二进制信息（CI 自动打的）。")
    except Exception:
        pass

    notes = ""
    for i, a in enumerate(sys.argv):
        if a == "--notes" and i + 1 < len(sys.argv):
            notes = sys.argv[i + 1]
    if not notes:
        notes = "PDF 转 TXT（含图片 OCR）。支持检查更新，下载优先走镜像站、官方源兜底。"

    manifest = build_manifest(version, sha, size, direct_url, mirror_url, mirror2_url, release_page, notes, linux=linux)
    text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    out_path = os.path.join(HERE, "manifest.json")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print("[清单] 已写到 %s" % out_path)
    print(text)

    if dry:
        print("[dry-run] 不碰远端。确认无误后去掉 --dry-run 再跑。")
        return

    # ---- 1) 提交 manifest.json 到 main（没变就跳过，避免重复提交）----
    print("\n[1/3] 提交 manifest.json 到 %s 的 main 分支…" % REPO)
    existing = None
    try:
        cur = api("GET", "/repos/%s/contents/manifest.json?ref=main" % REPO, timeout=30, retry=False)
        if "content" in cur:
            existing = base64.b64decode(cur["content"]).decode("utf-8")
    except ApiError as e:
        if e.code != 404:
            raise
    if existing == text:
        print("       manifest.json 内容没变，跳过提交。")
    else:
        ref = api("GET", "/repos/%s/git/refs/heads/main" % REPO)
        commit_sha = ref["object"]["sha"]
        commit = api("GET", "/repos/%s/git/commits/%s" % (REPO, commit_sha))
        base_tree = commit["tree"]["sha"]
        blob = api("POST", "/repos/%s/git/blobs" % REPO,
                   {"content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
                    "encoding": "base64"})
        tree = api("POST", "/repos/%s/git/trees" % REPO,
                   {"base_tree": base_tree,
                    "tree": [{"path": "manifest.json", "mode": "100644",
                              "type": "blob", "sha": blob["sha"]}]})
        new_commit = api("POST", "/repos/%s/git/commits" % REPO,
                        {"message": "release %s: manifest" % version,
                         "tree": tree["sha"], "parents": [commit_sha]})
        api("PATCH", "/repos/%s/git/refs/heads/main" % REPO, {"sha": new_commit["sha"]})
        print("       main 已更新 -> %s" % new_commit["sha"][:10])

    # ---- 2) 建 Release + 传 exe（404 = 还没建，直接建）----
    print("\n[2/2] 建 Release %s 并上传 exe…" % tag)
    rel = {}
    try:
        rel = api("GET", "/repos/%s/releases/tags/%s" % (REPO, tag), timeout=30, retry=False)
    except ApiError as e:
        if e.code == 404:
            rel = {}
        else:
            raise
    if "id" in rel:
        print("       同名 Release 已存在，复用 id=%s" % rel["id"])
    else:
        rel = api("POST", "/repos/%s/releases" % REPO,
                  {"tag_name": tag, "name": tag, "body": notes,
                   "draft": False, "prerelease": False}, timeout=30)
        print("       已建 Release id=%s" % rel["id"])

    rid = rel["id"]
    # 先删同名旧附件，保证可重跑
    want = {"PDFtoTXT.exe", "pdf2txt_source.tar.gz", "PDFtoTXT_linux_aarch64"}
    for asset in rel.get("assets", []):
        if asset.get("name") in want:
            api("DELETE", "/repos/%s/releases/assets/%s" % (REPO, asset["id"]), timeout=30)
            print("       已删旧附件 %s" % asset.get("name"))

    # 要上传的附件清单（exe 必传；源码包必传；预编译二进制可选）
    uploads = [(EXE, "PDFtoTXT.exe")]
    uploads.append((SRC_TARBALL, "pdf2txt_source.tar.gz"))
    if os.path.isfile(BIN):
        uploads.append((BIN, "PDFtoTXT_linux_aarch64"))

    def _upload_asset(path, name):
        with open(path, "rb") as f:
            data = f.read()
        up_url = "%s/repos/%s/releases/%s/assets?name=%s" % (UPLOAD, REPO, rid, name)
        for attempt in range(3):
            h = headers()
            h["Content-Type"] = "application/octet-stream"
            req = urllib.request.Request(up_url, data=data, method="POST", headers=h)
            try:
                with urllib.request.urlopen(req, timeout=600) as r:
                    j = json.loads(r.read().decode("utf-8"))
                print("       已上传附件：%s (id=%s)" % (j.get("name"), j.get("id")))
                return True
            except urllib.error.HTTPError as e:
                print("       %s 上传第 %d 次失败: %s %s" % (
                    name, attempt + 1, e.code, e.read().decode("utf-8", "replace")[:200]))
                time.sleep(3)
            except Exception as e:
                print("       %s 上传第 %d 次异常: %s" % (name, attempt + 1, e))
                time.sleep(3)
        return False

    print("\n[2/3] 上传附件…")
    ok_all = True
    for path, name in uploads:
        if not _upload_asset(path, name):
            ok_all = False
    if not ok_all:
        raise SystemExit("有附件上传失败，请稍后重跑本脚本（manifest 已提交，不会重复建分支）。")

    # ---- 3) 自检：核对线上内容（用 api.github.com，沙箱也通，比直接 HEAD github.com 稳）----
    print("\n[3/3] 自检：复核线上发布内容…")
    raw_manifest = "https://raw.githubusercontent.com/%s/main/manifest.json" % REPO
    jsdelivr = "https://cdn.jsdelivr.net/gh/%s@main/manifest.json" % REPO

    def head(url, timeout=40):
        req = urllib.request.Request(url, headers={"User-Agent": "pdf2txt-check"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, int(r.headers.get("Content-Length") or 0)
        except urllib.error.HTTPError as e:
            return e.code, 0
        except Exception:
            return -1, 0

    st1, _ = head(raw_manifest)
    st2, _ = head(jsdelivr)
    print("  清单 raw       %s" % st1)
    print("  清单 jsDelivr  %s" % st2)

    # 用 API 核对 Release 附件大小（直接 HEAD github.com 在沙箱里会被挡，所以用 API）
    ok_assets = True
    try:
        rel = api("GET", "/repos/%s/releases/tags/%s" % (REPO, tag), timeout=30, retry=False)
        have = {a["name"]: a["size"] for a in rel.get("assets", [])}
        print("  Release 附件：")
        for nm, sz in have.items():
            print("    %s  (%d 字节)" % (nm, sz))
        ok_assets = (have.get("PDFtoTXT.exe") == size and
                     have.get("pdf2txt_source.tar.gz") == src_size)
        if not ok_assets:
            print("    ⚠ 附件大小对不上，请检查上传。")
    except Exception as e:
        print("  Release 附件核对失败：%s" % e)
        ok_assets = False

    ok_wire = (st1 == 200 and st2 == 200 and ok_assets)
    print("\n判定：%s" % ("✓ 清单与附件都核对通过，更新通道可用" if ok_wire else "✗ 有不对的地方，见上"))

    if ok_wire:
        ensure_uos_binary_on_release(tag)

    print("\n发布完成：")
    print("  清单（程序读这个）：%s" % raw_manifest)
    print("  发布页（用户手动下）：%s" % release_page)
    print("  exe 镜像下载：%s" % mirror_url)
    print("  UOS 源码更新包：%s" % src_url)
    if not ok_wire:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        print("GH_API_ERR", e.code, e.body[:300])
        sys.exit(1)
