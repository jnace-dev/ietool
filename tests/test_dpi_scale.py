# -*- coding: utf-8 -*-
"""回归自测：DPI 缩放适配。

覆盖：
  · 进程 DPI 感知模式（必须在建窗口之前声明）；
  · 显示器 DPI 与工作区读取；
  · 像素量换算（内边距 / 外边距 / 换行宽度）；
  · 字体随系数缩放——Tk 的 `tk scaling` 对**已建好的**控件无效，
    必须自己把字号换算成像素重设，否则自适应缩放时字不会跟着缩；
  · 窗口放不下时自动缩到工作区内；
  · 真实界面在当前显示器上完整落在工作区内
    （真实界面单独跑在子进程里：初始化顺序和真实入口完全一致，
     免得本测试前面建过的窗口影响 Tk 默认字体的取值）。
"""
import json
import os
import subprocess
import sys
import tempfile
import tkinter as tk
import tkinter.ttk as ttk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ie_launcher as L

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(("  PASS " if cond else "  FAIL ") + label +
          (" | " + str(detail) if detail else ""))


print("[1] 进程 DPI 感知")
mode = L.enable_dpi_awareness()
check("进程已声明 DPI 感知", bool(mode) and "未启用" not in mode, mode)
check("重复调用是幂等的", L.enable_dpi_awareness() == mode, mode)

print("\n[2] 显示器 DPI 与工作区")
root = tk.Tk()
root.withdraw()
dpi = L.window_dpi(root.winfo_id())
check("窗口 DPI 在合理范围内", 48 <= dpi <= 480, dpi)
left, top, right, bottom = L.work_area(root.winfo_id())
check("工作区是一个有效矩形", right > left and bottom > top,
      (left, top, right, bottom))
check("工作区不小于 640x400", (right - left) >= 640 and (bottom - top) >= 400,
      (right - left, bottom - top))

print("\n[3] 像素量换算")
check("整数按系数换算", L._scale_len(10, 1.5) == 15, L._scale_len(10, 1.5))
check("二元组按系数换算", L._scale_len((2, 4), 2.0) == (4, 8),
      L._scale_len((2, 4), 2.0))
check("解析 '8' 得到整数", L._parse_len("8") == 8, L._parse_len("8"))
check("解析 '8 4' 得到二元组", L._parse_len("8 4") == (8, 4),
      L._parse_len("8 4"))
check("解析 (8,) 得到整数", L._parse_len((8,)) == 8, L._parse_len((8,)))
check("换算结果不会变成负数", L._scale_len(4, 0.1) == 0, L._scale_len(4, 0.1))

print("\n[4] 界面像素量随系数缩放")
frame = ttk.LabelFrame(root, text="  测试  ", padding=8)
frame.grid(row=0, column=0, padx=10, pady=(4, 6))
label = tk.Label(root, text="预览文字", font=("Consolas", 9), wraplength=720)
label.grid(row=1, column=0)

layout = L.DpiLayout(root)
layout.capture(1.0)
layout.apply(2.0)
root.update_idletasks()
check("LabelFrame 内边距放大一倍", frame.cget("padding")[0] == 16,
      frame.cget("padding"))
check("grid 外边距放大一倍", frame.grid_info()["padx"] == 20,
      frame.grid_info()["padx"])
check("grid 外边距（纵向二元组）放大一倍",
      frame.grid_info()["pady"] == (8, 12), frame.grid_info()["pady"])
check("换行宽度放大一倍", label.cget("wraplength") == 1440,
      label.cget("wraplength"))
layout.apply(1.0)
root.update_idletasks()
check("缩回设计值", frame.cget("padding")[0] == 8
      and frame.grid_info()["padx"] == 10
      and label.cget("wraplength") == 720,
      (frame.cget("padding"), frame.grid_info()["padx"]))

print("\n[5] 字体随系数缩放")
scaling = float(root.tk.call("tk", "scaling"))
design_px = int(round(9 * scaling))
text = tk.Text(root, height=6, width=40, font=("Consolas", 9))
text.grid(row=2, column=0)

layout2 = L.DpiLayout(root)
layout2.capture(1.0)
layout2.apply(2.0)
root.update_idletasks()
family, size = L._font_spec_info(text.cget("font")) or ("", 0)
check("自定义字体被换成像素尺寸并放大一倍",
      size == -design_px * 2, (family, size, design_px))
default_size = int(root.tk.call("font", "configure", "TkDefaultFont", "-size"))
check("Tk 默认字体（ttk 控件用）也被缩放", default_size < 0, default_size)

print("\n[6] 放不下时自动缩到工作区内")
big = tk.Text(root, height=40, width=110, font=("Consolas", 9))
big.grid(row=3, column=0)
layout3 = L.DpiLayout(root)
layout3.capture(1.0)
layout3.add_elastic(big, 40, 4)
real_work_area = L.work_area
try:
    L.work_area = lambda h=None: (0, 0, 700, 500)
    factor, win_w, win_h = L.fit_to_work_area(root, root, layout3, 1.25, None)
finally:
    L.work_area = real_work_area
check("缩放系数被调小", factor < 1.25, factor)
check("窗口宽度落在工作区内", win_w <= 700, win_w)
check("窗口高度落在工作区内", win_h <= 500, win_h)

root.destroy()

print("\n[7] 真实界面在当前显示器上（单独进程跑）")
CHILD = r'''
import json, sys, tkinter as tk
sys.path.insert(0, %r)
import ie_launcher as L

# 可选参数：伪造工作区与 DPI（用来模拟别的屏幕）
if len(sys.argv) > 2:
    _area = tuple(int(x) for x in sys.argv[1].split(","))
    _dpi = int(sys.argv[2])
    L.work_area = lambda h=None: _area
    L.window_dpi = lambda h=None: _dpi

def fake_mainloop(self):
    self.update_idletasks()
    area = L.work_area(self.winfo_id())
    outer = self.winfo_children()[0]
    canvas = [c for c in outer.winfo_children() if c.winfo_class() == "Canvas"][0]
    inner = [c for c in canvas.winfo_children() if c.winfo_class() == "TFrame"][0]
    scrolls = [c for c in outer.winfo_children() if c.winfo_class() == "TScrollbar"]
    vscroll = any(str(s.cget("orient")) == "vertical" and s.winfo_manager()
                  for s in scrolls)
    hscroll = any(str(s.cget("orient")) == "horizontal" and s.winfo_manager()
                  for s in scrolls)
    payload = {
        "w": self.winfo_reqwidth(), "h": self.winfo_reqheight(),
        "aw": area[2] - area[0], "ah": area[3] - area[1],
        "cw": inner.winfo_reqwidth(), "ch": inner.winfo_reqheight(),
        "x": self.winfo_x(), "y": self.winfo_y(),
        "scale": self.tk.call("tk", "scaling"),
        "dpi": L.window_dpi(self.winfo_id()),
        "mode": L.enable_dpi_awareness(),
        "vscroll": vscroll, "hscroll": hscroll,
    }
    print("__METRICS__" + json.dumps(payload))
    raise SystemExit(0)

tk.Tk.mainloop = fake_mainloop
try:
    L.build_gui()
except SystemExit:
    pass
''' % ROOT

script = os.path.join(tempfile.gettempdir(), "ie_launcher_dpi_child.py")
with open(script, "w", encoding="utf-8") as fp:
    fp.write(CHILD)


def run_child(area=None, dpi=None):
    """跑一次真实界面，返回它报上来的尺寸信息。"""
    args = [sys.executable, script]
    if area:
        args += ["%d,%d,%d,%d" % area, str(dpi)]
    proc = subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    for line in (proc.stdout or "").splitlines():
        if line.startswith("__METRICS__"):
            return json.loads(line[len("__METRICS__"):])
    return {}


metrics = run_child()
check("真实界面跑起来了", bool(metrics), "子进程没有输出尺寸信息")
if metrics:
    check("界面宽度落在工作区内", metrics["w"] <= metrics["aw"],
          (metrics["w"], metrics["aw"]))
    check("界面高度落在工作区内", metrics["h"] <= metrics["ah"],
          (metrics["h"], metrics["ah"]))
    check("按显示器的真实 DPI 排版",
          abs(metrics["scale"] - metrics["dpi"] / 72.0) < 0.2,
          (metrics["scale"], metrics["dpi"]))
    check("窗口摆在工作区之内",
          metrics["x"] >= 0 and metrics["y"] >= 0,
          (metrics["x"], metrics["y"]))

print("\n[8] 低分辨率屏幕适配")
CASES = [
    ("1366x768 @125%", (0, 0, 1366, 728), 120),
    ("1366x768 @100%", (0, 0, 1366, 728), 96),
    ("1024x768 @100%", (0, 0, 1024, 728), 96),
    ("800x600  @100%", (0, 0, 800, 560), 96),
    ("640x480  @100%", (0, 0, 640, 440), 96),
]
for label, area, dpi in CASES:
    m = run_child(area, dpi)
    if not m:
        check("%s 跑起来了" % label, False, "子进程没有输出")
        continue
    aw, ah = area[2] - area[0], area[3] - area[1]
    check("%s 窗口完整落在工作区内" % label,
          m["w"] <= aw and m["h"] <= ah, (m["w"], m["h"], aw, ah))
    # 内容要么完整可见，要么能通过滚动条访问
    visible = (m["cw"] <= m["w"] and m["ch"] <= m["h"])
    check("%s 内容可完整访问（可见或可滚动）" % label,
          visible or m["vscroll"] or m["hscroll"],
          (m["cw"], m["ch"], m["vscroll"], m["hscroll"]))

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
