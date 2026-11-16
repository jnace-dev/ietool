# -*- coding: utf-8 -*-
"""GUI 联动自测：界面参数变化时，预览与单选框是否同步（不真正进入事件循环）。"""
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


def walk(widget, out):
    out.append(widget)
    for child in widget.winfo_children():
        walk(child, out)
    return out


def fake_mainloop(self):
    widgets = walk(self, [])
    entries = [w for w in widgets if isinstance(w, tk.Entry)]
    labels = [w for w in widgets if isinstance(w, (tk.Label, ttk.Label))]
    radios = [w for w in widgets if isinstance(w, ttk.Radiobutton)]

    print("  Entry 数量: %d" % len(entries))
    custom_entry = entries[4]
    radio_custom = [r for r in radios if r.cget("value") == "custom"][0]
    preview = [l for l in labels if str(l.cget("textvariable") or "")][0]

    def ptext():
        return str(self.tk.globalgetvar(preview.cget("textvariable")))

    print("  预览:", ptext().replace("\n", " | "))
    check("预览含默认命令",
          'iexplore.exe" https://www.baidu.com/#ie= -Embedding' in ptext())
    check("默认名称是 IE 缩写", L.DEFAULT_NAME == "IE", L.DEFAULT_NAME)
    check("预览含真实桌面保存路径",
          os.path.join(L.desktop_dir(), L.DEFAULT_NAME + ".lnk") in ptext(),
          ptext().replace("\n", " | "))
    check("名称沿用界面填的内容（不做自动改名）",
          "已改名为" not in ptext()
          and os.path.join(L.desktop_dir(), L.DEFAULT_NAME + ".lnk") in ptext(),
          ptext().replace("\n", " | "))
    check("默认名 IE 不含 Internet Explorer，不会触发系统刷新",
          "旧版 IE" not in ptext(), ptext().replace("\n", " | "))

    entries[2].delete(0, "end")
    entries[2].insert(0, "Internet Explorer")
    check("名字含 Internet Explorer 时给出被刷新的提醒",
          "旧版 IE" in ptext(), ptext().replace("\n", " | "))
    entries[2].delete(0, "end")
    entries[2].insert(0, "我的 IE")

    entries[1].delete(0, "end")
    entries[1].insert(0, "http://oa.example.com")
    check("改启动参数后预览同步", "http://oa.example.com" in ptext(),
          ptext().replace("\n", " | "))

    entries[2].delete(0, "end")
    entries[2].insert(0, "我的 IE")
    check("改名称后预览同步", "我的 IE.lnk" in ptext(),
          ptext().replace("\n", " | "))

    custom_entry.delete(0, "end")
    custom_entry.insert(0, r"D:\ai\ie\_out")
    check("填自定义目录后预览同步", r"_out\我的 IE.lnk" in ptext(),
          ptext().replace("\n", " | "))
    check("自定义目录自动切到对应单选框", radio_custom.instate(["selected"]),
          radio_custom.cget("value"))

    custom_entry.delete(0, "end")
    check("清空自定义目录后明确提示（不静默回退）",
          "自定义目录" in ptext() and "还没填写目录" in ptext(),
          ptext().replace("\n", " | "))
    check("单选框仍停在自定义目录", radio_custom.instate(["selected"]))

    raise SystemExit(0)


tk.Tk.mainloop = fake_mainloop
try:
    L.build_gui()
except SystemExit:
    pass

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
