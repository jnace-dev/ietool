# -*- coding: utf-8 -*-
"""新逻辑的功能自测：参数是否真的全部落到快捷方式里。"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ie_launcher as L

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(("  PASS " if cond else "  FAIL ") + label + (" | " + str(detail) if detail else ""))


print("[1] 命令行拆分（带引号参数）")
argv = L.split_command_line('"C:\\Program Files\\Internet Explorer\\iexplore.exe" https://www.baidu.com/#ie= -Embedding')
print("     ", argv)
check("目标程序作为 argv[0]", argv[0] == r"C:\Program Files\Internet Explorer\iexplore.exe")
check("URL 与 -Embedding 分开", argv[1:] == ["https://www.baidu.com/#ie=", "-Embedding"], argv[1:])
argv2 = L.split_command_line('"C:\\x\\a.exe" --title "我的 页面"')
check("引号内空格不被切开", argv2[1:] == ["--title", "我的 页面"], argv2[1:])

print("[2] 自定义目录解析")
check("自定义目录为空时返回空串", L.resolve_target_folder("custom", "") == "")
check("自定义目录生效", L.resolve_target_folder("custom", r"D:\\tmp\\x") == r"D:\tmp\x")
check("桌面位置解析", L.resolve_target_folder("desktop") == L.desktop_dir())

print("[3] 完整参数写入 + 逐项核对")
tmp = tempfile.mkdtemp(prefix="ietest_")
settings = {
    "target": r"C:\Program Files\Internet Explorer\iexplore.exe",
    "args": 'https://www.baidu.com/#ie= -Embedding --title "我的 页面"',
    "name": "测试 快捷方式",
    "desc": "单元测试描述",
    "location": "custom",
    "custom_dir": tmp,
    "icon_mode": "bundled",
}
res = L.create_shortcut(settings)
print("     ", res["path"])
checks = L.verify_shortcut(res["path"], res)
for c in checks:
    print(("  PASS " if c["ok"] else "  FAIL ") + c["field"] +
          " | 实际=%r 期望=%r" % (c["actual"], c["expected"]))
    ok = ok and c["ok"]

print("[4] 内置图标模式")
check("bundled 图标指向释放出来的 ico", res["icon"].lower().endswith(".ico"), res["icon"])
check("ico 文件确实存在", os.path.isfile(res["icon"]))

print("[5] 参数原样保留（引号不被吞）")
actual = L.read_shortcut(res["path"])
check("Arguments 与界面一致", actual.get("arguments") == settings["args"], actual.get("arguments"))
check("Description 与界面一致", actual.get("description") == settings["desc"], actual.get("description"))

print("[6] 名称非法字符处理")
res2 = L.create_shortcut(dict(settings, name="a/b:c*d?", location="custom", custom_dir=tmp))
check("非法字符被替换", os.path.basename(res2["path"]) == "a_b_c_d_.lnk", os.path.basename(res2["path"]))

print("[7] 缺目录时报错")
try:
    L.create_shortcut(dict(settings, location="custom", custom_dir=""))
    check("空自定义目录应报错", False)
except ValueError as exc:
    check("空自定义目录应报错", True, exc)

shutil.rmtree(tmp, ignore_errors=True)
print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
