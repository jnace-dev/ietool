# -*- coding: utf-8 -*-
"""回归自测：所有用户桌面 + 系统改写规则 + 落盘内容真的能被系统读回。

覆盖两个真实踩过的坑：
  1) 之前“二进制直写”生成的 .lnk 是坏的——Windows 读出来目标为空，图标在、点不动；
  2) 系统会把名字含 “Internet Explorer” 的快捷方式改写成旧版 IE
     （开始菜单、**所有用户桌面**都会），改写只要 1.7 秒。
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, r"D:\ai\ie")
import ie_launcher as L

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(("  PASS " if cond else "  FAIL ") + label + (" | " + str(detail) if detail else ""))


TARGET = r"C:\Program Files\Internet Explorer\iexplore.exe"
ARGS = r"https://www.baidu.com/#ie= -Embedding"
WORKDIR = r"C:\Program Files\Internet Explorer"
ICON = TARGET + ",0"
DESC = "使用 Internet Explorer 打开网页"

print("[1] 系统改写范围（名字含 Internet Explorer 会被改写的位置）")
for label, folder in (("当前用户桌面", L.desktop_dir()),
                      ("所有用户桌面", L.public_desktop_dir()),
                      ("当前用户开始菜单", L.start_menu_dir()),
                      ("所有用户开始菜单", L.all_users_start_menu_dir())):
    check("%s 属于改写范围" % label, L._in_rewrite_scope(folder), folder)
check("自定义目录不属于改写范围",
      not L._in_rewrite_scope(r"D:\ai\ie\_out"))

print("\n[2] 名称策略：始终沿用界面填的名字，不做自动改名")
for label, folder in (("当前用户桌面", L.desktop_dir()),
                      ("所有用户桌面", L.public_desktop_dir()),
                      ("当前用户开始菜单", L.start_menu_dir())):
    cands = L._safe_name_candidates(folder, "Internet Explorer")
    first = os.path.basename(cands[0])
    check("%s 首选名字就是界面填的" % label,
          first == "Internet Explorer.lnk", first)
    check("%s 后面备选仍可用" % label, len(cands) > 1, cands[1:3])
custom = L._safe_name_candidates(r"D:\ai\ie\_out", "Internet Explorer")
check("自定义目录沿用界面填的名字",
      os.path.basename(custom[0]) == "Internet Explorer.lnk",
      os.path.basename(custom[0]))

check("默认名称是 IE 缩写", L.DEFAULT_NAME == "IE", L.DEFAULT_NAME)
safe = L.safe_name_for(L.start_menu_dir(), "Internet Explorer")
check("备用的安全名不含 Internet Explorer",
      "internet explorer" not in safe.lower(), safe)
check("备用的安全名是纯名称（不带 .lnk）",
      not safe.lower().endswith(".lnk"), safe)

print("\n[3] 落盘内容必须能被 Windows 自己读回（点得动）")
tmp = tempfile.mkdtemp(prefix="ielnk_test_")
try:
    dest = os.path.join(tmp, "IE 快捷方式.lnk")
    # 模拟“目标目录禁止 COM 新建快捷方式”的场景：走临时生成 + 字节写入这条路
    actual, note = L._create_shortcut_via_temp(dest, TARGET, ARGS, WORKDIR,
                                                ICON, DESC, [dest])
    check("文件已生成", os.path.isfile(actual), actual)

    # 系统默认属性：外部时间戳 = 实际创建时刻；属性 = ARCHIVE；
    # 内嵌 ShellLink 头里的时间戳是“目标程序”的时间戳（外壳写入，不应改动）
    import struct
    with open(actual, "rb") as f:
        head = f.read(0x4C)
    inner_write = struct.unpack_from("<Q", head, 44)[0]
    inner_unix = (inner_write - 116444736000000000) / 10000000.0
    st = os.stat(actual)
    check("外部时间戳 = 实际创建时刻（系统默认）",
          abs(st.st_mtime - st.st_ctime) < 2.0
          and abs(st.st_mtime - time.time()) < 120,
          (st.st_mtime, st.st_ctime, time.time()))
    check("内嵌时间戳仍是目标程序的时间戳（外壳写入，不改）",
          inner_unix < time.time() - 86400, inner_unix)
    attrs = getattr(st, "st_file_attributes", 0)
    check("文件属性含 ARCHIVE（0x20）", bool(attrs & 0x20), hex(attrs))

    import win32com.client
    shell = win32com.client.Dispatch("WScript.Shell")
    link = shell.CreateShortcut(actual)
    check("系统读回目标程序", link.TargetPath == TARGET, repr(link.TargetPath))
    check("系统读回启动参数", link.Arguments == ARGS, repr(link.Arguments))
    check("系统读回工作目录", link.WorkingDirectory == WORKDIR,
          repr(link.WorkingDirectory))
    check("系统读回描述", link.Description == DESC, repr(link.Description))

    info = L.read_shortcut(actual)
    check("工具自身核对通过", L._shortcut_matches(actual, TARGET, ARGS), info)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[4] 系统改写检测")
tmp2 = tempfile.mkdtemp(prefix="ielnk_test2_")
try:
    dest = os.path.join(tmp2, "IE 快捷方式.lnk")
    L._create_shortcut_com(dest, TARGET, ARGS, WORKDIR, ICON, DESC)
    time.sleep(2.5)
    check("安全名不会被系统改写",
          L._shortcut_matches(dest, TARGET, ARGS),
          L.read_shortcut(dest))
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
