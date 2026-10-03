# -*- coding: utf-8 -*-
"""PDF 转 TXT —— 哈夫克图片同款深色界面，但这是一个独立、干净、可商用的小工具。

功能：
  * 单个文件 / 批量文件 两种选择模式；
  * 把 PDF 里的「原生文字层」直接扒成 .txt；
  * 如果 PDF 含图片（扫描件 / 图片里的字），可选 OCR 把图片里的字也扒下来；
  * 输出 UTF-8（带 BOM，记事本不乱码）；
  * 额外支持无界面模式：pdf2txt.exe --headless --in a.pdf --out a.txt --mode image
    （方便自动批量 / 脚本调用，也方便自测）。

许可证（全部可商用、可再分发）：
  pypdfium2  -> BSD-3-Clause   （Chrome 同款 PDF 引擎，负责读文字 + 把页面渲染成图）
  RapidOCR   -> Apache-2.0     （负责图片里的字，自带模型，离线可用）
  opencv / pillow / onnxruntime -> 上述 OCR 的配套依赖
"""
from __future__ import annotations

import os
import re
import sys
import math
import time
import threading
import queue
import traceback
import subprocess

import tkinter as tk
from tkinter import font as tkfont, filedialog, messagebox
import tkinter.scrolledtext as scrolledtext

import json
import hashlib
import tarfile
import tempfile
import urllib.request
from urllib.parse import urlparse

import feedback  # 反馈模块：点「反馈」发邮件给作者 + 出错自动记录并询问是否发送

import pypdfium2 as pdfium
from PIL import Image
import numpy as np

# RapidOCR 比较重，用到（用户选了“含图片”）时才导入，纯文字模式启动更快。
RapidOCR = None
_OCR_ENGINE = None
_OCR_FAILED = False


# ==========================================================================
# 版本 & 更新（获取更新能力的核心配置，全在这里）
# ==========================================================================
# 当前版本号（以后发新版，只改这一行就行）。程序拿它和线上清单比对。
APP_VERSION = "1.0.11"

# 更新清单（manifest）地址：一个公开、不需要密码就能访问的 JSON 文件。
# 推荐放在 GitHub 仓库里（见 UPDATE.md 说明）。下面这行是示例占位，
# 你发版前把它换成自己的地址即可，只改这一处。
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/wang1357441/pdf2txt-dist/main/manifest.json"

# 清单镜像站（国内拉 raw.githubusercontent 慢，用 jsDelivr 兜底；出错自动退回直连）。
UPDATE_MANIFEST_MIRROR = "https://cdn.jsdelivr.net/gh/wang1357441/pdf2txt-dist@main/manifest.json"

# 是否一打开程序就自动检查更新（默认关，免得每次启动都联网；想开改成 True）。
AUTO_CHECK_ON_START = False

# ==========================================================================
# 商用激活（联网校验：每个激活码仅限激活一次；吊销可经服务端作废）
# ==========================================================================
# 激活服务器（Cloudflare Worker，负责校验激活码；密钥在服务端，程序里只有这个网址）。
# 激活后端已迁到 GitHub 上的 licenses.json（见下方 LICENSE_REPO / LICENSE_SOURCES），
# 不再依赖国内连不上的 workers.dev。文件里只存激活码的 SHA-256 哈希，不泄露真码。
# 激活码字符表（去掉易混的 0/O/1/I/L 等）。
LICENSE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
LICENSE_CODE_LEN = 16   # 4 组 x 4 位


# ==========================================================================
# 主题（配色、圆角、字体，全部集中一处，不抄字面量）
# ==========================================================================
BG_APP = "#101114"
BG_PANEL = "#181a20"
BG_ELEV = "#1e2128"
BG_HOVER = "#24272f"
BG_ACTIVE = "#2d313a"
BG_SUNKEN = "#0d0e11"
STROKE = "#2a2e37"
STROKE_SOFT = "#21242b"

FG_TEXT = "#eceef2"
FG_DIM = "#8b93a1"
FG_MUTED = "#5d6572"
FG_ON_ACCENT = "#ffffff"

ACCENT = "#4c8dff"
ACCENT_HOVER = "#5f9bff"
ACCENT_PRESS = "#3d7ae8"
FG_OK = "#3ddc84"
FG_WARN = "#ffc94d"
FG_BAD = "#ff5f56"

RADIUS_SM = 6
RADIUS_MD = 10
RADIUS_LG = 14
PAD = 12

FONT_UI = "Microsoft YaHei UI"
FONT_MONO = "Consolas"


def font(size: int, weight: str = "normal") -> tuple:
    return (FONT_UI, size, weight)


def mono(size: int, weight: str = "normal") -> tuple:
    return (FONT_MONO, size, weight)


def resource_path(rel: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def open_path(path: str):
    """跨平台打开文件/文件夹：Windows 用 startfile，Linux（含统信 UOS）用 xdg-open，macOS 用 open。"""
    if not path or not os.path.exists(path):
        return
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)
        elif sys.platform.startswith("darwin"):
            subprocess.run(["open", path], check=False)
        else:  # Linux / 统信 UOS 等
            subprocess.run(["xdg-open", path], check=False)
    except Exception:
        pass


# ==========================================================================
# 版本比较 & 更新检查（纯函数 / 标准库，不依赖任何第三方包）
# ==========================================================================
def _parse_ver(v):
    """把 '1.2.3' 变成 (1, 2, 3)，方便比大小。"""
    parts = re.findall(r"\d+", v or "")
    nums = [int(p) for p in parts[:3]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _version_newer(remote, current):
    """remote 版本是不是比 current 新。比不出就当没新（保守）。"""
    try:
        return _parse_ver(remote) > _parse_ver(current)
    except Exception:
        return False


def fetch_manifest(url, timeout=10):
    """下载并解析更新清单 JSON。失败一律抛异常，由调用方决定怎么提示。"""
    req = urllib.request.Request(url, headers={"User-Agent": "PDFtoTXT-Updater/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def _download_file(url, dest, timeout=60, progress=None, expect_size=0):
    """下载文件到 dest，返回它的 sha256（十六进制小写）。progress(got, total) 可选。

    断点续传（重点）：
    - 先下到 dest + ".part" 临时文件，全部收完、校验通过后才原子替换成 dest。
    - 上次下到一半断了，这次会从上次的位置接着下（发 HTTP Range 请求），而不是从头重来。
      专门对付网络不稳、大文件（100+MB）容易断的情况。
    - 用一个 .part.meta 小文件记住「这个 .part 是哪个 url 下的」，避免不同版本 / 不同源
      的半截文件串味导致损坏（例如 v1.0.8 的半截被误当成 v1.0.9 的续传）。
    - 服务器若不支持 Range（返回 200 整包），自动退回「从头重写」，绝不损坏文件。
    - 中途断了：保留 .part + .meta，下次同一个 url 一来就接着下。
    """
    import time
    tmp = dest + ".part"
    meta = dest + ".part.meta"
    resume_from = 0
    # 看本地有没有「同一个 url」的半截文件可以续
    if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
        same_url = False
        try:
            with open(meta, "r", encoding="utf-8") as fh:
                same_url = fh.read().strip() == url
        except Exception:
            same_url = False
        cur = os.path.getsize(tmp)
        if same_url and (not expect_size or cur < expect_size):
            resume_from = cur
        else:
            # 不是同一个 url，或已经下满却没替换成功 → 当作废，重头来
            try:
                os.remove(tmp)
            except Exception:
                pass
            try:
                os.remove(meta)
            except Exception:
                pass
            resume_from = 0
    # 记下 url，方便下次续传时判断是不是同一个文件
    try:
        with open(meta, "w", encoding="utf-8") as fh:
            fh.write(url)
    except Exception:
        pass
    headers = {"User-Agent": "PDFtoTXT-Updater/1.0"}
    if resume_from > 0:
        headers["Range"] = "bytes=%d-" % resume_from
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        code = resp.getcode()
        if code == 206:                      # 服务器认 Range：接着下
            remaining = int(resp.headers.get("Content-Length", "0") or "0")
            total = resume_from + remaining
            mode = "ab"
        else:                                # 200 整包：从头重写（绝不损坏）
            total = int(resp.headers.get("Content-Length", "0") or "0")
            mode = "wb"
            resume_from = 0
        hasher = hashlib.sha256()
        # 续传时先把已下的部分算进哈希，保证整包顺序一致（校验才对得上）
        if resume_from > 0:
            with open(tmp, "rb") as pf:
                while True:
                    buf = pf.read(1 << 16)
                    if not buf:
                        break
                    hasher.update(buf)
        got = resume_from
        chunk = 1 << 16
        last_t = time.time()
        try:
            with open(tmp, mode) as f:
                while True:
                    buf = resp.read(chunk)
                    if not buf:
                        break
                    f.write(buf)
                    hasher.update(buf)
                    got += len(buf)
                    if progress and total:
                        now = time.time()
                        if now - last_t >= 0.2 or got >= total:   # 节流，别刷爆 UI 线程
                            last_t = now
                            progress(got, total)
            os.replace(tmp, dest)   # 全下完才替换目标；中途失败只删临时文件
        except Exception:
            # 中途失败：保留 .part + meta，下次同一个 url 接着下（不删！）
            raise
    # 成功了：清掉 meta（.part 已被 replace 掉）
    try:
        os.remove(meta)
    except Exception:
        pass
    return hasher.hexdigest()


# ==========================================================================
# 并行分块下载（叠带宽，目标 ≥1MB/s）
# --------------------------------------------------------------------------
# 思路：大文件（100+MB）一个连接经常被限速到几百 KB/s。把它切成 N 段，每段
# 各自开一个连接同时下不同区段，把零散带宽「叠」起来，整体速度就能上 1MB/s。
# 下完按顺序拼成一个整文件，再整体算 sha256 防篡改（和单连接一致）。
# 断点续传保留在「块」粒度：某块下坏只重下那一块，已下好的不动。
# ==========================================================================
CHUNK_TARGET = 4 * 1024 * 1024     # 每块目标大小 4MB（块越多，能叠的带宽越多）
MIN_CHUNKS = 6
MAX_CHUNKS = 16


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _probe_size(url, expect_size=0):
    """探一个下载地址：返回 (total 字节, 是否支持 Range)。

    发一个 Range: bytes=0-0 的小请求，能从 Content-Range 里拿到文件总大小；
    同时验证服务器认不认 Range（认的话才能分块并行下）。
    拿不到 / 返回整包（200）/ 大小明显对不上（多半是镜像回了个错误页）就当
    「不支持 Range」，交给单连接 _download_file 兜底。"""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "PDFtoTXT-Updater/1.0",
                          "Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.getcode() != 206:
                return 0, False
            cr = resp.headers.get("Content-Range") or ""
            if "/" not in cr:
                return 0, False
            total = int(cr.rsplit("/", 1)[1])
            if expect_size and total and abs(total - expect_size) > 1024:
                return 0, False
            return total, True
    except Exception:
        return 0, False


def _download_parallel(url, dest, timeout=60, progress=None, expect_size=0):
    """并行分块下载：多连接同时拉不同区段，把零散带宽叠起来，整体更快。

    - 自动探测大小与 Range 支持；不支持就退回 _download_file（单连接，带断点续传）。
    - 按块落盘到 dest.part.N；某块下坏只重下那一块（断点续传在「块」粒度）。
    - 全部块收齐后按顺序拼成整文件，整体算 sha256 防篡改。
    - progress(got, total, speed_bps) 实时上报进度与速度；speed_bps=0 表示「未知」。
    """
    import concurrent.futures

    total, range_ok = _probe_size(url, expect_size)
    if not (range_ok and total):
        # 兜底：单连接 + 断点续传（原来的实现）
        def _fb(g, t):
            if progress:
                progress(g, t, 0)
        return _download_file(url, dest, timeout=timeout, progress=_fb, expect_size=expect_size)

    n = _clamp(total // CHUNK_TARGET, MIN_CHUNKS, MAX_CHUNKS)
    chunk_len = total // n
    chunks = []                       # (start, end) 闭区间
    for i in range(n):
        s = i * chunk_len
        e = total - 1 if i == n - 1 else s + chunk_len - 1
        chunks.append((s, e))

    meta = dest + ".part.meta"
    part_paths = [dest + ".part.%d" % i for i in range(n)]

    # 版本/源校验：meta 记录 url+total，不一致就说明是别的版本或别的源的半截，全清重来
    mismatch = False
    if os.path.exists(meta):
        try:
            with open(meta, "r", encoding="utf-8") as fh:
                rec = fh.read().strip().split("\n")
            if len(rec) != 2 or rec[0] != url or int(rec[1]) != total:
                mismatch = True
        except Exception:
            mismatch = True
    if mismatch:
        for p in part_paths + [meta]:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

    # 记 meta（url + total），便于下次判断是否同一下载
    try:
        with open(meta, "w", encoding="utf-8") as fh:
            fh.write(url + "\n" + str(total))
    except Exception:
        pass

    # 已下完的块直接算进总量；没下完的由工作线程边下边累加
    lock = threading.Lock()
    got = 0
    for i in range(n):
        p = part_paths[i]
        s, e = chunks[i]
        want = e - s + 1
        if os.path.exists(p) and os.path.getsize(p) == want:
            got += want

    def _worker(i):
        nonlocal got
        s, e = chunks[i]
        want = e - s + 1
        p = part_paths[i]
        # 已经下好这一块：直接跳过
        if os.path.exists(p) and os.path.getsize(p) == want:
            return True
        # 半截或不完整的：整块重下（简单可靠，wb 从头写这一块）
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "PDFtoTXT-Updater/1.0",
                              "Range": "bytes=%d-%d" % (s, e)})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.getcode() not in (200, 206):
                    return False
                with open(p, "wb") as f:
                    while True:
                        buf = resp.read(1 << 16)
                        if not buf:
                            break
                        f.write(buf)
                        with lock:
                            got += len(buf)
            # 落盘后核对本块大小，防止镜像截断
            if os.path.getsize(p) != want:
                try:
                    os.remove(p)
                except Exception:
                    pass
                return False
            return True
        except Exception:
            # 失败保留半截文件，下次同一块重下（支持断点续传）
            return False

    # 跑并行下载，同时按 0.2s 节流上报进度 + 速度
    last_t = time.time()
    last_got = got
    ema = 0.0
    if progress:
        progress(got, total, 0)
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as ex:
        futs = {ex.submit(_worker, i): i for i in range(n)}
        pending = set(futs)
        while pending:
            _done, pending = concurrent.futures.wait(pending, timeout=0.2)
            now = time.time()
            g = got
            dt = now - last_t
            if dt >= 0.2 or not pending:
                inst = (g - last_got) / dt if dt > 0 else 0.0
                ema = inst if ema == 0 else (0.7 * inst + 0.3 * ema)
                if progress:
                    progress(min(g, total), total, ema if ema > 0 else 0)
                last_t = now
                last_got = g
        bad = [i for f, i in futs.items() if not f.result()]
    if bad:
        # 有块没下成：保留已下的块，抛异常让上层重试（或换源）
        raise RuntimeError("分块下载有 %d 块失败，已保留进度将自动重试" % len(bad))

    # 全部块收齐：按顺序拼成一个整文件（先写 .merged 临时文件，再原子替换），整体算 sha256
    merged = dest + ".merged"
    hasher = hashlib.sha256()
    merge_ok = False
    try:
        with open(merged, "wb") as out:
            for i in range(n):
                with open(part_paths[i], "rb") as cf:
                    while True:
                        buf = cf.read(1 << 16)
                        if not buf:
                            break
                        out.write(buf)
                        hasher.update(buf)
        merge_ok = True
    finally:
        if merge_ok:
            try:
                if os.path.exists(dest):
                    os.remove(dest)
            except Exception:
                pass
            os.replace(merged, dest)
            # 清分块 + meta
            for p in part_paths:
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass
            try:
                os.remove(meta)
            except Exception:
                pass
        else:
            # 失败：留着分块和 meta 给重试；删掉可能半截的 merged
            try:
                if os.path.exists(merged):
                    os.remove(merged)
            except Exception:
                pass
            raise RuntimeError("分块合并失败，已保留各块进度将自动重试")
    return hasher.hexdigest()


def _manifest_candidates():
    """更新清单候选地址（竞速用，参考哈夫克图片程序）：
    ① 加速站包 raw（实测最快、不吃 GitHub 限流）
    ② 直连 raw（权威、无版本滞后）
    ③ jsDelivr（带缓存，可能滞后一两版，放最后兜底）
    谁先返回合法清单就用谁。"""
    base = "https://raw.githubusercontent.com/wang1357441/pdf2txt-dist/main/manifest.json"
    out = [
        "https://ghproxy.net/" + base,                       # ① 加速站包 raw（最快）
        base,                                               # ② 直连 raw（权威）
        "https://cdn.jsdelivr.net/gh/wang1357441/pdf2txt-dist@main/manifest.json",  # ③ 缓存兜底
    ]
    seen = set(); uniq = []
    for u in out:
        if u not in seen:
            seen.add(u); uniq.append(u)
    return uniq


def _human_bytes(n):
    """把字节数变成人话：1.2 MB / 256 KB / 123 字节。"""
    n = int(n or 0)
    if n >= 1048576:
        return "%.1f MB" % (n / 1048576.0)
    if n >= 1024:
        return "%.0f KB" % (n / 1024.0)
    return "%d 字节" % n


def _race_fetch_json(urls, timeout=6.0):
    """并发去拿几个清单地址，谁先返回**合法 JSON** 就用谁（参考哈夫克图片程序）。
    全部失败才抛异常，绝不把垃圾当清单。"""
    result = {}
    done = threading.Event()
    failures = {}

    def _worker(u):
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "PDFtoTXT-Updater/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("清单不是对象")
            result["data"] = data
            done.set()
        except Exception as e:                       # noqa: BLE001 - 记下所有失败
            failures[u] = e

    for u in urls:
        threading.Thread(target=_worker, args=(u,), daemon=True).start()
    if not done.wait(timeout):
        if failures:
            raise next(iter(failures.values()))
        raise TimeoutError("更新清单竞速超时（已试 %d 条）" % len(urls))
    return result["data"]


def _fetch_manifest_race(retries=2):
    """竞速拉清单；全部源失败就整体重试几次（应对临时网络抖）。

    仍失败抛带人话的异常，直接交给更新弹窗显示，不让用户看天书。
    """
    last = None
    for attempt in range(retries + 1):
        try:
            return _race_fetch_json(_manifest_candidates())
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(1.0)
    raise RuntimeError(_friendly_net_error(last))


# ==========================================================================
# 下载源择优（参考哈夫克图片程序）：先探每个源的真实速度，挑最快的先用
# ==========================================================================
SOURCE_PROBE_TIMEOUT = 8.0
SOURCE_PROBE_SAMPLE = 256 * 1024


def _label_for(url):
    """给下载地址起个中文名，界面上写清楚「从哪个源下」。"""
    try:
        host = urlparse(url).netloc
    except Exception:
        host = url
    if "ghproxy" in host or "gh-proxy" in host:
        return "镜像站（%s）" % host
    if "jsdelivr" in host:
        return "CDN 镜像（jsDelivr）"
    return "官方源（%s）" % host


def _probe_source(url, expect_size=0, timeout=SOURCE_PROBE_TIMEOUT,
                  sample=SOURCE_PROBE_SAMPLE):
    """探一个下载源：返回 (能不能用, 实测速度 字节/秒)。

    判据是「限时读满 sample 字节」，也就是量**吞吐**，不是量首字节——
    首字节快不等于整包拉得动（参考哈夫克图片程序）。"""
    if not url or not url.startswith("https://"):
        return False, 0.0
    t0 = time.time()
    deadline = t0 + timeout
    got = 0
    elapsed = 0.0
    last = t0
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "PDFtoTXT-Updater/1.0",
                          "Range": "bytes=0-%d" % (sample - 1)})
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            total = 0
            cr = resp.headers.get("Content-Range") or ""
            if "/" in cr:
                try:
                    total = int(cr.rsplit("/", 1)[1])
                except (TypeError, ValueError):
                    total = 0
            if not total:
                try:
                    total = int(resp.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    total = 0
            # 大小对不上（多半是镜像回了个错误页）→ 直接判不合格
            if expect_size and total and total != expect_size:
                return False, 0.0
            want = min(sample, total) if total else sample
            while got < want and time.time() < deadline:
                blk = resp.read(min(64 * 1024, want - got))
                if not blk:
                    break
                got += len(blk)
                now = time.time()
                elapsed += now - last
                last = now
    except Exception:
        return False, 0.0
    ok = got >= want
    rate = got / elapsed if elapsed > 0.05 else float(got)
    return ok, rate


def _order_sources(cands, expect_size=0):
    """把候选下载地址按实测速度从快到慢排好（探不通的垫在后面兜底）。
    cands: list of (url, label)。返回 ordered_list。"""
    urls = [u for u, _ in cands]
    label_of = dict(cands)
    measured = {}
    for u in urls:
        try:
            ok, rate = _probe_source(u, expect_size=expect_size)
        except Exception:
            ok, rate = False, 0.0
        measured[u] = (ok, rate)
    usable = [u for u in urls if measured[u][0]]
    usable.sort(key=lambda u: -measured[u][1])      # 快的在前
    unusable = [u for u in urls if not measured[u][0]]
    return [(u, label_of[u]) for u in (usable + unusable)]


def _uos_candidates(dl):
    """把 Linux / 统信 UOS 的下载项（可能含 urls/url/fallback）展开成带镜像的候选列表。

    和 Windows 一样：给 GitHub 官方地址自动加两个国内镜像前缀（ghproxy.net / gh-proxy.com），
    程序再按实测速度挑最快的先用。dl 是清单里的 linux.source 或 linux.binary 那一段。"""
    raw = []
    seen = set()
    for u in (dl.get("urls") or []):
        u = (u or "").strip()
        if u and u not in seen:
            raw.append(u); seen.add(u)
    for key in ("url", "fallback"):
        u = (dl.get(key) or "").strip()
        if u and u not in seen:
            raw.append(u); seen.add(u)
    if not raw:
        return []
    out = []
    for u in raw:
        if u.startswith("https://github.com/"):
            out.append(("https://ghproxy.net/" + u, _label_for("https://ghproxy.net/" + u)))
            out.append(("https://gh-proxy.com/" + u, _label_for("https://gh-proxy.com/" + u)))
        out.append((u, _label_for(u)))
    uniq = []
    s2 = set()
    for u, lbl in out:
        if u not in s2:
            s2.add(u); uniq.append((u, lbl))
    return uniq


def round_rect_points(x1, y1, x2, y2, r, steps=8):
    r = max(0.0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    if r <= 0.5:
        return [x1, y1, x2, y1, x2, y2, x1, y2]
    corners = (
        (x2 - r, y2 - r, 0.0),
        (x1 + r, y2 - r, 90.0),
        (x1 + r, y1 + r, 180.0),
        (x2 - r, y1 + r, 270.0),
    )
    pts = []
    for cx, cy, a0 in corners:
        for i in range(steps + 1):
            a = math.radians(a0 + 90.0 * i / steps)
            pts.append(cx + r * math.cos(a))
            pts.append(cy + r * math.sin(a))
    return pts


def round_rect(canvas, x1, y1, x2, y2, r, **kw):
    return canvas.create_polygon(round_rect_points(x1, y1, x2, y2, r), **kw)


# ==========================================================================
# 自绘控件（圆角按钮 / 分段开关 / 圆角面板）—— 不引第三方 UI 库
# ==========================================================================
class RoundButton(tk.Canvas):
    KINDS = {
        "accent": (ACCENT, ACCENT_HOVER, ACCENT_PRESS, FG_ON_ACCENT),
        "solid": (BG_HOVER, BG_ACTIVE, "#343943", FG_TEXT),
        "ghost": (None, BG_HOVER, BG_ACTIVE, FG_TEXT),
    }

    def __init__(self, parent, text="", command=None, *, kind="accent",
                 height=34, radius=RADIUS_SM, pad_x=14, font_size=11,
                 width=None):
        self._bg = BG_APP
        super().__init__(parent, height=height, highlightthickness=0, bd=0,
                         bg=self._bg, takefocus=0)
        self._text = text
        self._command = command
        self._kind = kind if kind in self.KINDS else "accent"
        self._radius = radius
        self._pad_x = pad_x
        self._font = font(font_size)
        self._state = "normal"
        self._mode = "idle"
        self._fixed_w = width
        self._width = width or self._natural_width()
        self.configure(width=self._width)
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Return>", lambda _e: self._invoke())
        self.after_idle(self._redraw)

    def _natural_width(self):
        tmp = tkfont.Font(font=self._font)
        return int(tmp.measure(self._text) + self._pad_x * 2)

    def set_text(self, text):
        self._text = text
        if self._fixed_w is None:
            self._width = self._natural_width()
            self.configure(width=self._width)
        self._redraw()

    def set_state(self, state):
        self._state = state
        self.configure(cursor="" if state == "disabled" else "hand2")
        self._redraw()

    def set_command(self, command):
        self._command = command

    def _on_enter(self, _e=None):
        if self._state == "disabled":
            return
        self._mode = "hover"
        self._redraw()

    def _on_leave(self, _e=None):
        self._mode = "idle"
        self._redraw()

    def _on_press(self, _e=None):
        if self._state == "disabled":
            return
        self._mode = "press"
        self._redraw()

    def _on_release(self, _e=None):
        if self._state == "disabled":
            return
        inside = (0 <= self.winfo_pointerx() - self.winfo_rootx() <= self.winfo_width()
                  and 0 <= self.winfo_pointery() - self.winfo_rooty() <= self.winfo_height())
        self._mode = "hover" if inside else "idle"
        self._redraw()
        if inside:
            self._invoke()

    def _invoke(self):
        if self._state != "disabled" and callable(self._command):
            self._command()

    def _redraw(self):
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        if w <= 1 or h <= 1:
            self.after(16, self._redraw)
            return
        self.delete("all")
        base, hover, press, fg = self.KINDS[self._kind]
        if self._state == "disabled":
            fill = "#2a2d34" if self._kind != "ghost" else BG_HOVER
            fg = FG_MUTED
        elif self._mode == "press":
            fill = press
        elif self._mode == "hover":
            fill = hover
        else:
            fill = base
        if fill is not None:
            round_rect(self, 1, 1, w - 1, h - 1, self._radius, fill=fill, outline="")
        elif self._mode != "idle":
            round_rect(self, 1, 1, w - 1, h - 1, self._radius, fill=hover, outline="")
        if self._text:
            self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)


class Segmented(tk.Canvas):
    PAD_X = 12

    def __init__(self, parent, options, *, value=None, command=None,
                 height=32, font_size=10, radius=RADIUS_SM):
        self._bg = BG_APP
        self._segs = [(str(k), str(v)) for k, v in options]
        self._cmd = command
        self._font = font(font_size)
        self._radius = radius
        self._hover = None
        tmp = tkfont.Font(font=self._font)
        seg = max(tmp.measure(lab) for _k, lab in self._segs) + self.PAD_X * 2
        self._seg = float(max(40, seg))
        w = int(self._seg * len(self._segs)) + 4
        super().__init__(parent, width=w, height=height, highlightthickness=0,
                         bd=0, bg=self._bg, takefocus=0)
        self._value = self._segs[0][0] if value is None else str(value)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda _e: self._set_hover(None))
        self.after_idle(self._redraw)

    def get(self):
        return self._value

    def set(self, key, notify=False):
        key = str(key)
        if key not in [k for k, _ in self._segs] or key == self._value:
            return
        self._value = key
        self._redraw()
        if notify and callable(self._cmd):
            self._cmd(key)

    def _index_at(self, x):
        i = int((x - 2) // self._seg)
        return max(0, min(len(self._segs) - 1, i))

    def _on_click(self, event):
        self.set(self._segs[self._index_at(event.x)][0], notify=True)

    def _on_motion(self, event):
        self._set_hover(self._index_at(event.x))

    def _set_hover(self, idx):
        if idx == self._hover:
            return
        self._hover = idx
        self._redraw()

    def _redraw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            self.after(16, self._redraw)
            return
        self.delete("all")
        round_rect(self, 1, 1, w - 1, h - 1, self._radius,
                   fill=BG_SUNKEN, outline=STROKE)
        for i, (key, label) in enumerate(self._segs):
            x0 = 2 + i * self._seg
            x1 = x0 + self._seg
            sel = key == self._value
            if sel:
                round_rect(self, x0 + 1, 3, x1 - 1, h - 3,
                           max(2, self._radius - 2), fill=ACCENT, outline="")
                fg = FG_ON_ACCENT
            elif i == self._hover:
                round_rect(self, x0 + 1, 3, x1 - 1, h - 3,
                           max(2, self._radius - 2), fill=BG_HOVER, outline="")
                fg = FG_TEXT
            else:
                fg = FG_DIM
            self.create_text((x0 + x1) / 2, h / 2, text=label, fill=fg, font=self._font)


class RoundedPanel(tk.Canvas):
    """圆角面板：画一个圆角矩形当底，内容 Frame 内缩一个圆角半径，
    于是四个角露出底色、看起来就是圆角的。"""

    def __init__(self, parent, bg=BG_ELEV, radius=RADIUS_MD, border=True):
        super().__init__(parent, bg=parent["bg"], highlightthickness=0, bd=0)
        self._bg = bg
        self._radius = radius
        self._border = border
        self.bind("<Configure>", lambda _e: self._redraw())
        self.after_idle(self._redraw)

    def _redraw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            self.after(16, self._redraw)
            return
        self.delete("all")
        round_rect(self, 1, 1, w - 1, h - 1, self._radius,
                   fill=self._bg, outline=STROKE if self._border else "")

    def content(self):
        f = tk.Frame(self, bg=self._bg)
        r = self._radius
        f.place(x=r, y=r, relwidth=1, width=-2 * r, relheight=1, height=-2 * r)
        return f


class RoundedProgress(tk.Canvas):
    """圆角进度条：轨道 + 填充都用圆角多边形，和整体深色风格一致。
    支持确定进度 set_fraction(0~1)，以及在不知道总量时的跑马灯态 start_indeterminate()。"""

    def __init__(self, parent, height=12, radius=6, track=STROKE, fill=ACCENT, **kw):
        super().__init__(parent, height=height, bg=parent["bg"], highlightthickness=0, bd=0, **kw)
        self._radius = radius
        self._track = track
        self._fill = fill
        self._frac = 0.0
        self._indeterminate = False
        self._ind = 0.0
        self.bind("<Configure>", lambda _e: self._redraw())
        self.after_idle(self._redraw)

    def set_fraction(self, frac):
        self._indeterminate = False
        self._frac = max(0.0, min(1.0, float(frac)))
        self._redraw()

    def start_indeterminate(self):
        if self._indeterminate:
            return
        self._indeterminate = True
        self._ind = 0.0
        self._anim()

    def stop(self):
        self._indeterminate = False
        self._redraw()

    def _anim(self):
        if not self._indeterminate:
            return
        self._ind = (self._ind + 0.03) % 1.0
        self._redraw()
        self.after(40, self._anim)

    def _redraw(self):
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except tk.TclError:
            return
        if w <= 2 or h <= 2:
            self.after(16, self._redraw)
            return
        self.delete("all")
        round_rect(self, 1, 1, w - 1, h - 1, self._radius, fill=self._track, outline="")
        if self._indeterminate:
            seg = max(24.0, w * 0.28)
            x0 = self._ind * (w + seg) - seg
            x1 = x0 + seg
            x0 = max(1.0, x0)
            x1 = min(w - 1, x1)
            if x1 - x0 > 2:
                round_rect(self, x0, 1, x1, h - 1, self._radius, fill=self._fill, outline="")
        elif self._frac > 0.001:
            fw = (w - 2) * self._frac
            round_rect(self, 1, 1, 1 + fw, h - 1, self._radius, fill=self._fill, outline="")


class UpdateDialog(tk.Toplevel):
    """深色风格的检查更新弹窗（模态）。网络操作在外部线程跑，这里只负责显示。"""

    def __init__(self, parent, current_version):
        super().__init__(parent)
        self.title("检查更新")
        self.configure(bg=BG_APP)
        self.resizable(False, False)
        self._current = current_version
        self._info = None
        self._dest = None
        self._update_mode = "win"   # 安装方式：win / linux_bin / linux_src（手动点「安装并更新」时用）
        self._dl_source = ""   # 当前正在用的下载源（镜像站 / 官方源），只在下载时显示
        self._build()
        self.grab_set()
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry("%dx%d+%d+%d" % (
            w, h,
            max(0, parent.winfo_rootx() + (parent.winfo_width() - w) // 2),
            max(0, parent.winfo_rooty() + (parent.winfo_height() - h) // 3)))

    def _build(self):
        pad = PAD
        tk.Label(self, text="检查更新", bg=BG_APP, fg=FG_TEXT,
                 font=font(15, "bold")).pack(anchor="w", padx=pad, pady=(pad, 4))
        tk.Label(self, text="当前版本：%s" % self._current, bg=BG_APP, fg=FG_DIM,
                 font=font(10)).pack(anchor="w", padx=pad)

        panel = RoundedPanel(self, bg=BG_ELEV, radius=RADIUS_MD)
        panel.pack(fill="x", padx=pad, pady=(8, 8))
        pf = panel.content()
        self.lbl_status = tk.Label(pf, text="正在检查更新…", bg=BG_ELEV, fg=FG_TEXT,
                                   font=font(11), anchor="w", wraplength=360, justify="left")
        self.lbl_status.pack(fill="x", padx=12, pady=12)

        self.pbar = RoundedProgress(pf, height=12)
        self.pbar.pack_forget()

        self.frm_notes = tk.Frame(pf, bg=BG_ELEV)
        self.frm_notes.pack(fill="x", padx=12, pady=(0, 12))
        self.frm_notes.pack_forget()

        self.lbl_progress = tk.Label(pf, text="", bg=BG_ELEV, fg=FG_DIM, font=font(9))
        self.lbl_progress.pack(fill="x", padx=12, pady=(0, 12))
        self.lbl_progress.pack_forget()

        btn_row = tk.Frame(self, bg=BG_APP)
        btn_row.pack(fill="x", padx=pad, pady=(0, pad))
        self.btn_secondary = RoundButton(btn_row, "关闭", command=self.destroy,
                                         kind="ghost", height=32)
        self.btn_secondary.pack(side="right")
        self.btn_primary = RoundButton(btn_row, "确定", command=self.destroy,
                                       kind="accent", height=32)
        self.btn_primary.pack(side="right", padx=(8, 0))

    def show_checking(self):
        self._set_buttons(primary=("确定", self.destroy), secondary=None)
        self.lbl_status.configure(text="正在检查更新…", fg=FG_TEXT)
        self._hide(self.frm_notes)
        self._hide(self.lbl_progress)
        self.pbar.set_fraction(0.0)
        self.pbar.start_indeterminate()
        self.pbar.pack(fill="x", padx=12, pady=(4, 2))

    def show_uptodate(self):
        self._set_buttons(primary=("好的", self.destroy), secondary=None)
        self.lbl_status.configure(text="已经是最新版本了，无需更新。", fg=FG_OK)
        self._hide(self.frm_notes)
        self._hide(self.lbl_progress)
        self.pbar.stop()
        self.pbar.pack_forget()

    def show_update(self, info):
        self._info = info
        notes = (info.get("notes") or "（没有写更新说明）").strip()
        self._hide(self.lbl_progress)
        self.pbar.stop()
        self.pbar.pack_forget()
        self.lbl_status.configure(text="发现新版本：%s" % info.get("version", "?"), fg=FG_WARN)
        for w in list(self.frm_notes.children.values()):
            w.destroy()
        tk.Label(self.frm_notes, text="更新内容：", bg=BG_ELEV, fg=FG_DIM,
                 font=font(10)).pack(anchor="w")
        tk.Label(self.frm_notes, text=notes, bg=BG_ELEV, fg=FG_TEXT,
                 font=font(10), anchor="w", wraplength=360, justify="left").pack(anchor="w")
        self.frm_notes.pack(fill="x")
        self._set_buttons(primary=("下载新版", self._on_download),
                          secondary=("稍后", self.destroy))

    def set_downloading_source(self, label):
        """外部线程在开始每个候选地址前调用，记录“当前从哪个源下”，仅用于显示。"""
        self._dl_source = label or ""

    def show_sourcing(self):
        """探测阶段：写清「正在挑选最快的下载源」，进度条跑马灯。"""
        self._set_buttons(primary=None, secondary=("关闭", self.destroy))
        self.lbl_status.configure(text="正在挑选最快的下载源…", fg=FG_TEXT)
        self._hide(self.frm_notes)
        self._hide(self.lbl_progress)
        self.pbar.stop()
        self.pbar.set_fraction(0.0)
        self.pbar.start_indeterminate()
        self.pbar.pack(fill="x", padx=12, pady=(4, 2))

    def start_source(self, label, idx, total):
        """开始从某个源下载时调用：界面写明“正在从哪个源下（第几个源）”，避免跳来跳去。"""
        self._dl_source = label or ""
        self._set_buttons(primary=None, secondary=("关闭", self.destroy))
        self._hide(self.frm_notes)
        self.lbl_status.configure(
            text="正在从 %s 下载新版本（第 %d/%d 个源）…" % (label, idx, total),
            fg=FG_TEXT)
        self._hide(self.lbl_progress)
        # 进度条：先跑无确定总量的跑马灯，拿到总量后再变确定进度
        self.pbar.stop()
        self.pbar.set_fraction(0.0)
        self.pbar.start_indeterminate()
        self.pbar.pack(fill="x", padx=12, pady=(4, 2))

    def note_retry(self):
        """某个源连接失败时调用：提示正在重试，而不是把界面清空。"""
        self.lbl_status.configure(
            text="连接 %s 失败，正在重试…" % (self._dl_source or "源"), fg=FG_WARN)

    def set_downloading(self, got, total, source=None, speed_bps=None):
        if source:
            self._dl_source = source
        self._set_buttons(primary=None, secondary=("关闭", self.destroy))
        src = ("（来源：%s）" % self._dl_source) if self._dl_source else ""
        self.lbl_status.configure(text="正在下载新版本…" + src, fg=FG_TEXT)
        if total:
            # 有总量：进度条变成确定进度
            self.pbar.stop()
            self.pbar.set_fraction(got / total)
            self.pbar.pack(fill="x", padx=12, pady=(4, 2))
            pct = got * 100 // total
            speed_txt = ""
            if speed_bps and speed_bps > 0:
                speed_txt = "  %s/s" % _human_bytes(speed_bps)
            self.lbl_progress.configure(
                text="%d%%  （%s / %s）%s" % (pct, _human_bytes(got), _human_bytes(total), speed_txt))
        else:
            # 不知道总量：继续跑马灯
            self.pbar.stop()
            self.pbar.start_indeterminate()
            self.pbar.pack(fill="x", padx=12, pady=(4, 2))
            self.lbl_progress.configure(text="下载中…")
        self._show(self.lbl_progress)

    def show_downloaded(self, dest):
        """下载并校验通过：给出“安装并更新”按钮，由程序自己关掉、替换、重启。"""
        self._dest = dest
        self._hide(self.lbl_progress)
        self.pbar.stop()
        self.pbar.pack_forget()
        self.lbl_status.configure(
            text="新版本已下载并校验通过！\n"
                 "点「安装并更新」会自动关闭本程序、替换成新版本并重新打开。\n"
                 "（不会卡死，因为替换动作由隐藏助手在你退出后再做。）",
            fg=FG_OK)
        self._set_buttons(primary=("安装并更新", self._on_install),
                          secondary=("稍后", self.destroy))

    def _on_install(self):
        owner = self.master
        if callable(getattr(owner, "_install_update", None)):
            owner._install_update(self._dest, self._update_mode)

    def show_extracting(self):
        """源码更新：下载完在解压替换阶段，先提示一下，避免以为卡住了。"""
        self._set_buttons(primary=None, secondary=("关闭", self.destroy))
        self.lbl_status.configure(text="下载完成，正在解压并替换文件…", fg=FG_TEXT)
        self._hide(self.frm_notes)
        self._hide(self.lbl_progress)
        self.pbar.stop()
        self.pbar.set_fraction(0.0)
        self.pbar.start_indeterminate()
        self.pbar.pack(fill="x", padx=12, pady=(4, 2))

    def show_opened_page(self):
        self._hide(self.lbl_progress)
        self.pbar.stop()
        self.pbar.pack_forget()
        self.lbl_status.configure(text="已为你打开下载页面，去拿最新版即可。", fg=FG_OK)
        self._set_buttons(primary=("好的", self.destroy), secondary=None)

    def show_error(self, msg):
        self._hide(self.lbl_progress)
        self.pbar.stop()
        self.pbar.pack_forget()
        self.lbl_status.configure(text="检查更新失败：%s" % msg, fg=FG_BAD)
        self._set_buttons(primary=("重试", self._on_retry),
                          secondary=("关闭", self.destroy))

    def _on_download(self):
        if self._info is not None and callable(getattr(self.master, "_begin_update", None)):
            self.master._begin_update(self._info, self)

    def _on_retry(self):
        owner = self.master
        self.destroy()
        if callable(getattr(owner, "_on_check_update", None)):
            owner._on_check_update()

    @staticmethod
    def _hide(w):
        try:
            w.pack_forget()
        except Exception:
            pass

    @staticmethod
    def _show(w):
        try:
            w.pack(fill="x")
        except Exception:
            pass

    def _set_buttons(self, primary, secondary):
        if primary:
            self.btn_primary.set_text(primary[0])
            self.btn_primary.set_command(primary[1])
            self.btn_primary.set_state("normal")
            self.btn_primary.pack(side="right", padx=(8, 0))
        else:
            self.btn_primary.pack_forget()
        if secondary:
            self.btn_secondary.set_text(secondary[0])
            self.btn_secondary.set_command(secondary[1])
            self.btn_secondary.set_state("normal")
            self.btn_secondary.pack(side="right")
        else:
            self.btn_secondary.pack_forget()


class FeedbackDialog(tk.Toplevel):
    """深色风格的反馈弹窗：用户写一句话，点发送就打开邮件软件（已写好邮件）。"""

    def __init__(self, parent, version):
        super().__init__(parent)
        self.title("反馈 / 联系作者")
        self.configure(bg=BG_APP)
        self.resizable(False, False)
        self._version = version
        self._build()
        self.grab_set()
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry("%dx%d+%d+%d" % (
            w, h,
            max(0, parent.winfo_rootx() + (parent.winfo_width() - w) // 2),
            max(0, parent.winfo_rooty() + (parent.winfo_height() - h) // 3)))

    def _build(self):
        pad = PAD
        tk.Label(self, text="反馈 / 联系作者", bg=BG_APP, fg=FG_TEXT,
                 font=font(15, "bold")).pack(anchor="w", padx=pad, pady=(pad, 4))
        tk.Label(self, text="把你遇到的问题、建议写下来，点「发送」会打开你的邮件软件，"
                            "邮件已经帮你写好，你点一下发送就行。",
                 bg=BG_APP, fg=FG_DIM, font=font(10), anchor="w",
                 wraplength=420, justify="left").pack(anchor="w", padx=pad, pady=(0, 8))

        panel = RoundedPanel(self, bg=BG_ELEV, radius=RADIUS_MD)
        panel.pack(fill="x", padx=pad, pady=(0, 8))
        pf = panel.content()

        self.txt = tk.Text(pf, bg=BG_SUNKEN, fg=FG_TEXT, font=font(11),
                           relief="flat", highlightthickness=0, padx=10, pady=8,
                           height=8, width=52, wrap="word", insertbackground=FG_TEXT)
        self.txt.pack(fill="x", padx=12, pady=12)
        self.txt.focus_set()

        self.var_err = tk.BooleanVar(value=True)
        tk.Checkbutton(pf, text="附上近期错误记录（有助于排查，不含 PDF 内容）",
                       variable=self.var_err, bg=BG_ELEV, fg=FG_DIM,
                       font=font(9), anchor="w", selectcolor=BG_SUNKEN,
                       activebackground=BG_ELEV, activeforeground=FG_DIM,
                       relief="flat", borderwidth=0).pack(anchor="w", padx=12, pady=(0, 12))

        btn_row = tk.Frame(self, bg=BG_APP)
        btn_row.pack(fill="x", padx=pad, pady=(0, pad))
        RoundButton(btn_row, "取消", command=self.destroy,
                    kind="ghost", height=32).pack(side="right")
        RoundButton(btn_row, "发送", command=self._on_send,
                    kind="accent", height=32).pack(side="right", padx=(8, 0))

    def _on_send(self):
        text = self.txt.get("1.0", "end").strip()
        ok = feedback.send_via_mailto(text, version=self._version,
                                      include_errors=self.var_err.get())
        if ok:
            messagebox.showinfo("已打开邮件软件",
                                "邮件已经帮你写好了，在你的邮件软件里点「发送」即可。\n"
                                "如果没弹出邮件软件，请检查一下系统默认的邮件程序。")
            self.destroy()
        else:
            messagebox.showwarning("没能自动打开",
                                   "没能自动打开邮件软件。请手动发邮件到：\n%s\n"
                                   "主题随便写，把你想说的话发来就行。" % feedback.FEEDBACK_RECIPIENT)


# ==========================================================================
# 转换核心（GUI 与无界面模式共用；不碰任何 Tk 对象）
# ==========================================================================
def _page_has_images(page) -> bool:
    try:
        for obj in page.get_objects():
            if getattr(obj, "type", "") == "image":
                return True
    except Exception:
        pass
    return False


# 整页 OCR 的触发门槛：原生文字少于这么多字符，才认为“这一页基本是图片/扫描件”，
# 才值得整页 OCR。文字层已经很全的页（里面的图片多半是图表），OCR 只会添乱，跳过。
OCR_SPARSE_THRESHOLD = 30


def _keep_ocr_line(line: str) -> bool:
    """OCR 出来的行要不要保留：过滤掉图表里的纯数字/刻度、装饰字符等噪声。"""
    s = line.strip()
    if len(s) < 2:
        return False
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", s))
    has_alpha = bool(re.search(r"[A-Za-z]", s))
    if not has_cjk and not has_alpha:        # 纯数字/符号，像 “36850”“224” → 丢弃
        return False
    if not has_cjk and len(s) < 4:           # 短拉丁噪声，像 “Qi”“Qir” → 丢弃
        return False
    return True


def _load_ocr(log):
    global RapidOCR, _OCR_ENGINE, _OCR_FAILED
    if RapidOCR is None:
        try:
            from rapidocr_onnxruntime import RapidOCR as _R
            RapidOCR = _R
        except Exception as e:
            log("bad", "OCR 组件加载失败：" + str(e))
            _OCR_FAILED = True
            return None
    if _OCR_ENGINE is None and not _OCR_FAILED:
        try:
            log("info", "正在加载图片识别引擎（首次稍慢）…")
            _OCR_ENGINE = RapidOCR()
        except Exception as e:
            log("bad", "OCR 引擎初始化失败：" + str(e))
            _OCR_FAILED = True
            return None
    return _OCR_ENGINE


def _ocr_page(page, log) -> str:
    engine = _load_ocr(log)
    if engine is None:
        return ""
    try:
        bitmap = page.render(scale=2.0)
        pil = bitmap.to_pil()
        arr = np.array(pil)
        if arr.ndim == 3:
            import cv2
            arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
        result, _ = engine(arr)
        if not result:
            return ""
        texts = [ln[1] for ln in result if ln and len(ln) > 1 and ln[1]]
        return "\n".join(t.strip() for t in texts if t and t.strip())
    except Exception as e:
        log("warn", "某一页 OCR 失败，跳过：" + str(e))
        return ""


# 常见“表单字段标签”后缀。导出时，如果某行以这些词结尾却没有冒号，就补一个「：」。
# （用户要的：姓名/人名/寄语 这类标签后面统一加上冒号）
_LABEL_SUFFIXES = (
    "姓名", "人名", "名字", "性别", "年龄", "民族", "籍贯", "身份证", "学号", "班级",
    "学校", "年级", "地址", "住址", "电话", "手机", "邮箱", "寄语", "赠言", "留言",
    "签字", "签名", "日期", "时间", "编号", "序号", "备注", "说明", "标题", "主题",
    "内容", "单位", "职务", "职业", "部门", "公司", "成绩", "科目", "结论", "意见",
    "评价", "金额", "单价", "数量", "合计", "总价", "邮编", "省份", "城市", "所在地",
    "监护人", "毕业小学", "毕业学校", "联系电话", "电子邮箱", "出生日期", "校验码",
    "验证码", "密码", "二维码", "条形码",
)


def ensure_label_colons(text: str) -> str:
    """给像“姓名 / 寄语”这类字段标签的行尾补上「：」（已有时不动）。"""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.endswith("：") or s.endswith(":"):
            out.append(line)
            continue
        # 去掉行尾可能的标点/引号再判断，避免误伤正文
        core = s.rstrip("。，、；;,.）)】]〞\"' ")
        if len(core) > 16:          # 太长的句子不当作标签
            out.append(line)
            continue
        hit = any(core == kw or core.endswith(kw) for kw in _LABEL_SUFFIXES)
        out.append(line.rstrip() + "：" if hit else line)
    return "\n".join(out)


def convert_file(pdf: str, out: str, mode: str, log) -> int:
    """转换单个 PDF。log(kind, msg) 用于回传进度。返回页数；出错抛异常。"""
    doc = pdfium.PdfDocument(pdf)
    total = len(doc)
    if mode == "image":
        log("info", "「含图片」模式：原生文字 + 图片 OCR 双管齐下。")
    else:
        log("info", "「纯文字」模式：只取原生文字层。")

    blocks = []
    for i in range(total):
        page = doc[i]
        tp = page.get_textpage()
        native = (tp.get_text_range() or "").strip()
        page_lines = []
        if mode == "image":
            # 只有“原生文字很少”的页（扫描件 / 封面图 / 分隔页）才整页 OCR；
            # 文字层已经很全的页，里面的图片多半是图表，OCR 只会添乱，跳过。
            if len(native) < OCR_SPARSE_THRESHOLD:
                ocr_text = _ocr_page(page, log)
                if ocr_text:
                    for line in ocr_text.splitlines():
                        ls = line.strip()
                        if ls and ls not in native and _keep_ocr_line(ls):
                            page_lines.append(ls)
            log("ok" if page_lines else "info",
                "第 %d 页：原生 %d 字%s"
                % (i + 1, len(native),
                   ("，图片补充 %d 行" % len(page_lines)) if page_lines else "（原生已齐全，不 OCR）"))
        else:
            log("info", "第 %d 页：原生 %d 字" % (i + 1, len(native)))

        combined = native
        if page_lines:
            combined = (native + "\n" if native else "") + "\n".join(page_lines)
        if combined.strip():
            combined = ensure_label_colons(combined)
        blocks.append(combined if combined.strip() else "（本页无可识别文字）")
        page.close()
    doc.close()

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8-sig") as f:
        for idx, b in enumerate(blocks, 1):
            f.write("\n===== 第 %d 页 / 共 %d 页 =====\n" % (idx, total))
            f.write(b + "\n")
    return total


# ==========================================================================
# 主程序（GUI）
# ==========================================================================
# ==========================================================================
# 商用激活逻辑（联网校验：每个激活码仅限激活一台设备，用过即作废；可服务端吊销）
# ==========================================================================
def normalize_license_code(c):
    """只保留字母数字并转大写，去掉空格 / 连字符等。"""
    return re.sub(r"[^A-Za-z0-9]", "", (c or "").upper())


def validate_license_format(c):
    """校验格式：全是 LICENSE_ALPHABET 里的字符，且长度对。"""
    c = normalize_license_code(c)
    if len(c) != LICENSE_CODE_LEN:
        return False
    for ch in c:
        if ch not in LICENSE_ALPHABET:
            return False
    return True


def _friendly_net_error(e):
    """把 urllib / socket 异常翻成人话，别给用户看 WinError 10060 这种天书。"""
    if e is None:
        return "未知网络错误"
    s = str(e)
    low = s.lower()
    if "10060" in s or "timed out" in low or "timeout" in low:
        return ("连接超时：连不上服务器。\n"
                "可能是你的网络暂时不通，或服务器在海外被墙。\n"
                "请检查网络后重试，或稍后再试。")
    if "10061" in s or "connection refused" in low:
        return "服务器拒绝连接（服务可能暂时下线），请稍后重试。"
    if "getaddrinfo" in low or "name or service not known" in low \
            or "nodename nor servname" in low:
        return ("连不上这个网址（域名解析失败）。\n"
                "可能是网络/DNS 问题，或该域名对你当前网络不可达。")
    if "10013" in s or "permission" in low:
        return "网络被系统/防火墙拦了，请检查防火墙或换个网络。"
    if "403" in s or "401" in s:
        return "服务器拒绝了请求（可能密钥/权限不对）。"
    if "404" in s:
        return "找不到对应资源（404），可能地址已变，请检查更新源。"
    if "ssl" in low or "certificate" in low:
        return "SSL/证书错误，可能无法安全连接该服务器。"
    if "urlopen error" in low:
        return "网络请求失败：" + s.replace("urlopen error: ", "")
    return "网络出错了：" + s


# --------------------------------------------------------------------------
# 激活后端：GitHub 上公开、但只存「激活码哈希」的 licenses.json
# （raw + jsDelivr 双源，和“检查更新”同一条能连的通道；workers.dev 在国内连不上）
# 只存哈希 → 任何人能看到这份文件也推不出真码（SHA-256 反推不了）。
# --------------------------------------------------------------------------
LICENSE_REPO = "wang1357441/pdf2txt-dist"
LICENSE_PATH = "licenses.json"
LICENSE_SOURCES = [
    "https://raw.githubusercontent.com/%s/main/%s" % (LICENSE_REPO, LICENSE_PATH),
    "https://cdn.jsdelivr.net/gh/%s@main/%s" % (LICENSE_REPO, LICENSE_PATH),
]


def _license_hash(code):
    """激活码归一化后取 SHA-256，用于和 licenses.json 里的哈希比对（不泄露真码）。"""
    return hashlib.sha256(normalize_license_code(code).encode("utf-8")).hexdigest()


def _license_fetch(retries=2):
    """竞速拉 licenses.json（多源 + 失败重试），最终抛带人话的异常。"""
    last = None
    for attempt in range(retries + 1):
        try:
            return _race_fetch_json(LICENSE_SOURCES)
        except Exception as e:
            last = e
            if attempt < retries:
                time.sleep(1.0)
    raise RuntimeError(_friendly_net_error(last))


def _check_code_status(code):
    """返回 'ok' / 'revoked' / 'invalid'。联网失败会抛异常，交给上层决定离线信任。"""
    data = _license_fetch()
    hh = _license_hash(code)
    revoked = set(data.get("revoked", []))
    valid = set(data.get("valid", []))
    if hh in revoked:
        return "revoked"
    if hh in valid:
        return "ok"
    return "invalid"


def activate_license_online(code):
    # 公开只读后端没有“服务端写入”，激活 = 校验合法 + 本地存档（调用方负责存档）
    return {"status": _check_code_status(code)}


def verify_license_online(code):
    return {"status": _check_code_status(code)}


def _license_dirs():
    """激活码存档目录（可能多个，互相备份）。
    关键：这些目录都【在 exe 之外、跟着 Windows 用户账号走】，
    所以“更新软件 / 覆盖 exe / 重装程序”都不会丢激活。
    Windows 同时写本地(LOCALAPPDATA)和漫游(APPDATA)两份，任一被清也能从另一份恢复。
    只有两个环境变量都缺失（极少见）才退到用户主目录。
    """
    dirs = []
    if sys.platform.startswith("win"):
        have_env = False
        for env in ("LOCALAPPDATA", "APPDATA"):
            base = os.environ.get(env)
            if base:
                dirs.append(os.path.join(base, "pdf2txt"))
                have_env = True
        if not have_env:
            dirs.append(os.path.join(os.path.expanduser("~"), "pdf2txt"))
    else:
        dirs.append(os.path.expanduser("~/.pdf2txt"))
    # 去重、保顺序
    seen, out = set(), []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    for d in out:
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
    return out


def _license_paths():
    return [os.path.join(d, "license.json") for d in _license_dirs()]


def load_cached_license():
    """从所有备份位置读取激活码，任一有效即返回（更新/重装后依然能认出来）。"""
    for p in _license_paths():
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data and validate_license_format(data.get("code", "")):
                return data
        except Exception:
            continue
    return None


def save_cached_license(code):
    """把激活码写到所有备份位置；只要有一份写成功就算成功。"""
    ok = False
    norm = normalize_license_code(code)
    for p in _license_paths():
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"code": norm}, f)
            ok = True
        except Exception:
            pass
    return ok


def ensure_licensed(app):
    """检查 / 要求激活。返回 True 表示已授权，False 表示用户拒绝激活（应退出）。"""
    cached = load_cached_license()
    if cached and validate_license_format(cached.get("code", "")):
        try:
            res = verify_license_online(cached["code"])
            st = res.get("status")
            if st == "ok":
                return True
            if st == "revoked":
                messagebox.showwarning(
                    "激活码已被吊销",
                    "你这台设备的激活码已被作者吊销，\n请重新输入一个新的激活码。")
            # invalid / notactivated → 落到下面重新激活
        except Exception:
            # 联网失败：离线状态下信任本地缓存，允许继续使用（联网后下次会自动复核）
            messagebox.showwarning(
                "未能联网复核",
                "暂时连不上激活服务器，已使用本地缓存离线运行。\n"
                "网络恢复后，下次启动会自动联网复核激活状态。")
            return True
    # 需要激活（首次 / 被吊销 / 本地无记录）
    while True:
        dlg = ActivationDialog(app)
        app.wait_window(dlg)
        if dlg.result == "quit":
            return False
        if dlg.result == "ok":
            save_cached_license(dlg.code)
            return True


class ActivationDialog(tk.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.title("激活 PDF 转 TXT")
        self.configure(bg=BG_APP)
        self.resizable(False, False)
        self.result = None      # "ok" | "quit"
        self.code = ""
        self._build()
        self.grab_set()
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry("%dx%d+%d+%d" % (
            w, h,
            max(0, parent.winfo_rootx() + (parent.winfo_width() - w) // 2),
            max(0, parent.winfo_rooty() + (parent.winfo_height() - h) // 2)))
        self.entry.focus_set()

    def _build(self):
        pad = PAD
        tk.Label(self, text="激活 PDF 转 TXT", bg=BG_APP, fg=FG_TEXT,
                 font=font(15, "bold")).pack(anchor="w", padx=pad, pady=(pad, 4))
        tk.Label(self, text="请输入激活码以解锁转换功能。\n"
                            "每个激活码仅限一台设备使用（用过即作废，不可转移）。\n"
                            "（没激活也能先打开程序、检查更新；点「稍后再说」即可。）",
                 bg=BG_APP, fg=FG_DIM, font=font(10), anchor="w",
                 wraplength=400, justify="left").pack(anchor="w", padx=pad, pady=(0, 10))

        panel = RoundedPanel(self, bg=BG_ELEV, radius=RADIUS_MD)
        panel.pack(fill="x", padx=pad, pady=(0, 8))
        pf = panel.content()
        tk.Label(pf, text="激活码", bg=BG_ELEV, fg=FG_DIM, font=font(10)).pack(
            anchor="w", padx=12, pady=(12, 4))
        self.entry = tk.Entry(pf, bg=BG_SUNKEN, fg=FG_TEXT, font=mono(13),
                              relief="flat", highlightthickness=1,
                              highlightcolor=ACCENT, insertbackground=FG_TEXT,
                              width=28)
        self.entry.pack(fill="x", padx=12, pady=(0, 12))
        self.entry.bind("<Return>", lambda e: self._on_activate())

        self.status = tk.Label(self, text="", bg=BG_APP, fg=FG_DIM, font=font(9),
                               anchor="w", wraplength=400, justify="left")
        self.status.pack(anchor="w", padx=pad, pady=(0, 6))

        btn_row = tk.Frame(self, bg=BG_APP)
        btn_row.pack(fill="x", padx=pad, pady=(0, pad))
        RoundButton(btn_row, "稍后再说", command=self._on_later,
                    kind="ghost", height=34).pack(side="right")
        RoundButton(btn_row, "激活", command=self._on_activate,
                    kind="accent", height=34).pack(side="right", padx=(8, 0))

    def _set_status(self, msg, kind="info"):
        color = {"info": FG_DIM, "ok": FG_OK, "bad": FG_BAD,
                 "warn": FG_WARN}.get(kind, FG_DIM)
        self.status.configure(text=msg, fg=color)

    def _on_activate(self):
        raw = self.entry.get().strip()
        code = normalize_license_code(raw)
        if not validate_license_format(code):
            self._set_status("激活码格式不对（形如 ABCD-1234-EFGH-5678，共 16 位）。", "bad")
            return
        self._set_status("正在联网校验…", "info")
        self.update_idletasks()
        last_err = None
        for attempt in range(3):
            try:
                res = activate_license_online(code)
                break
            except Exception as e:
                last_err = e
                if attempt < 2:
                    self._set_status("连接失败，正在重试（%d/3）…" % (attempt + 2), "info")
                    self.update_idletasks()
                    time.sleep(1.2)
        else:
            # activate_license_online 失败时已把错误翻成人话
            self._set_status(str(last_err), "bad")
            return
        st = res.get("status")
        if st == "ok":
            self.code = code
            self.result = "ok"
            self.destroy()
        elif st == "invalid":
            self._set_status("该激活码无效（未发售或已失效）。", "bad")
        elif st == "used":
            self._set_status("该激活码已被使用（每个码限一台设备，用过即作废）。", "bad")
        elif st == "revoked":
            self._set_status("该激活码已被吊销。", "bad")
        else:
            self._set_status("未知返回：" + str(st), "bad")

    def _on_later(self):
        self.result = "later"
        self.destroy()

    def _on_close(self):
        self.result = "later"
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF 转 TXT")
        self.configure(bg=BG_APP)
        self.minsize(760, 600)

        self.sel_mode = tk.StringVar(value="single")
        self.ocr_mode = tk.StringVar(value="image")
        self.save_dir = tk.StringVar(value="")
        self.pdf_list = []
        self.input_kind = "files"   # "files" = 选多个文件；"folder" = 选整个文件夹
        self.src_folder = ""        # 文件夹模式下的源文件夹
        self.out_root = ""          # 文件夹模式下的输出根目录（<源文件夹名> TXT 版）
        self._running = False
        self._last_outs = []
        self._queue = queue.Queue()

        # 图标：Windows 用 .ico；Linux / 统信 UOS / macOS 用 PNG（iconphoto）
        try:
            if sys.platform.startswith("win"):
                self.iconbitmap(resource_path("app.ico"))
            else:
                png = resource_path("app.png")
                if os.path.exists(png):
                    # 必须保留引用，否则 PNG 图标会被垃圾回收导致窗口图标消失
                    self._icon_img = tk.PhotoImage(file=png)
                    self.iconphoto(True, self._icon_img)
        except Exception:
            pass

        self._build_ui()
        self._poll_queue()
        self.update_idletasks()
        if AUTO_CHECK_ON_START:
            self.after(1500, self._autocheck)
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2), max(0, (sh - h) // 3)))

        # ---- 商用激活：不再卡启动 ----
        # 没激活也能打开程序、用「检查更新」/「反馈」。
        # 有缓存就先信任（离线也能用），后台联网复核；
        # 没缓存则未激活，点「开始转换」时才会要求激活（可「稍后再说」继续用壳子）。
        _cached = load_cached_license()
        self.licensed = bool(
            _cached and validate_license_format(_cached.get("code", ""))
        )
        if self.licensed:
            threading.Thread(target=self._bg_verify_license,
                             args=(_cached["code"],), daemon=True).start()
        self._refresh_license_ui()

    def _bg_verify_license(self, code):
        """后台联网复核缓存的激活码：ok 保持授权；被吊销/失效则收回授权。"""
        try:
            res = verify_license_online(code)
            st = res.get("status")
        except Exception:
            return  # 离线：继续信任本地缓存，不弹窗、不卡
        if st == "ok":
            self.after(0, lambda: self._set_licensed(True, None))
        elif st in ("revoked", "invalid", "notactivated"):
            self.after(0, lambda: self._set_licensed(False, st))

    def _set_licensed(self, ok, st):
        if self.licensed == ok and ok:
            return
        self.licensed = ok
        self._refresh_license_ui()
        if st == "revoked":
            messagebox.showwarning(
                "激活码已被吊销",
                "你这台设备的激活码已被作者吊销，转换功能已锁定。\n"
                "点「开始转换」可重新输入激活码；检查更新/反馈不受影响。")

    def _refresh_license_ui(self):
        """未激活时把按钮文案改成提示；已激活则正常。"""
        try:
            if getattr(self, "btn_run", None) is None:
                return
            if self.licensed:
                self.btn_run.set_text("开始转换")
            else:
                self.btn_run.set_text("开始转换（需先激活）")
        except Exception:
            pass

    def _require_activation(self):
        """弹激活框；成功则存档并解锁，失败/稍后则继续用壳子（更新可用）。"""
        dlg = ActivationDialog(self)
        self.wait_window(dlg)
        if dlg.result == "ok":
            save_cached_license(dlg.code)
            self._set_licensed(True, None)

    def _build_ui(self):
        head = tk.Frame(self, bg=BG_APP)
        head.pack(fill="x", padx=PAD, pady=(PAD, 6))
        self._header_icon(head)
        tk.Label(head, text="PDF 转 TXT", bg=BG_APP, fg=FG_TEXT,
                 font=font(17, "bold")).pack(side="left")
        tk.Label(head, text="把 PDF 里的文字（含图片里的字）导出成 .txt",
                 bg=BG_APP, fg=FG_DIM, font=font(10)).pack(side="left", padx=(10, 0))
        self.btn_feedback = RoundButton(head, "反馈", command=self._on_feedback,
                                        kind="ghost", height=30, font_size=10)
        self.btn_feedback.pack(side="right", padx=(0, 8))
        self.btn_check_update = RoundButton(head, "检查更新", command=self._on_check_update,
                                            kind="ghost", height=30, font_size=10)
        self.btn_check_update.pack(side="right")

        card = RoundedPanel(self, bg=BG_ELEV, radius=RADIUS_MD)
        card.pack(fill="x", padx=PAD, pady=6)
        body = card.content()
        body.columnconfigure(2, weight=1)

        tk.Label(body, text="文件模式", bg=BG_ELEV, fg=FG_DIM, font=font(10),
                 width=10, anchor="w").grid(row=0, column=0, sticky="w", padx=12, pady=(12, 8))
        self.seg_sel = Segmented(body, [("single", "单个文件"), ("batch", "批量文件")],
                                 value=self.sel_mode.get(),
                                 command=lambda v: self._on_sel_mode(v))
        self.seg_sel.grid(row=0, column=1, sticky="w", padx=(0, 10), pady=(12, 8))
        self.lbl_sel_hint = tk.Label(body, text="一次挑一个 PDF", bg=BG_ELEV,
                                     fg=FG_MUTED, font=font(9), anchor="w")
        self.lbl_sel_hint.grid(row=0, column=2, sticky="w", padx=(0, 12), pady=(12, 8))

        tk.Label(body, text="选择 PDF", bg=BG_ELEV, fg=FG_DIM, font=font(10),
                 width=10, anchor="w").grid(row=1, column=0, sticky="w", padx=12, pady=8)
        self.pick_frame = tk.Frame(body, bg=BG_ELEV)
        self.pick_frame.grid(row=1, column=1, sticky="w", padx=(0, 10), pady=8)
        self.btn_pick = RoundButton(self.pick_frame, "选择 PDF", command=self._pick, kind="accent")
        self.btn_pick.pack(side="left")
        self.btn_pick_folder = RoundButton(self.pick_frame, "选择文件夹", command=self._pick_folder, kind="solid")
        self.btn_pick_folder.pack(side="left", padx=(8, 0))
        self.lbl_pdf = tk.Label(body, text="（还没选文件）", bg=BG_ELEV, fg=FG_MUTED,
                                font=font(10), anchor="w")
        self.lbl_pdf.grid(row=1, column=2, sticky="ew", padx=(0, 12), pady=8)

        tk.Label(body, text="PDF 类型", bg=BG_ELEV, fg=FG_DIM, font=font(10),
                 width=10, anchor="w").grid(row=2, column=0, sticky="w", padx=12, pady=8)
        self.seg_ocr = Segmented(body, [("text", "纯文字（不开 OCR）"),
                                        ("image", "含图片（识别图里字）")],
                                 value=self.ocr_mode.get(),
                                 command=lambda v: self._on_ocr_mode(v))
        self.seg_ocr.grid(row=2, column=1, sticky="w", padx=(0, 10), pady=8)
        self.lbl_ocr_hint = tk.Label(body, text="", bg=BG_ELEV, fg=FG_MUTED, font=font(9))
        self.lbl_ocr_hint.grid(row=2, column=2, sticky="w", padx=(0, 12), pady=8)
        self._update_ocr_hint()
        self._sync_pick_ui()

        tk.Label(body, text="保存为", bg=BG_ELEV, fg=FG_DIM, font=font(10),
                 width=10, anchor="w").grid(row=3, column=0, sticky="w", padx=12, pady=(8, 12))
        self.btn_save = RoundButton(body, "更改位置", command=self._pick_save, kind="solid")
        self.btn_save.grid(row=3, column=1, sticky="w", padx=(0, 10), pady=(8, 12))
        self.lbl_out = tk.Label(body, text="（与各 PDF 同目录，同名 .txt）", bg=BG_ELEV,
                                fg=FG_MUTED, font=font(10), anchor="w")
        self.lbl_out.grid(row=3, column=2, sticky="ew", padx=(0, 12), pady=(8, 12))

        self.btn_run = RoundButton(self, "开始转换", command=self._start, kind="accent",
                                   height=40, font_size=12)
        self.btn_run.pack(padx=PAD, pady=(2, 8))

        log_card = RoundedPanel(self, bg=BG_SUNKEN, radius=RADIUS_MD)
        log_card.pack(fill="both", expand=True, padx=PAD, pady=(0, PAD))
        lf = log_card.content()
        self.log = scrolledtext.ScrolledText(lf, bg=BG_SUNKEN, fg=FG_TEXT,
                                             font=mono(10), relief="flat",
                                             highlightthickness=0, padx=10, pady=8,
                                             insertbackground=FG_TEXT)
        self.log.pack(fill="both", expand=True)
        self.log.configure(state="disabled")
        self.log.tag_config("info", foreground=FG_DIM)
        self.log.tag_config("ok", foreground=FG_OK)
        self.log.tag_config("warn", foreground=FG_WARN)
        self.log.tag_config("bad", foreground=FG_BAD)
        self._welcome()

        foot = tk.Frame(self, bg=BG_APP)
        foot.pack(fill="x", padx=PAD, pady=(0, PAD))
        self.btn_open_txt = RoundButton(foot, "打开 TXT", command=self._open_txt, kind="solid")
        self.btn_open_txt.set_state("disabled")
        self.btn_open_txt.pack(side="right", padx=(8, 0))
        self.btn_open_dir = RoundButton(foot, "打开文件夹", command=self._open_dir, kind="solid")
        self.btn_open_dir.set_state("disabled")
        self.btn_open_dir.pack(side="right")
        self.lbl_status = tk.Label(foot, text="就绪", bg=BG_APP, fg=FG_DIM, font=font(10))
        self.lbl_status.pack(side="left")

    def _header_icon(self, parent):
        c = tk.Canvas(parent, width=26, height=26, bg=BG_APP, highlightthickness=0)
        c.pack(side="left", padx=(0, 8))
        round_rect(c, 2, 2, 22, 24, 4, fill=ACCENT, outline="")
        c.create_line(8, 9, 16, 9, fill="#ffffff", width=1.6)
        c.create_line(8, 13, 16, 13, fill="#ffffff", width=1.6)
        c.create_line(8, 17, 13, 17, fill="#ffffff", width=1.6)
        c.create_line(20, 17, 24, 21, fill=FG_OK, width=1.6)
        c.create_line(24, 21, 21, 21, fill=FG_OK, width=1.6)
        c.create_line(24, 21, 24, 18, fill=FG_OK, width=1.6)

    def _sync_pick_ui(self):
        # 单个文件模式只显示「选择 PDF」；批量模式再显示「选择文件夹」。
        if self.sel_mode.get() == "single":
            self.btn_pick.set_text("选择 PDF")
            self.btn_pick_folder.pack_forget()
        else:
            self.btn_pick.set_text("选择多个 PDF")
            self.btn_pick_folder.pack(side="left", padx=(8, 0))

    def _on_sel_mode(self, v):
        self.sel_mode.set(v)
        self.pdf_list = []
        self.input_kind = "files"
        self.src_folder = ""
        self.out_root = ""
        self.lbl_pdf.configure(text="（还没选文件）", fg=FG_MUTED)
        self.lbl_out.configure(text="（与各 PDF 同目录，同名 .txt）", fg=FG_MUTED)
        self._sync_pick_ui()
        if v == "single":
            self.lbl_sel_hint.configure(text="一次挑一个 PDF")
        else:
            self.lbl_sel_hint.configure(text="可多选文件，也可直接选一个装 PDF 的文件夹")
        self._log("info", "已切换到：" + ("单个文件" if v == "single" else "批量文件") + " 模式。")

    def _on_ocr_mode(self, v):
        self.ocr_mode.set(v)
        self._update_ocr_hint()

    def _update_ocr_hint(self):
        if self.ocr_mode.get() == "image":
            self.lbl_ocr_hint.configure(text="先取原生文字，再从图片里补字（默认开）")
        else:
            self.lbl_ocr_hint.configure(text="只取本来就有的文字，最快")

    def _pick(self):
        if self._running:
            return
        self.input_kind = "files"
        self.src_folder = ""
        self.out_root = ""
        if self.sel_mode.get() == "single":
            path = filedialog.askopenfilename(
                title="选择一个 PDF", filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")])
            if path:
                self.pdf_list = [path]
                self.lbl_pdf.configure(text=self._shorten(path), fg=FG_TEXT)
                self._log("info", "已选择：" + path)
        else:
            paths = filedialog.askopenfilenames(
                title="选择多个 PDF（可框选 / Ctrl 多选）",
                filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")])
            pdfs = [p for p in paths if p.lower().endswith(".pdf")]
            if pdfs:
                self.pdf_list = list(pdfs)
                self.lbl_pdf.configure(
                    text="已选 %d 个 PDF（首个：%s）" % (len(pdfs), self._shorten(pdfs[0])),
                    fg=FG_TEXT)
                self._log("info", "已选择 %d 个 PDF。" % len(pdfs))
        self._refresh_out_label()

    def _pick_save(self):
        if self._running:
            return
        d = filedialog.askdirectory(title="选择 .txt 保存位置（所有输出都放这）")
        if d:
            self.save_dir.set(d)
            self._log("info", "保存位置改为：" + d)
        self._refresh_out_label()

    @staticmethod
    def _collect_folder_pdfs(folder: str):
        """递归收集文件夹里所有的 .pdf（含子文件夹），按路径排序。"""
        out = []
        for root, _dirs, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(".pdf"):
                    out.append(os.path.join(root, f))
        out.sort()
        return out

    def _pick_folder(self):
        if self._running:
            return
        if self.sel_mode.get() != "batch":
            return
        d = filedialog.askdirectory(title="选择一个装满了 PDF 的文件夹")
        if not d:
            return
        self.input_kind = "folder"
        self.src_folder = d
        pdfs = self._collect_folder_pdfs(d)
        self.pdf_list = pdfs
        # 输出文件夹：默认放在源文件夹旁边，叫「<源文件夹名> TXT 版」；
        # 如果点过「更改位置」，就放在那个位置里。
        name = os.path.basename(d.rstrip(os.sep)) + " TXT 版"
        if self.save_dir.get():
            self.out_root = os.path.join(self.save_dir.get(), name)
        else:
            self.out_root = os.path.join(os.path.dirname(d), name)
        if not pdfs:
            self.lbl_pdf.configure(text="（这个文件夹里没有 PDF）", fg=FG_WARN)
            self._log("warn", "文件夹里没找到 PDF：" + d)
        else:
            self.lbl_pdf.configure(
                text="文件夹：%s（%d 个 PDF）" % (self._shorten(d), len(pdfs)), fg=FG_TEXT)
            self._log("info", "已选择文件夹：%s，共 %d 个 PDF。" % (d, len(pdfs)))
            self._log("info", "输出文件夹将是：%s" % self.out_root)
        self._refresh_out_label()

    def _out_for(self, pdf: str) -> str:
        if self.input_kind == "folder" and self.src_folder and self.out_root:
            # 文件夹模式：保持原来的目录结构，落到「<源文件夹名> TXT 版」里
            rel_dir = os.path.relpath(os.path.dirname(pdf), self.src_folder)
            if rel_dir == ".":
                rel_dir = ""
            stem = os.path.splitext(os.path.basename(pdf))[0]
            return os.path.join(self.out_root, rel_dir, stem + ".txt")
        stem = os.path.splitext(os.path.basename(pdf))[0]
        d = self.save_dir.get() or os.path.dirname(pdf)
        return os.path.join(d, stem + ".txt")

    def _refresh_out_label(self):
        if self.input_kind == "folder" and self.out_root:
            self.lbl_out.configure(
                text="输出文件夹：%s（%d 个 .txt）" % (self._shorten(self.out_root), len(self.pdf_list)),
                fg=FG_DIM)
            return
        if not self.pdf_list:
            self.lbl_out.configure(text="（与各 PDF 同目录，同名 .txt）", fg=FG_MUTED)
            return
        if self.save_dir.get():
            self.lbl_out.configure(
                text="全部保存到：" + self._shorten(self.save_dir.get()), fg=FG_DIM)
        elif len(self.pdf_list) == 1:
            self.lbl_out.configure(text=self._shorten(self._out_for(self.pdf_list[0])), fg=FG_DIM)
        else:
            self.lbl_out.configure(
                text="将生成 %d 个 .txt（与各 PDF 同目录）" % len(self.pdf_list), fg=FG_DIM)

    @staticmethod
    def _shorten(p: str, n=66) -> str:
        if not p or len(p) <= n:
            return p
        return p[: n // 2] + "…" + p[-(n // 2):]

    def _welcome(self):
        self._log("info", "欢迎使用 PDF 转 TXT。")
        self._log("info", "① 选「单个/批量」挑 PDF   ② 选「含图片/纯文字」   ③ 点「开始转换」。")
        self._log("info", "批量模式还能直接选一个「装满了 PDF 的文件夹」→ 输出一个同名 +「 TXT 版」的文件夹。")

    def _log(self, kind, msg):
        try:
            self.log.configure(state="normal")
            self.log.insert("end", msg + "\n", kind)
            self.log.configure(state="disabled")
            self.log.see("end")
        except tk.TclError:
            pass

    def _poll_queue(self):
        try:
            while True:
                item = self._queue.get_nowait()
                if item[0] == "log":
                    self._log(item[1], item[2])
                elif item[0] == "status":
                    self.lbl_status.configure(text=item[1])
                elif item[0] == "done":
                    self._on_done(item[1], item[2])
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    def _start(self):
        if not self.licensed:
            self._require_activation()
            if not self.licensed:
                return
        if self._running:
            return
        if not self.pdf_list:
            messagebox.showwarning("还没选 PDF", "请先点「选择 PDF」挑文件（批量可多选）。")
            return
        self._running = True
        self.btn_run.set_state("disabled")
        self.btn_pick.set_state("disabled")
        self.btn_save.set_state("disabled")
        self.btn_open_txt.set_state("disabled")
        self.btn_open_dir.set_state("disabled")
        self._log("info", "── 开始转换（%d 个文件）──" % len(self.pdf_list))
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        total_files = len(self.pdf_list)
        ok_count = 0
        outs = []
        for fi, pdf in enumerate(self.pdf_list, 1):
            out = self._out_for(pdf)
            self._queue.put(("log", "info",
                             "[%d/%d] %s" % (fi, total_files, os.path.basename(pdf))))
            try:
                n = convert_file(pdf, out, self.ocr_mode.get(),
                                 lambda k, m: self._queue.put(("log", k, m)))
                ok_count += 1
                outs.append(out)
                self._queue.put(("log", "ok", "  ↳ 完成，%d 页 → %s" % (n, out)))
            except Exception as e:
                self._queue.put(("log", "bad", "  ↳ 失败：" + str(e)))
                self._queue.put(("log", "bad", "     " + (traceback.format_exc().splitlines()[-1] if traceback.format_exc() else "")))
        self._last_outs = outs
        self._queue.put(("status", "完成 %d/%d 个文件" % (ok_count, total_files)))
        self._queue.put(("done", ok_count > 0, outs))

    def _on_done(self, ok, outs):
        self._running = False
        self.btn_run.set_state("normal")
        self.btn_pick.set_state("normal")
        self.btn_save.set_state("normal")
        if ok and outs:
            self._log("ok", "全部完成，成功 %d 个文件。" % len(outs))
            self.btn_open_txt.set_state("normal")
            self.btn_open_dir.set_state("normal")
        else:
            self._log("bad", "没有文件转换成功，请检查上面的红色信息。")
            self.lbl_status.configure(text="失败")

    def _open_txt(self):
        if self._last_outs:
            open_path(self._last_outs[0])

    def _open_dir(self):
        if self.input_kind == "folder" and self.out_root and os.path.isdir(self.out_root):
            d = self.out_root
        elif self._last_outs:
            d = os.path.dirname(self._last_outs[0])
        elif self.save_dir.get():
            d = self.save_dir.get()
        elif self.pdf_list:
            d = os.path.dirname(self.pdf_list[0])
        else:
            return
        if d and os.path.isdir(d):
            open_path(d)

    # ------------------------------------------------------------------
    # 检查更新 / 下载更新
    # ------------------------------------------------------------------
    def _on_check_update(self):
        """点『检查更新』时调用；若弹窗已开就把它提到前面重新检查。"""
        dlg = getattr(self, "_upd_dlg", None)
        if dlg and dlg.winfo_exists():
            dlg.lift()
            dlg.show_checking()
            threading.Thread(target=self._check_thread, args=(dlg,), daemon=True).start()
            return
        dlg = UpdateDialog(self, APP_VERSION)
        self._upd_dlg = dlg
        dlg.show_checking()
        threading.Thread(target=self._check_thread, args=(dlg,), daemon=True).start()

    def _on_feedback(self):
        """点『反馈』时调用，弹出反馈弹窗。"""
        FeedbackDialog(self, APP_VERSION)

    def _check_thread(self, dlg):
        try:
            info = _fetch_manifest_race()
            remote = info.get("version", "")
            if not _version_newer(remote, APP_VERSION):
                dlg.after_idle(lambda: _safe(lambda: dlg.show_uptodate()))
                return
            dlg.after_idle(lambda: _safe(lambda: dlg.show_update(info)))
        except Exception as e:
            dlg.after_idle(lambda: _safe(lambda: dlg.show_error(str(e))))

    def _begin_update(self, info, dlg):
        if getattr(self, "_updating", False):
            return
        self._updating = True
        threading.Thread(target=self._update_thread, args=(info, dlg), daemon=True).start()

    def _update_thread(self, info, dlg):
        try:
            if getattr(sys, "frozen", False) and sys.platform.startswith("win"):
                w = info.get("windows") or {}
                expected = (w.get("sha256") or "").strip().lower()
                expect_size = int(w.get("size") or 0)
                # 候选下载地址：urls（多个镜像，程序挑最快）+ url（兼容老清单）+ fallback（官方兜底）
                raw_cands = []
                seen = set()
                for u in (w.get("urls") or []):
                    u = (u or "").strip()
                    if u and u not in seen:
                        raw_cands.append(u); seen.add(u)
                for key in ("url", "fallback"):
                    u = (w.get(key) or "").strip()
                    if u and u not in seen:
                        raw_cands.append(u); seen.add(u)
                if not raw_cands:
                    raise RuntimeError("清单里没有 Windows 下载地址（windows.url / urls / fallback）")
                cands = [(u, _label_for(u)) for u in raw_cands]

                # 1) 先探每个源的真实速度，挑最快的先用（参考哈夫克图片程序）
                dlg.after_idle(lambda: _safe(dlg.show_sourcing))
                try:
                    cands = _order_sources(cands, expect_size=expect_size)
                except Exception:
                    pass   # 探测失败就按原顺序试，不阻断下载
                dest = os.path.join(os.path.dirname(sys.executable), "PDFtoTXT_new.exe")
                got = None
                last_err = None
                for idx, (u, label) in enumerate(cands, 1):
                    # 开始这个源：界面写清楚“正在从哪个源下（第几个源）”
                    dlg._dl_source = label          # 先直接记下来，避免首帧进度条错位
                    dlg.after_idle(lambda lb=label, n=idx, tot=len(cands): _safe(
                        lambda: dlg.start_source(lb, n, tot)))
                    # 同一个源最多试 3 次（应对网络抖掉），每次都从临时文件重新下
                    for attempt in range(3):
                        try:
                            got = _download_parallel(
                                u, dest,
                                expect_size=expect_size,
                                progress=lambda g, t, sp, lb=label: dlg.after_idle(
                                    lambda: _safe(lambda: dlg.set_downloading(g, t, lb, sp))))
                            if expected and got.lower() != expected:
                                try:
                                    os.remove(dest)
                                except Exception:
                                    pass
                                raise RuntimeError("下载文件校验失败（sha256 不一致），已删除，可能被篡改")
                            break   # 这个源成功
                        except Exception as e:
                            last_err = e
                            if attempt < 2:
                                dlg.after_idle(lambda: _safe(lambda: dlg.note_retry()))
                            continue
                    if got is not None:
                        break
                if got is None:
                    raise RuntimeError("所有下载地址都失败了：" + str(last_err))
                dlg.after_idle(lambda: _safe(lambda: dlg.show_downloaded(dest)))
            else:
                # 统信 UOS / 其它 Linux：分两种情况
                #  ① 打包成单个文件（./dist/PDFtoTXT）：下载预编译的 Linux 二进制替换自己
                #  ② 直接跑源码（python3 pdf2txt.py）：下载源码包，解压替换后重启
                # 两种都是「手动点『安装并更新』」，不会偷偷动你文件。
                if sys.platform.startswith("linux"):
                    lnx = info.get("linux") or {}
                    if getattr(sys, "frozen", False):
                        b = lnx.get("binary") or {}
                        if b.get("url"):
                            self._download_and_install_linux(b, dlg, is_binary=True)
                            return
                    else:
                        s = lnx.get("source") or {}
                        if s.get("url"):
                            self._download_and_install_linux(s, dlg, is_binary=False)
                            return
                # 清单里没有对应 Linux 地址，或不是 Linux：打开发布页让你自己拿
                target = info.get("url") or UPDATE_MANIFEST_URL
                open_path(target)
                dlg.after_idle(lambda: _safe(dlg.show_opened_page))
        except Exception as e:
            dlg.after_idle(lambda: _safe(lambda: dlg.show_error(str(e))))
        finally:
            self._updating = False

    def _install_update_windows(self, new_exe):
        """下载完成后：写个助手脚本，关掉自己，由助手在程序完全退出（释放文件锁）后，
        把新 exe 替换上来并重启新版本。助手窗口隐藏，不会闪；路径全用绝对路径，
        不依赖工作目录。"""
        cur = os.path.abspath(sys.executable)
        d = os.path.dirname(cur)
        bat = os.path.join(d, "_pdf2txt_update.bat")
        old = os.path.join(d, "_pdf2txt_old.exe")
        new = os.path.abspath(new_exe)
        cur_s = cur.replace("/", "\\")
        old_s = old.replace("/", "\\")
        new_s = new.replace("/", "\\")
        bat_s = bat.replace("/", "\\")
        try:
            # 用 UTF-8 BOM + chcp 65001，确保中文路径（如 王浩然）在 cmd 里不会被读错
            with open(bat, "w", encoding="utf-8-sig") as f:
                f.write('@echo off\r\n')
                f.write('chcp 65001 >nul\r\n')
                f.write('if exist "%s" del "%s"\r\n' % (old_s, old_s))  # 清掉上次残留
                f.write('timeout /t 1 /nobreak >nul\r\n')        # 等本程序退出、释放文件锁
                f.write(':retry\r\n')
                f.write('rename "%s" "_pdf2txt_old.exe" >nul 2>&1\r\n' % cur_s)
                f.write('if exist "%s" goto moved\r\n' % old_s)
                f.write('goto retry\r\n')
                f.write(':moved\r\n')
                f.write('move /Y "%s" "%s" >nul\r\n' % (new_s, cur_s))
                f.write('start "" "%s"\r\n' % cur_s)
                f.write('del "%s"\r\n' % old_s)
                f.write('del "%s"\r\n' % bat_s)
            si = subprocess.STARTUPINFO()
            si.dwFlags = subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0   # 隐藏助手的控制台窗口
            # 用新进程组 + 关闭继承的句柄，保证助手不随本程序一起死
            subprocess.Popen(["cmd.exe", "/c", bat],
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                            startupinfo=si, close_fds=True)
        except Exception:
            open_path(d)
            return
        self.quit()
        sys.exit(0)

    # ------------------------------------------------------------------
    # 统信 UOS / Linux 的手动点击更新
    # ------------------------------------------------------------------
    def _install_update(self, new_path, mode="win"):
        """「安装并更新」按钮总入口：按平台分派。"""
        if mode == "linux_bin":
            self._install_update_linux_binary(new_path)
        elif mode == "linux_src":
            self._install_update_linux_source(new_path)
        else:
            self._install_update_windows(new_path)

    def _download_and_install_linux(self, dl, dlg, is_binary):
        """Linux / UOS 专用：挑最快源下载（带 sha256 校验），下载完交给对话框等用户点安装。

        is_binary=True  → 下载预编译的 Linux 单文件，安装时整包替换自己；
        is_binary=False → 下载源码包，安装时解压覆盖当前目录后重启。"""
        expected = (dl.get("sha256") or "").strip().lower()
        expect_size = int(dl.get("size") or 0)
        if is_binary:
            dest = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "PDFtoTXT_new")
        else:
            dest = os.path.join(tempfile.gettempdir(), "pdf2txt_source.tar.gz")
        got = self._linux_download(dl, dlg, expect_size, dest)
        if expected and got.lower() != expected:
            try:
                os.remove(dest)
            except Exception:
                pass
            raise RuntimeError("下载文件校验失败（sha256 不一致），已删除，可能被篡改")
        if is_binary:
            dlg._update_mode = "linux_bin"
            dlg.after_idle(lambda: _safe(lambda: dlg.show_downloaded(dest)))
        else:
            # 解压源码包覆盖当前目录（同目录里的 .py 等）
            dlg.after_idle(lambda: _safe(dlg.show_extracting))
            here = os.path.dirname(os.path.abspath(__file__))
            with tarfile.open(dest, "r:gz") as tf:
                for m in tf.getmembers():
                    if not m.isfile():
                        continue
                    name = m.name
                    if "/" in name:
                        parts = name.split("/")
                        name = "/".join(parts[1:]) if len(parts) > 1 else parts[0]
                    if not name:
                        continue
                    target = os.path.normpath(os.path.join(here, name))
                    base = os.path.normpath(here)
                    if target != base and not target.startswith(base + os.sep):
                        continue   # 防目录穿越：只许解到程序目录内
                    f = tf.extractfile(m)
                    if not f:
                        continue
                    with open(target, "wb") as out:
                        out.write(f.read())
            try:
                os.remove(dest)
            except Exception:
                pass
            dlg._update_mode = "linux_src"
            dlg.after_idle(lambda: _safe(lambda: dlg.show_downloaded(here)))

    def _linux_download(self, dl, dlg, expect_size, dest):
        """Linux / UOS 专用：挑最快源、每源重试 3 次，返回下载文件的 sha256。"""
        cands = _uos_candidates(dl)
        if not cands:
            raise RuntimeError("清单里没有 Linux 下载地址（linux.source / linux.binary）")
        dlg.after_idle(lambda: _safe(dlg.show_sourcing))
        try:
            cands = _order_sources(cands, expect_size=expect_size)
        except Exception:
            pass
        got = None
        last_err = None
        for idx, (u, label) in enumerate(cands, 1):
            dlg._dl_source = label
            dlg.after_idle(lambda lb=label, n=idx, tot=len(cands): _safe(
                lambda: dlg.start_source(lb, n, tot)))
            for attempt in range(3):
                try:
                    got = _download_file(
                        u, dest,
                        expect_size=expect_size,
                        progress=lambda g, t: dlg.after_idle(
                            lambda: _safe(lambda: dlg.set_downloading(g, t, label))))
                    break
                except Exception as e:
                    last_err = e
                    if attempt < 2:
                        dlg.after_idle(lambda: _safe(lambda: dlg.note_retry()))
                    continue
            if got is not None:
                break
        if got is None:
            raise RuntimeError("所有下载地址都失败了：" + str(last_err))
        return got

    def _install_update_linux_binary(self, new_path):
        """打包单文件：写个隐藏助手脚本，关掉自己，由助手替换并重启（避免文件锁卡死）。"""
        cur = os.path.abspath(sys.executable)
        d = os.path.dirname(cur)
        helper = os.path.join(d, "_pdf2txt_update.sh")
        new = os.path.abspath(new_path)
        try:
            with open(helper, "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env bash\n")
                f.write("sleep 1\n")                       # 等本程序退出、释放文件锁
                f.write('mv -f "%s" "%s"\n' % (new, cur))  # 新版本顶上来
                f.write('chmod +x "%s"\n' % cur)
                f.write('exec "%s"\n' % cur)               # 重启新版本
            os.chmod(helper, 0o755)
            subprocess.Popen(["bash", helper], start_new_session=True)
        except Exception:
            open_path(d)
            return
        self.quit()
        sys.exit(0)

    def _install_update_linux_source(self, new_path):
        """源码模式：写个隐藏助手脚本，关掉自己，由助手用原来的方式重启（python3 pdf2txt.py）。"""
        cur = os.path.abspath(sys.executable)        # 多半是 venv 里的 python3，自带依赖
        script = os.path.abspath(__file__)
        d = os.path.dirname(script)
        helper = os.path.join(d, "_pdf2txt_restart.sh")
        try:
            with open(helper, "w", encoding="utf-8") as f:
                f.write("#!/usr/bin/env bash\n")
                f.write("sleep 1\n")                       # 等本程序退出
                f.write('cd "%s"\n' % d)
                f.write('exec "%s" "%s"\n' % (cur, script))  # 以原方式重启
            os.chmod(helper, 0o755)
            subprocess.Popen(["bash", helper], start_new_session=True)
        except Exception:
            open_path(d)
            return
        self.quit()
        sys.exit(0)

    def _autocheck(self):
        """静默检查：只有发现新版本才弹窗。"""
        threading.Thread(target=self._autocheck_thread, daemon=True).start()

    def _autocheck_thread(self):
        try:
            info = _fetch_manifest_race()
            if _version_newer(info.get("version", ""), APP_VERSION):
                self.after_idle(self._on_check_update)
        except Exception:
            pass


def _safe(fn):
    """在主线程外更新 UI 时，弹窗可能已关；包一层避免崩。"""
    try:
        fn()
    except Exception:
        pass


def _run_headless(argv):
    """无界面模式：pdf2txt.exe --headless --in a.pdf [--out a.txt] [--mode image|text] [--code 激活码]"""
    args = {"in": None, "out": None, "mode": "image", "code": None}
    it = iter(argv[1:])
    for a in it:
        if a in ("--in", "--out", "--mode", "--code"):
            try:
                args[a.lstrip("-")] = next(it)
            except StopIteration:
                pass
        elif a == "--headless":
            continue
    # 商用激活（无界面模式同样需要授权）
    cached = load_cached_license()
    if cached and validate_license_format(cached.get("code", "")):
        try:
            r = verify_license_online(cached["code"])
            if r.get("status") == "revoked":
                print("该激活码已被吊销，无法使用。")
                return 1
            if r.get("status") not in ("ok",):
                print("本地激活状态异常，请重新激活（--code <码>）。")
                return 1
        except Exception:
            pass  # 联网失败：离线信任本地缓存
    else:
        code = args.get("code")
        if code:
            try:
                r = activate_license_online(code)
            except Exception as e:
                print("联网激活失败：", e)
                return 1
            if r.get("status") == "ok":
                save_cached_license(code)
                print("激活成功。")
            else:
                print("激活失败：", r.get("status"))
                return 1
        else:
            print("未激活。请先激活：--code <你的激活码>")
            return 1
    if not args["in"]:
        print("用法: pdf2txt --headless --in 文件.pdf [--out 输出.txt] [--mode image|text]")
        return 2
    if not args["in"].lower().endswith(".pdf") or not os.path.isfile(args["in"]):
        print("找不到 PDF：", args["in"])
        return 2
    if not args["out"]:
        args["out"] = os.path.splitext(args["in"])[0] + ".txt"
    print("转换:", args["in"], "->", args["out"], "模式:", args["mode"])
    try:
        n = convert_file(args["in"], args["out"], args["mode"],
                         lambda k, m: print(("[" + k + "] " if k != "info" else "") + m))
        print("完成，共 %d 页。" % n)
        return 0
    except Exception as e:
        print("失败：", e)
        traceback.print_exc()
        return 1


def _warn_stale_copy():
    """如果你打开的是上一次更新时被改名留下的旧副本（PDFtoTXT_old.exe），
    它可能不是最新版、更新功能也会不正常。弹个窗提醒一下，免得稀里糊涂用着旧程序。"""
    try:
        if sys.platform.startswith("win") and \
           os.path.basename(sys.executable).lower() == "pdftotxt_old.exe":
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning(
                "你打开的是旧副本",
                "你正在运行的这个程序，文件名是 PDFtoTXT_old.exe，\n"
                "是上一次更新时留下的旧副本。它可能已不是最新版，更新功能也可能不正常。\n\n"
                "建议：关掉这个窗口，去桌面双击打开 PDFtoTXT.exe（没有 _old 的那个）。")
            root.destroy()
    except Exception:
        pass


def main():
    if "--headless" in sys.argv:
        sys.exit(_run_headless(sys.argv))
    feedback.install_error_capture(APP_VERSION)   # 出错自动记录并询问是否发邮件
    _warn_stale_copy()
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
