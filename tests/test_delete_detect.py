# -*- coding: utf-8 -*-
"""回归自测：删除快捷方式时的“检测 → 存在就删”。"""
import os
import shutil
import sys
import tempfile

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

print("[1] 按界面参数检测已存在的快捷方式")
tmp = tempfile.mkdtemp(prefix="ielnk_del_")
try:
    path = os.path.join(tmp, "我的 IE.lnk")
    L._create_shortcut_com(path, TARGET, ARGS, WORKDIR, ICON, "删除测试")

    settings = {"target": TARGET, "args": ARGS, "name": "我的 IE",
                "desc": "", "location": "custom", "custom_dir": tmp,
                "icon_mode": "system"}
    existing, expected = L.find_shortcuts_for_delete(settings)
    check("检测到已存在的快捷方式", path in existing, existing)
    check("检测范围里包含预期路径", path in expected, expected[:3])

    print("\n[2] 删除：存在就删")
    success, msg = L.delete_shortcut(path)
    check("删除成功", success and not os.path.exists(path), msg)
    check("提示文字含路径", path in msg, msg)

    print("\n[3] 删除：不存在就不删")
    success, msg = L.delete_shortcut(path)
    check("返回失败", not success, msg)
    check("提示说清是不存在", "不存在" in msg, msg)

    success, msg = L.delete_shortcut("")
    check("空路径也给出明确提示", not success and "没有指定" in msg, msg)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[4] 安全名候选也能被检测到（创建时改过名的情形）")
sm = L.start_menu_dir()
probe = os.path.join(sm, "IE 快捷方式.lnk")
existed_before = os.path.isfile(probe)
try:
    if not existed_before:
        L._create_shortcut_com(probe, TARGET, ARGS, WORKDIR, ICON, "检测测试")
    settings = {"target": TARGET, "args": ARGS, "name": "Internet Explorer",
                "desc": "", "location": "startmenu", "custom_dir": "",
                "icon_mode": "system"}
    existing, expected = L.find_shortcuts_for_delete(settings)
    check("界面填 Internet Explorer 也能定位到实际落盘的安全名",
          probe in existing, existing)
    targets, skipped, expected = L.detect_for_delete(settings)
    check("detect_for_delete 能区分可删与系统自带",
          probe in targets and probe not in skipped, (targets, skipped))
finally:
    if not existed_before and os.path.isfile(probe):
        L.delete_shortcut(probe)

print("\n[5] 系统自带的 IE 条目不自动删")
tmp2 = tempfile.mkdtemp(prefix="ielnk_sys_")
try:
    # 系统自带条目的特征：指向 iexplore.exe 且没有参数
    syslnk = os.path.join(tmp2, "Internet Explorer.lnk")
    L._create_shortcut_com(syslnk, TARGET, "", WORKDIR, ICON, "")
    check("能被识别为系统自带条目", L._is_system_ie_shortcut(syslnk))
    settings = {"target": TARGET, "args": ARGS, "name": "Internet Explorer",
                "desc": "", "location": "custom", "custom_dir": tmp2,
                "icon_mode": "system"}
    targets, skipped, expected = L.detect_for_delete(settings)
    check("系统自带条目进跳过列表", syslnk in skipped, skipped)
    check("系统自带条目不进可删列表", syslnk not in targets, targets)

    # 但如果是本次会话刚创建出来的，就照删
    targets2, skipped2, _ = L.detect_for_delete(settings, [syslnk])
    check("本次会话创建的照常可删", syslnk in targets2 and syslnk not in skipped2,
          (targets2, skipped2))
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

print("\n[6] delete_paths 逐个删除并复核")
tmp3 = tempfile.mkdtemp(prefix="ielnk_del3_")
try:
    files = []
    for name in ("甲.lnk", "乙.lnk"):
        p = os.path.join(tmp3, name)
        L._create_shortcut_com(p, TARGET, ARGS, WORKDIR, ICON, "批量")
        files.append(p)
    removed, failed = L.delete_paths(files)
    check("两个都删掉", len(removed) == 2 and not failed, (removed, failed))
    check("文件确实不在了",
          not any(os.path.isfile(p) for p in files),
          [p for p in files if os.path.isfile(p)])
    removed2, failed2 = L.delete_paths(files)
    check("再删一次全部失败并给出原因",
          not removed2 and len(failed2) == 2
          and all("不存在" in m for _p, m in failed2), failed2)
finally:
    shutil.rmtree(tmp3, ignore_errors=True)

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
