# -*- coding: utf-8 -*-
"""验证：开始菜单里不同名字的快捷方式，短时间内是否被系统组件改写。"""
import os
import re
import sys
import time

sys.path.insert(0, r"D:\ai\ie")
import ie_launcher as L

sm = L.start_menu_dir()
TARGET = r"C:\Program Files\Internet Explorer\iexplore.exe"
ARGS = "https://www.baidu.com/#ie= -Embedding"


def content(path):
    data = open(path, "rb").read()
    uni = data.decode("utf-16-le", "ignore")
    has_baidu = "baidu" in uni
    m = re.search(r"[A-Za-z]:\\[^\x00]{0,80}?iexplore\.exe", uni)
    return ("含baidu=%s | %s" % (has_baidu, m.group(0) if m else "?"))


cases = ["IE 快捷方式.lnk", "IE启动器.lnk", "Internet Explorer (IE).lnk"]
for name in cases:
    path = os.path.join(sm, name)
    try:
        L._create_shortcut_com(path, TARGET, ARGS,
                               r"C:\Program Files\Internet Explorer",
                               TARGET + ",0", "时间序列验证")
    except Exception as exc:
        print("%-28s 创建失败: %s" % (name, L._short_error(exc)))
        continue
    print("%-28s 刚创建: %s" % (name, content(path)))

print("\n等待 45 秒后复查……")
time.sleep(45)
for name in cases:
    path = os.path.join(sm, name)
    if os.path.isfile(path):
        print("%-28s 45秒后: %s" % (name, content(path)))
    else:
        print("%-28s 45秒后: 文件已消失" % name)
