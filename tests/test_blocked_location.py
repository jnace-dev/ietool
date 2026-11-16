# -*- coding: utf-8 -*-
"""桌面被安全软件拦截时，创建流程是否仍能成功并通过逐项核对。"""
import os
import sys

sys.path.insert(0, r"D:\ai\ie")
import ie_launcher as L

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(("  PASS " if cond else "  FAIL ") + label + (" | " + str(detail) if detail else ""))


print("[1] 桌面（安全软件禁止直接新建快捷方式）")
settings = {
    "target": r"C:\Program Files\Internet Explorer\iexplore.exe",
    "args": "https://www.baidu.com/#ie= -Embedding",
    "name": "Internet Explorer",
    "desc": "使用 Internet Explorer 打开网页",
    "location": "desktop",
    "custom_dir": "",
    "icon_mode": "system",
}
try:
    res = L.create_shortcut(settings)
except Exception as exc:
    # 桌面被安全软件全面拦截时，必须给出可操作的提示，而不是含糊报错
    text = str(exc)
    print("      桌面被拦截，报错信息如下：")
    check("报错里给出可操作建议", "建议" in text and "开始菜单" in text)
    check("报错里保留原始原因", "拒绝访问" in text or "拦截" in text)
    res = None

if res is not None:
    print("      写入方式:", res["method"])
    print("      路径    :", res["path"])
    check("快捷方式已生成", os.path.isfile(res["path"]))
    for note in res.get("notes") or []:
        print("      提示:", note)

    print("[2] 逐项核对")
    for c in L.verify_shortcut(res["path"], res):
        print(("  PASS " if c["ok"] else "  FAIL ") + c["field"] +
              " | 实际=%r 期望=%r" % (c["actual"], c["expected"]))
        ok = ok and c["ok"]

print("[3] 开始菜单（COM 直建路径）")
sm = L.start_menu_dir()
res2 = L.create_shortcut(dict(settings, location="startmenu", name="IE 测试项"))
print("      写入方式:", res2["method"], "|", res2["path"])
for c in L.verify_shortcut(res2["path"], res2):
    print(("  PASS " if c["ok"] else "  FAIL ") + c["field"])
    ok = ok and c["ok"]

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
