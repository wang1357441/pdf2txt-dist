# -*- coding: utf-8 -*-
"""PDFtoTXT 的反馈模块（干净、安全、可商用）。

两种反馈：
  1) 手动：点「反馈」按钮 → 写一句话 → 打开你的邮件软件，一封写好收件人/正文的
     邮件草稿就出来了，你点「发送」即可。
  2) 自动：程序运行时若出错（包括你点按钮触发的崩溃），会自动记下来，并弹窗问
     「要发给作者吗？」→ 点发送就打开同样的邮件草稿。

安全规矩（和主程序一致）：
  * 不发任何密码 / token。只写「发给谁」（一个邮箱地址，不是密码，可公开）。
  * 不偷偷上传任何东西；一切要你亲手点「发送」。
  * 出错信息只存在你自己电脑的临时文件里，随邮件内容发，绝不会自动外传。
  * 不读你的 PDF 内容，只可能记文件「路径」（方便复现问题），绝不读内容。
"""
from __future__ import annotations

import os
import sys
import time
import threading
import tempfile
import webbrowser
import urllib.parse
import traceback
import base64

# 收件人：改成你自己的邮箱即可。这只是「发给谁」，不是密码，可以放心写在这里。
FEEDBACK_RECIPIENT = "nhgn0763@agent.qq.com"

# 错误日志存哪（你电脑的临时目录，不会随程序打包出去）
_ERROR_LOG = os.path.join(tempfile.gettempdir(), "pdf2txt_errors.log")
_lock = threading.Lock()
_recent = []          # 内存里的近期错误（最多留 5 条）
_APP_VERSION = ""     # 由 install_error_capture / send_via_mailto 写入


def set_version(v):
    global _APP_VERSION
    _APP_VERSION = v or ""


def _clip(text, n):
    """只留最后 n 个字符，避免 mailto 地址过长。"""
    text = text or ""
    return text if len(text) <= n else text[-n:]


def record_error(text):
    """把一段错误文本同时记到内存和本地日志文件。"""
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), text)
    with _lock:
        _recent.append(line)
        if len(_recent) > 5:
            _recent.pop(0)
    try:
        with open(_ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        # 控制日志体积，避免长期运行无限增长（只留最近 200 行）
        try:
            if os.path.getsize(_ERROR_LOG) > 51200:
                with open(_ERROR_LOG, "r", encoding="utf-8", errors="replace") as g:
                    kept = g.readlines()[-200:]
                with open(_ERROR_LOG, "w", encoding="utf-8") as g:
                    g.writelines(kept)
        except Exception:
            pass
    except Exception:
        pass


def recent_errors():
    with _lock:
        mem = list(_recent)
    try:
        with open(_ERROR_LOG, "r", encoding="utf-8", errors="replace") as f:
            file_txt = f.read().strip()
    except Exception:
        file_txt = ""
    return mem, file_txt


def gather_system_info():
    info = []
    info.append("版本：%s" % (_APP_VERSION or "未知"))
    info.append("系统：%s / Python %s" % (sys.platform, sys.version.split()[0]))
    info.append("是否打包为 exe：%s" % bool(getattr(sys, "frozen", False)))
    if getattr(sys, "frozen", False):
        info.append("程序路径：%s" % sys.executable)
    return "\n".join(info)


def build_body(user_text, crash=None, include_errors=True):
    parts = []
    parts.append("【用户留言】")
    parts.append((user_text or "").strip() or "（用户没写额外内容）")
    parts.append("")
    parts.append("【系统信息】")
    parts.append(gather_system_info())
    if crash:
        parts.append("")
        parts.append("【本次错误】")
        parts.append(_clip(crash, 4000))
    elif include_errors:
        mem, file_txt = recent_errors()
        err = "\n".join(mem) if mem else file_txt
        if err.strip():
            parts.append("")
            parts.append("【近期错误记录】")
            parts.append(_clip(err, 3000))
    parts.append("")
    parts.append("（本邮件由 PDFtoTXT 反馈功能生成，不会包含你的 PDF 文件内容。）")
    return "\n".join(parts)


def _b64(s):
    """把字符串做 base64（用于 .eml 的正文/标题，避免特殊字符出问题）。"""
    return base64.b64encode((s or "").encode("utf-8")).decode("ascii")


def _start_file(path):
    """跨平台打开一个文件/URL（Windows 用 os.startfile，其它用浏览器助手）。"""
    if sys.platform.startswith("win"):
        os.startfile(path)
    else:
        webbrowser.open(path)


def _write_eml(subject, body):
    """mailto 太长时，改写一封 .eml 草稿到临时目录再打开（邮件软件都能认）。"""
    path = os.path.join(tempfile.gettempdir(), "pdf2txt_feedback.eml")
    lines = [
        "To: %s" % FEEDBACK_RECIPIENT,
        "Subject: =?utf-8?B?%s?=" % _b64(subject or "PDFtoTXT 反馈"),
        "Date: %s" % time.strftime("%a, %d %b %Y %H:%M:%S +0000", time.gmtime()),
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=UTF-8",
        "Content-Transfer-Encoding: base64",
        "",
        _b64(body or ""),
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def _open_mailto(subject, body):
    """打开邮件软件写反馈。mailto 太长（Windows 经常打不开/截断）时，
    自动降级成写一封 .eml 草稿再打开，保证反馈一定能发出去。"""
    url = "mailto:%s?subject=%s&body=%s" % (
        FEEDBACK_RECIPIENT,
        urllib.parse.quote(subject or "PDFtoTXT 反馈"),
        urllib.parse.quote(body or ""),
    )
    if len(url) > 1900:
        try:
            eml = _write_eml(subject, body)
            if eml and os.path.exists(eml):
                _start_file(eml)
                return True
        except Exception:
            pass
    try:
        _start_file(url)
        return True
    except Exception:
        try:
            webbrowser.open(url)
            return True
        except Exception:
            return False


def send_via_mailto(user_text, version=None, crash=None, include_errors=True):
    """打开邮件软件，返回是否成功打开。user_text 是用户写的留言。"""
    if version:
        set_version(version)
    body = build_body(user_text, crash=crash, include_errors=include_errors)
    return _open_mailto("PDFtoTXT 反馈（v%s）" % (_APP_VERSION or "未知"), body)


def install_error_capture(version=""):
    """装好全局错误捕获：Tk 回调异常 + 其它未捕获异常，都会记录并询问是否发邮件。"""
    set_version(version)

    def _first_lines(tb, n=8):
        lines = tb.strip().splitlines()
        return "\n".join(lines[-n:]) if lines else tb

    def _tk_err(self, exc, val, tb):
        tb_text = "".join(traceback.format_exception(exc, val, tb))
        record_error(tb_text)
        try:
            from tkinter import messagebox
            ok = messagebox.askyesno(
                "程序出错了",
                "运行时出现一个错误：\n%s\n\n要发邮件给作者帮忙修吗？\n"
                "（不会自动发送，要你亲自点发送）" % _first_lines(tb_text))
            if ok:
                send_via_mailto("", crash=tb_text)
        except Exception:
            pass

    try:
        import tkinter as tk
        tk.Tk.report_callback_exception = _tk_err
    except Exception:
        pass

    def _hook(exc_type, exc_value, exc_tb):
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        record_error(tb_text)
        try:
            import tkinter as tk
            from tkinter import messagebox
            if getattr(tk, "_default_root", None) is not None:
                ok = messagebox.askyesno(
                    "程序出错了",
                    "运行时出现一个错误，要发邮件给作者帮忙修吗？\n"
                    "（不会自动发送，要你亲自点发送）")
                if ok:
                    send_via_mailto("", crash=tb_text)
        except Exception:
            pass

    sys.excepthook = _hook
