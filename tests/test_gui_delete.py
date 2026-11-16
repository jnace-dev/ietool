# -*- coding: utf-8 -*-
"""回归自测：真正驱动图形界面的“删除快捷方式”按钮。

走的是 GUI 里那条完整链路：
  界面参数 -> 检测已存在的快捷方式 -> 弹窗确认 -> 执行删除 -> 结果提示
"""
import os
import shutil
import sys
import tempfile
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import messagebox

sys.path.insert(0, r"D:\ai\ie")
import ie_launcher as L

ok = True
dialogs = []


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(("  PASS " if cond else "  FAIL ") + label + (" | " + str(detail) if detail else ""))


TARGET = r"C:\Program Files\Internet Explorer\iexplore.exe"
ARGS = r"https://www.baidu.com/#ie= -Embedding"
WORKDIR = r"C:\Program Files\Internet Explorer"
ICON = TARGET + ",0"

TMP = tempfile.mkdtemp(prefix="ielnk_guidel_")
SHORTCUT = os.path.join(TMP, "我的 IE.lnk")
L._create_shortcut_com(SHORTCUT, TARGET, ARGS, WORKDIR, ICON, "界面删除测试")
print("已准备: %s" % SHORTCUT)


def walk(widget, out):
    out.append(widget)
    for child in widget.winfo_children():
        walk(child, out)
    return out


def fake_mainloop(self):
    widgets = walk(self, [])
    entries = [w for w in widgets if isinstance(w, tk.Entry)]
    radios = [w for w in widgets if isinstance(w, ttk.Radiobutton)]
    buttons = [w for w in widgets if isinstance(w, ttk.Button)]

    # 填界面参数：名称 + 自定义目录
    entries[2].delete(0, "end")
    entries[2].insert(0, "我的 IE")
    entries[4].delete(0, "end")
    entries[4].insert(0, TMP)
    [r for r in radios if r.cget("value") == "custom"][0].invoke()

    delete_btn = [b for b in buttons if "删除" in str(b.cget("text"))][0]

    print("\n[1] 未确认时不应删除")
    _answers["value"] = False
    delete_btn.invoke()
    check("取消确认后文件仍在", os.path.isfile(SHORTCUT))
    check("弹出了确认框", any("确认删除" in d for d in dialogs), dialogs[-1:])

    print("\n[2] 确认后执行删除")
    dialogs.clear()
    _answers["value"] = True
    delete_btn.invoke()
    check("文件已删除", not os.path.isfile(SHORTCUT))
    check("结果提示里写了删除结果",
          any("删除结果" in d and "已删除 1 个" in d for d in dialogs), dialogs[-1:])

    print("\n[3] 已经不存在时给出明确提示")
    dialogs.clear()
    delete_btn.invoke()
    check("提示“没有找到”",
          any("没有找到" in d for d in dialogs), dialogs[-1:])
    check("不会误报成别的错误",
          not any("删除失败" in d for d in dialogs), dialogs[-1:])

    raise SystemExit(0)


# 记录所有弹窗内容
_showinfo = messagebox.showinfo
_showwarning = messagebox.showwarning
_showerror = messagebox.showerror


def record(kind):
    def inner(title="", message="", **kw):
        dialogs.append("%s | %s | %s" % (kind, title, message))
    return inner


messagebox.showinfo = record("info")
messagebox.showwarning = record("warn")
messagebox.showerror = record("error")

# 确认框也要记录（并按用例决定返回 True/False）
_answers = {"value": True}


def fake_askyesno(title="", message="", **kw):
    dialogs.append("ask | %s | %s" % (title, message))
    return _answers["value"]


messagebox.askyesno = fake_askyesno

tk.Tk.mainloop = fake_mainloop
try:
    L.build_gui()
except SystemExit:
    pass
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
