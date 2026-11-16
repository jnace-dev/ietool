# -*- coding: utf-8 -*-
"""回归自测：IE 位数互切按钮——文案跟着目标路径走，点了就切到另一种位数。"""
import os
import sys
import tkinter as tk
import tkinter.ttk as ttk

sys.path.insert(0, r"D:\ai\ie")
import ie_launcher as L

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(("  PASS " if cond else "  FAIL ") + label + (" | " + str(detail) if detail else ""))


print("[1] 位数识别")
check("64 位 Program Files", L.ie_bitness(L.IE_64) == "64", L.ie_bitness(L.IE_64))
check("32 位 Program Files (x86)", L.ie_bitness(L.IE_32) == "32", L.ie_bitness(L.IE_32))
check("SysWOW64 算 32 位",
      L.ie_bitness(r"C:\Windows\SysWOW64\iexplore.exe") == "32",
      L.ie_bitness(r"C:\Windows\SysWOW64\iexplore.exe"))
check("System32 算 64 位",
      L.ie_bitness(r"C:\Windows\System32\iexplore.exe") == "64",
      L.ie_bitness(r"C:\Windows\System32\iexplore.exe"))
check("带引号也能认出来", L.ie_bitness('"%s"' % L.IE_64) == "64")
check("不是 IE 返回空", L.ie_bitness(r"C:\Windows\notepad.exe") == "",
      L.ie_bitness(r"C:\Windows\notepad.exe"))
check("空路径返回空", L.ie_bitness("") == "")

print("\n[2] 切换目标")
check("当前 64 位 -> 切到 32 位",
      L.ie_switch_target(L.IE_64)[0] == L.IE_32, L.ie_switch_target(L.IE_64))
check("当前 32 位 -> 切到 64 位",
      L.ie_switch_target(L.IE_32)[0] == L.IE_64, L.ie_switch_target(L.IE_32))
check("标签跟着走",
      L.ie_switch_target(L.IE_64)[1] == "32 位"
      and L.ie_switch_target(L.IE_32)[1] == "64 位")

print("\n[3] 界面按钮文案与点击")


def walk(widget, out):
    out.append(widget)
    for child in widget.winfo_children():
        walk(child, out)
    return out


def fake_mainloop(self):
    widgets = walk(self, [])
    entries = [w for w in widgets if isinstance(w, tk.Entry)]
    buttons = [w for w in widgets if isinstance(w, ttk.Button)]
    target_entry = entries[0]
    switch_btn = [b for b in buttons if "位 IE" in str(b.cget("text"))
                  or "位数" in str(b.cget("text"))][0]

    print("      默认按钮文案: %r" % switch_btn.cget("text"))
    check("默认（64 位目标）显示“用 32 位 IE”",
          switch_btn.cget("text") == "用 32 位 IE", switch_btn.cget("text"))

    target_entry.delete(0, "end")
    target_entry.insert(0, L.IE_32)
    self.update()
    print("      32 位目标时文案: %r" % switch_btn.cget("text"))
    check("目标改成 32 位后显示“用 64 位 IE”",
          switch_btn.cget("text") == "用 64 位 IE", switch_btn.cget("text"))

    switch_btn.invoke()
    self.update()
    value = target_entry.get()
    print("      点击后目标: %s" % value)
    check("点击后目标切到 64 位", L.ie_bitness(value) == "64", value)
    check("点击后文案变回“用 32 位 IE”",
          switch_btn.cget("text") == "用 32 位 IE", switch_btn.cget("text"))

    switch_btn.invoke()
    self.update()
    value = target_entry.get()
    print("      再点一次目标: %s" % value)
    check("再点一次切回 32 位", L.ie_bitness(value) == "32", value)

    target_entry.delete(0, "end")
    target_entry.insert(0, r"C:\Windows\notepad.exe")
    self.update()
    check("不是 IE 时显示“切换 IE 位数”",
          switch_btn.cget("text") == "切换 IE 位数", switch_btn.cget("text"))

    raise SystemExit(0)


tk.Tk.mainloop = fake_mainloop
try:
    L.build_gui()
except SystemExit:
    pass

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
