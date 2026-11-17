# -*- coding: utf-8 -*-
"""回归自测：删除时不能误伤同目录里“别的名字”的快捷方式。

起因（2026-11-16 实测）：早期版本的候选名里硬塞了 “IE 快捷方式.lnk” / “IE.lnk”，
于是 `--delete --name ZZZ不存在` 反而把同目录的 IE.lnk 删掉了。
"""
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


TARGET = r"C:\Program Files\Internet Explorer\iexplore.exe"
ARGS = r"https://www.baidu.com/#ie= -Embedding"
WORKDIR = r"C:\Program Files\Internet Explorer"
ICON = TARGET + ",0"

print("[1] 删除只认界面上填的名字")
tmp = tempfile.mkdtemp(prefix="ielnk_scope_")
try:
    keep_plain = os.path.join(tmp, "IE.lnk")
    keep_cn = os.path.join(tmp, "IE 快捷方式.lnk")
    mine = os.path.join(tmp, "我的页面.lnk")
    for p in (keep_plain, keep_cn, mine):
        L._create_shortcut_com(p, TARGET, ARGS, WORKDIR, ICON, "范围测试")

    settings = {"target": TARGET, "args": ARGS, "name": "我的页面",
                "desc": "", "location": "custom", "custom_dir": tmp,
                "icon_mode": "system"}

    existing, expected = L.find_shortcuts_for_delete(settings)
    check("只检测到同名的那一个", existing == [mine], existing)
    check("检测范围里没有 IE.lnk", keep_plain not in expected, expected)
    check("检测范围里没有 IE 快捷方式.lnk", keep_cn not in expected, expected)

    targets, skipped, _ = L.detect_for_delete(settings)
    removed, failed = L.delete_paths(targets)
    check("只删掉了同名那个", len(removed) == 1 and not failed, (removed, failed))
    check("IE.lnk 还在", os.path.isfile(keep_plain))
    check("IE 快捷方式.lnk 还在", os.path.isfile(keep_cn))
    check("我的页面.lnk 确实没了", not os.path.isfile(mine))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("\n[2] 名字对不上时，命令行删除什么都不删、退出码 1")
tmp2 = tempfile.mkdtemp(prefix="ielnk_scope2_")
try:
    keep = os.path.join(tmp2, "IE.lnk")
    L._create_shortcut_com(keep, TARGET, ARGS, WORKDIR, ICON, "范围测试")
    settings = {"target": TARGET, "args": ARGS, "name": "ZZZ不存在",
                "desc": "", "location": "custom", "custom_dir": tmp2,
                "icon_mode": "system"}
    code = L.run_cli_delete(settings, True, None)
    check("退出码为 1（没找到）", code == 1, code)
    check("IE.lnk 没有被删", os.path.isfile(keep))
finally:
    shutil.rmtree(tmp2, ignore_errors=True)

print("\n[3] 名字含 Internet Explorer 时，仍要能定位到 IE 类安全名")
tmp3 = tempfile.mkdtemp(prefix="ielnk_scope3_")
try:
    safe = os.path.join(tmp3, "IE 快捷方式.lnk")
    L._create_shortcut_com(safe, TARGET, ARGS, WORKDIR, ICON, "安全名")
    settings = {"target": TARGET, "args": ARGS, "name": "Internet Explorer",
                "desc": "", "location": "custom", "custom_dir": tmp3,
                "icon_mode": "system"}
    existing, _expected = L.find_shortcuts_for_delete(settings)
    check("能定位到 IE 快捷方式.lnk", safe in existing, existing)
finally:
    shutil.rmtree(tmp3, ignore_errors=True)

print("\n[4] 候选名全部由界面名字派生")
cands = [os.path.basename(c) for c in
         L._safe_name_candidates(r"D:\\tmp", "我的页面")]
check("候选只有同名的三个变体",
      cands == ["我的页面.lnk", "我的页面 (IE).lnk", "我的页面 快捷方式.lnk"], cands)
cands_ie = [os.path.basename(c) for c in L._safe_name_candidates(r"D:\\tmp", "IE")]
check("名字就是 IE 时候选也是 IE 系列",
      cands_ie == ["IE.lnk", "IE (IE).lnk", "IE 快捷方式.lnk"], cands_ie)

print("\nRESULT:", "ALL PASS" if ok else "HAS FAILURE")
sys.exit(0 if ok else 1)
