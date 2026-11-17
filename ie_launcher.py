# -*- coding: utf-8 -*-
"""
IE 快捷方式创建工具
==================

在 Windows 10 / Windows 11 上创建“启动 Internet Explorer”的桌面/开始菜单快捷方式。

默认生成的快捷方式等价于执行：

    "C:\\Program Files\\Internet Explorer\\iexplore.exe" https://www.baidu.com/#ie= -Embedding

两种用法：
  1) 图形界面：双击 ie_launcher.exe
  2) 命令行：  ie_launcher.exe --create [--location desktop|startmenu|allusers] [--name 名称] ...

打包：PyInstaller（见 ie_launcher.spec）
"""

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import threading
import time

# --------------------------------------------------------------------------------------
# 默认配置
# --------------------------------------------------------------------------------------

DEFAULT_TARGET = r"C:\Program Files\Internet Explorer\iexplore.exe"
DEFAULT_ARGS = r"https://www.baidu.com/#ie= -Embedding"
DEFAULT_NAME = "IE"
DEFAULT_DESC = "使用 Internet Explorer 打开网页"

# iexplore.exe 的常见安装位置（Win10 放在 Program Files，Win11 基本已移除）
IE_64 = r"C:\Program Files\Internet Explorer\iexplore.exe"
IE_32 = r"C:\Program Files (x86)\Internet Explorer\iexplore.exe"

IE_CANDIDATES = [
    IE_64,
    IE_32,
    os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "iexplore.exe"),
    os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "SysWOW64", "iexplore.exe"),
]

EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def ie_bitness(target):
    """判断目标是 64 位还是 32 位的 IE 启动器；不是 IE 则返回空字符串。

    64 位系统上 `C:\\Windows\\System32` 里是 64 位程序，`SysWOW64` 里是 32 位程序。
    """
    text = (target or "").strip().strip('"').replace("/", "\\").lower()
    if not text:
        return ""
    if not text.endswith("\\iexplore.exe"):
        return ""
    if "(x86)" in text or "\\syswow64\\" in text:
        return "32"
    if "\\system32\\" in text or text == IE_64.lower():
        return "64"
    if text == IE_32.lower():
        return "32"
    return ""


def ie_switch_target(target):
    """给出“切到另一种位数”应该用的路径。

    现在是 64 位 → 返回 32 位路径；现在是 32 位 → 返回 64 位路径；
    认不出来时按本机实际存在的那个来定。返回 (路径, 位数标签)。
    """
    bits = ie_bitness(target)
    if bits == "64":
        return IE_32, "32 位"
    if bits == "32":
        return IE_64, "64 位"
    if os.path.isfile(IE_32) and not os.path.isfile(IE_64):
        return IE_32, "32 位"
    return IE_64, "64 位"


def resource_path(name):
    """兼容 PyInstaller 打包后的资源路径。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


# --------------------------------------------------------------------------------------
# 路径与快捷方式
# --------------------------------------------------------------------------------------

def _shell_folder(value_name, common=False):
    r"""从注册表读取 shell 目录（能正确处理桌面/文档重定向）。

    HKCU\User Shell Folders 是资源管理器真正使用的值，SHGetKnownFolderPath
    在部分重定向环境下会返回 FILE_NOT_FOUND，所以这里以注册表为准。
    """
    try:
        import winreg
        root = winreg.HKEY_LOCAL_MACHINE if common else winreg.HKEY_CURRENT_USER
        for sub in (r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"):
            try:
                with winreg.OpenKey(root, sub) as key:
                    raw, _ = winreg.QueryValueEx(key, value_name)
                expanded = os.path.expandvars(raw)
                if expanded:
                    return expanded
            except OSError:
                continue
    except Exception:
        pass
    return ""


def desktop_dir():
    """当前用户桌面（正确处理 OneDrive / 目录重定向）。"""
    return (_shell_folder("Desktop") or
            os.path.join(os.path.expanduser("~"), "Desktop"))


def public_desktop_dir():
    """所有用户桌面。"""
    return (_shell_folder("Common Desktop", common=True) or
            os.path.join(os.environ.get("Public", r"C:\Users\Public"), "Desktop"))


def start_menu_dir():
    """当前用户开始菜单程序目录。"""
    return (_shell_folder("Programs") or
            os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows",
                         "Start Menu", "Programs"))


def all_users_start_menu_dir():
    """所有用户开始菜单程序目录。"""
    return (_shell_folder("Common Programs", common=True) or
            os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                         "Microsoft", "Windows", "Start Menu", "Programs"))


def resolve_location(location, custom_dir=""):
    """把位置关键字翻译成实际目录。"""
    if location == "desktop":
        return desktop_dir()
    if location == "publicdesktop":
        return public_desktop_dir()
    if location == "startmenu":
        return start_menu_dir()
    if location == "allusersstartmenu":
        return all_users_start_menu_dir()
    if location == "custom" and custom_dir:
        return custom_dir
    return desktop_dir()


def _needs_admin(location):
    """“所有用户”级别的位置需要管理员权限才能写入。"""
    return (location or "") in ("publicdesktop", "allusersstartmenu")


def sanitize_name(name):
    """去掉文件名非法字符。"""
    bad = '<>:"/\\|?*'
    out = "".join("_" if ch in bad else ch for ch in (name or "").strip())
    return out or DEFAULT_NAME


def _create_shortcut_com(path, target, args, workdir, icon, desc):
    """用 pywin32 (WScript.Shell) 创建快捷方式。"""
    import win32com.client  # noqa: WPS433 (延迟导入，便于无 pywin32 时回退)

    shell = win32com.client.Dispatch("WScript.Shell")
    link = shell.CreateShortcut(path)
    link.TargetPath = target
    link.Arguments = args
    link.WorkingDirectory = workdir
    link.IconLocation = icon
    link.Description = desc
    link.Save()


def _create_shortcut_powershell(path, target, args, workdir, icon, desc):
    """没有 pywin32 时的回退方案：调用内置 PowerShell 的 WScript.Shell。

    所有值都通过环境变量传递，避免中文/空格/引号带来的转义问题。
    """
    script = (
        "$ErrorActionPreference='Stop'\n"
        "$s=New-Object -ComObject WScript.Shell\n"
        "$l=$s.CreateShortcut($env:IE_LNK_PATH)\n"
        "$l.TargetPath=$env:IE_LNK_TARGET\n"
        "$l.Arguments=$env:IE_LNK_ARGS\n"
        "$l.WorkingDirectory=$env:IE_LNK_WORKDIR\n"
        "$l.IconLocation=$env:IE_LNK_ICON\n"
        "$l.Description=$env:IE_LNK_DESC\n"
        "$l.Save()\n"
    )
    env = os.environ.copy()
    env.update({
        "IE_LNK_PATH": path,
        "IE_LNK_TARGET": target,
        "IE_LNK_ARGS": args,
        "IE_LNK_WORKDIR": workdir,
        "IE_LNK_ICON": icon,
        "IE_LNK_DESC": desc,
    })
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        env=env, check=True,
    )


def settings_to_cli(settings):
    """把界面参数原样转成命令行参数（用于以管理员身份重新执行）。"""
    args = ["--create",
            "--target", settings.get("target", ""),
            "--args", settings.get("args", ""),
            "--name", settings.get("name", ""),
            "--location", settings.get("location", "desktop"),
            "--icon", settings.get("icon_mode", "system")]
    if settings.get("desc"):
        args += ["--desc", settings.get("desc")]
    if settings.get("custom_dir"):
        args += ["--custom-dir", settings.get("custom_dir")]
    return args


def relaunch_elevated(cli_args=None):
    """以管理员身份重新运行本工具，成功返回 True。

    “所有用户桌面 / 所有用户开始菜单”必须提权才能写入，这里提供一键提权重试，
    参数原样带过去，不会丢掉界面上填的内容。
    """
    try:
        import ctypes
        if getattr(sys, "frozen", False):
            exe = sys.executable
        else:
            exe = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "dist", "ie_launcher.exe")
            if not os.path.isfile(exe):
                exe = sys.executable
        params = subprocess.list2cmdline(cli_args or [])
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return int(rc) > 32
    except Exception:
        return False


def refresh_explorer():
    """通知资源管理器刷新，桌面图标立即可见。"""
    try:
        SHCNE_ASSOCCHANGED = 0x08000000
        SHCNF_IDLIST = 0x0000
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        pass


# 说明：本工具刻意**不**手工拼 .lnk 二进制。
# MS-SHLLINK 里 Windows 解析目标路径主要依赖 LinkTargetIDList（外壳 ID 列表），
# 手工拼出来的 LinkInfo 缺 IDList 时，系统会退回去读 RelativePath，
# 结果是“图标能显示、目标却是空的”——也就是快捷方式建得出来却点不动。
# 所以要在受保护目录落盘时，一律采用
# “临时目录让 Windows 自己生成一份 .lnk，再把它的原始字节搬到目标目录”的办法，
# 内容由系统生成，格式与系统完全一致。
def parse_shortcut(path):
    """不依赖 COM，直接解析 .lnk 二进制，取出目标/参数/图标等。"""
    import struct
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return {}
    if len(data) < 0x4C:
        return {}
    try:
        header_size = struct.unpack_from("<I", data, 0)[0]
        flags = struct.unpack_from("<I", data, 0x14)[0]
        pos = header_size
        result = {}
        if flags & 0x01:  # LinkTargetIDList：IDListSize(2) + IDList
            idlist_size = struct.unpack_from("<H", data, pos)[0]
            pos += 2 + idlist_size
        if flags & 0x02:  # LinkInfo
            li_size = struct.unpack_from("<I", data, pos)[0]
            lbp_off = struct.unpack_from("<I", data, pos + 16)[0]
            start = pos + lbp_off
            end = start
            limit = min(len(data), pos + li_size if li_size else len(data))
            while end + 2 <= limit and data[end:end + 2] != b"\x00\x00":
                end += 2
            if end <= limit:
                result["target"] = data[start:end].decode("utf-16-le", "replace")
            pos += li_size

        def read_string(offset):
            if offset + 2 > len(data):
                raise ValueError("字符串区越界")
            count = struct.unpack_from("<H", data, offset)[0]
            if count > 4096:
                raise ValueError("字符串长度异常")
            offset += 2
            text = data[offset:offset + count * 2].decode("utf-16-le", "replace")
            return text, offset + count * 2 + 2

        if flags & 0x04:
            result["name"], pos = read_string(pos)
        if flags & 0x08:
            result["relative"], pos = read_string(pos)
        if flags & 0x10:
            result["workdir"], pos = read_string(pos)
        if flags & 0x20:
            result["args"], pos = read_string(pos)
        if flags & 0x40:
            result["icon"], pos = read_string(pos)
        return result
    except Exception:
        return {}


def command_line(target, args=""):
    """拼出用户看到的那条完整命令行。"""
    target = (target or "").strip()
    if not target:
        return ""
    line = '"%s"' % target
    if args:
        line += " " + args.strip()
    return line


def split_command_line(cmd):
    """按 Windows 规则拆分命令行（与快捷方式实际执行方式一致）。

    使用 CommandLineToArgvW，带引号的参数不会被错误地按空格切开。
    """
    cmd = (cmd or "").strip()
    if not cmd:
        return []
    if os.name != "nt":
        return cmd.split()
    try:
        import ctypes
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        shell32.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p,
                                               ctypes.POINTER(ctypes.c_int)]
        shell32.CommandLineToArgvW.restype = ctypes.c_void_p
        argc = ctypes.c_int(0)
        raw = shell32.CommandLineToArgvW(ctypes.c_wchar_p(cmd), ctypes.byref(argc))
        if not raw:
            return cmd.split()
        argv = ctypes.cast(raw, ctypes.POINTER(ctypes.c_wchar_p))
        result = [argv[i] for i in range(argc.value)]
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree(ctypes.c_void_p(raw))
        return result
    except Exception:
        return cmd.split()


def resolve_target_folder(location, custom_dir=""):
    """把界面上的“创建位置”翻译成实际目录。

    只要自定义目录里填了内容，就以它为准（界面会自动把单选框切到“自定义目录”），
    绝不会出现“填了却没生效”的情况。
    """
    custom_dir = (custom_dir or "").strip().strip('"')
    if custom_dir:
        return os.path.abspath(os.path.expanduser(custom_dir))
    if location == "custom":
        return ""
    return resolve_location(location, custom_dir)


def _create_shortcut_anywhere(path, target, args, workdir, icon, desc):
    """在任意目录创建快捷方式：优先 pywin32，没有就退回 PowerShell。"""
    try:
        _create_shortcut_com(path, target, args, workdir, icon, desc)
        return "pywin32"
    except ImportError:
        _create_shortcut_powershell(path, target, args, workdir, icon, desc)
        return "powershell"


def _replace_file(stage, final):
    """把临时文件改名到位；目标已存在时先让位再删除旧文件。"""
    backup = ""
    if os.path.exists(final):
        backup = final + ".old_%d" % int(time.time())
        try:
            os.replace(final, backup)
        except OSError:
            backup = ""
    os.replace(stage, final)
    if backup:
        try:
            os.remove(backup)
        except OSError:
            pass


def _in_start_menu(folder):
    """目录是否属于“开始菜单 程序”区域（那里的 IE 条目会被系统自动改写）。"""
    try:
        programs = os.path.normcase(os.path.normpath(start_menu_dir()))
        all_programs = os.path.normcase(os.path.normpath(all_users_start_menu_dir()))
        folder = os.path.normcase(os.path.normpath(folder or ""))
        return folder.startswith(programs) or folder.startswith(all_programs)
    except Exception:
        return False


def _in_rewrite_scope(folder):
    """这个目录是不是“系统会改写 IE 名字快捷方式”的范围。

    实测：当前用户/所有用户的开始菜单，以及**所有用户桌面**、当前用户桌面，
    名字里含 “Internet Explorer” 的快捷方式都会被系统组件改写成旧版 IE
    （32 位目标、参数被清空）——点开就不是我们要的 IE 页面了。
    """
    try:
        folder = os.path.normcase(os.path.normpath(folder or ""))
        watched = [start_menu_dir(), all_users_start_menu_dir(),
                   public_desktop_dir(), desktop_dir()]
        for item in watched:
            if not item:
                continue
            item = os.path.normcase(os.path.normpath(item))
            if folder == item or folder.startswith(item + os.sep):
                return True
        return False
    except Exception:
        return False


def _safe_name_candidates(folder, stem):
    """按“能活得久”的顺序给出文件名候选。

    首选**始终是界面上填的名字**（默认 IE），不做任何改名。
    后面的备选只是**同一个名字的变体**，只在该名字被安全软件按名字拦下时才启用。

    注意：候选里**不能掺入与用户填的名字无关的名字**。
    早期版本硬塞了 “IE 快捷方式.lnk” / “IE.lnk” 两个候选，结果“按名字删除”会去删
    别的名字的快捷方式。现在这两个名字只在**名字本身含 “Internet Explorer”** 时才出现——
    那正是系统会改写、工具会另存成 IE 类安全名的场景。
    """
    candidates = [os.path.join(folder, stem + ".lnk")]
    for alt in ("%s (IE).lnk" % stem, "%s 快捷方式.lnk" % stem):
        item = os.path.join(folder, alt)
        if item not in candidates:
            candidates.append(item)
    if "internet explorer" in (stem or "").lower():
        for alt in ("IE 快捷方式.lnk", "IE.lnk"):
            item = os.path.join(folder, alt)
            if item not in candidates:
                candidates.append(item)
    return candidates


def safe_name_for(folder, stem):
    """给出一个不含 “Internet Explorer” 字样的安全名（供系统改写后另存用）。

    返回的是**文件名（不带 .lnk）**，可以直接填回界面的“快捷方式名”。
    """
    for alt in ("IE 快捷方式", "IE启动器", "IE 网页", "IE"):
        if not os.path.isfile(os.path.join(folder, alt + ".lnk")):
            return alt
    return "IE 快捷方式"


def _shortcut_name_candidates(path):
    """快捷方式文件名候选：原名优先，再给几个安全软件不容易拦的备选。"""
    folder = os.path.dirname(path)
    stem = os.path.splitext(os.path.basename(path))[0]
    return _safe_name_candidates(folder, stem)


def _is_access_denied(exc):
    """判断错误是不是“目录/文件被拒绝写入”。"""
    text = ("%s %s" % (exc, getattr(exc, "args", ""))).lower()
    for needle in ("拒绝访问", "access is denied", "permission denied",
                   "permissionerror", "wshshortcut.save"):
        if needle in text:
            return True
    return False


def _is_system_ie_shortcut(path):
    """判断现有文件是否是系统自带的 IE 快捷方式（指向 iexplore.exe 且没有参数）。"""
    if not os.path.isfile(path):
        return False
    info = read_shortcut(path)
    target = (info.get("target") or "").lower()
    args = (info.get("arguments") or "").strip()
    return target.endswith("iexplore.exe") and not args


def _create_shortcut_via_temp(path, target, args, workdir, icon, desc,
                              candidates=None):
    """先在临时目录让 Windows 自己生成一份 .lnk，再把这份文件的原始字节写到目标目录。

    为什么不能自己手搓 .lnk 二进制：
    MS-SHLLINK 里 Windows 解析目标路径主要依赖 LinkTargetIDList（外壳 ID 列表），
    手写的 LinkInfo 缺少 IDList 时系统会退回去读 RelativePath，结果就是
    “快捷方式建得出来、点不动”（图标能显示、目标却是空的）。
    所以这里让系统自己生成，内容与系统完全一致，再整体搬运字节。

    搬运方式按“先易后难”依次尝试：
      1. 直接按**最终文件名**写字节——绕开“复制后再改名”里被安全软件拦下的改名动作；
      2. 复制成临时名再改名——应对“按最终名写入被拦、但改名放行”的环境。

    返回 (实际落盘路径, 提示文字)。
    """
    import shutil
    import tempfile
    import uuid

    tmp_dir = tempfile.mkdtemp(prefix="ielnk_")
    tmp_path = os.path.join(tmp_dir, os.path.basename(path))
    try:
        _create_shortcut_anywhere(tmp_path, target, args, workdir, icon, desc)
        with open(tmp_path, "rb") as f:
            payload = f.read()
        if not payload:
            raise OSError("临时目录里生成的快捷方式是空的")

        folder = os.path.dirname(path)
        if not candidates:
            candidates = _shortcut_name_candidates(path)

        last = None
        for candidate in candidates:
            # 方式一：直接按最终文件名写字节
            try:
                with open(candidate, "wb") as f:
                    f.write(payload)
                normalize_shortcut_properties(candidate)
                return candidate, _rename_note(path, candidate)
            except OSError as exc:
                last = exc
                if os.path.exists(candidate) and os.path.getsize(candidate) != len(payload):
                    try:
                        os.remove(candidate)
                    except OSError:
                        pass

            # 方式二：复制成临时名再改名
            stage = os.path.join(folder,
                                 ".ie_launcher_%s.tmp" % uuid.uuid4().hex[:8])
            try:
                shutil.copyfile(tmp_path, stage)
                _replace_file(stage, candidate)
                normalize_shortcut_properties(candidate)
                return candidate, _rename_note(path, candidate)
            except OSError as exc:
                last = exc
                if os.path.exists(stage):
                    try:
                        os.remove(stage)
                    except OSError:
                        pass
        raise last or OSError("落盘失败")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _rename_note(path, candidate):
    """如果最终落盘的文件名和界面里填的不一样，说明被拦了，给一句说明。"""
    if os.path.normcase(candidate) == os.path.normcase(path):
        return ""
    return ("安全软件拦截了 %s 这个名字（浏览器快捷方式防护），已改存为 %s。"
            "如需使用原名，请在安全软件里放行，或把创建位置改成开始菜单。"
            % (os.path.basename(path), os.path.basename(candidate)))


def plan_shortcut(folder, name, check_existing=True):
    """决定最终保存成哪个文件，返回 (路径, 候选列表, 说明列表)。

    界面预览与实际创建共用这套规则，保证“预览里看到的”和“真正写进去的”一致。
    """
    stem = sanitize_name(name)
    requested = os.path.join(folder, stem + ".lnk")
    candidates = _safe_name_candidates(folder, stem)
    notes = []

    if check_existing and _is_system_ie_shortcut(requested):
        candidates = [c for c in candidates
                      if os.path.normcase(c) != os.path.normcase(requested)]
        notes.append("目标位置已有系统自带的 Internet Explorer 条目，"
                     "为避免覆盖后被系统还原，已另存为新的快捷方式。")

    return candidates[0], candidates, notes


def _set_file_times(path, creation, access, write):
    """用 Windows API 设置文件的三个时间戳（os.utime 改不了创建时间）。"""
    try:
        import ctypes
        from ctypes import wintypes

        file_time = wintypes.FILETIME
        handle = ctypes.windll.kernel32.CreateFileW(
            path, 0x40000000, 0x7, None, 3, 0x02000000, None)
        if handle in (-1, 0xFFFFFFFFFFFFFFFF):
            return False
        try:
            c = file_time(creation & 0xFFFFFFFF, creation >> 32)
            a = file_time(access & 0xFFFFFFFF, access >> 32)
            w = file_time(write & 0xFFFFFFFF, write >> 32)
            ok = ctypes.windll.kernel32.SetFileTime(
                handle, ctypes.byref(c), ctypes.byref(a), ctypes.byref(w))
            return bool(ok)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return False


def _unix_to_filetime(seconds):
    """unix 秒 -> Windows FILETIME（100ns since 1601）。"""
    return int((seconds + 11644473600) * 10000000)


def normalize_shortcut_properties(path):
    """把落盘后的快捷方式文件属性整理成“系统默认”的样子。

    系统自己创建的 .lnk 长这样：
      · 外部文件的创建/写入/访问时间 = 它被创建的那一刻；
      · 文件属性 = ARCHIVE；
      · 内嵌 ShellLinkHeader 里的三个时间戳 = **目标程序**的时间戳（外壳写进去的）。

    这里保证外部时间戳与实际创建时刻一致、属性为 ARCHIVE，
    内嵌值原样保留（本来就该是目标程序的时间戳），与系统生成的快捷方式完全一致。
    返回是否处理成功。
    """
    try:
        # 用“此刻”作为创建/写入/访问时间：覆盖已有文件时 getctime 仍会返回
        # 它最初创建的时间，会让文件看起来是旧文件。
        now = time.time()
        ft = _unix_to_filetime(now)
        ok = _set_file_times(path, ft, ft, ft)
        if not ok:
            try:
                os.utime(path, (now, now))
            except OSError:
                pass
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(path, 0x20)  # ARCHIVE
        except Exception:
            pass
        return True
    except OSError:
        return False


def _shortcut_matches(path, target, args):
    """核对快捷方式里还是不是我们写进去的目标和参数（用来发现系统改写）。"""
    info = read_shortcut(path)
    if not info:
        return False
    return (_norm(info.get("target", "")) == _norm(target)
            and (info.get("arguments") or "") == (args or ""))


def _check_system_rewrite(path, target, args, wait=2.0):
    """系统有时会在创建后一两秒内把快捷方式改写掉。

    实测：这是 **Windows 自己**对“IE 同名快捷方式”的刷新行为——只要文件名里含
    “Internet Explorer”，在开始菜单 / 桌面 / 所有用户桌面这些系统托管位置，
    创建后 1.7 秒内就会被替换成旧版 IE 条目（32 位目标、参数被清空）。
    与创建方式无关：原生 WScript.Shell、原始字节搬运、属性归一化三种方式实测都被替换；
    而非系统托管的目录（自定义目录等）里同样的名字完全不受影响。

    返回 (是否被改写, 说明文字)。这里只**如实上报**，不改名——用哪个名字由用户决定。
    """
    time.sleep(wait)
    if _shortcut_matches(path, target, args):
        return False, ""
    return True, ("系统把刚创建的 %s 替换成了旧版 IE 条目"
                  "（Windows 对 IE 同名快捷方式的刷新行为，与创建方式无关；"
                  "换成不含“Internet Explorer”的名字，或存到自定义目录就不会被改写）。"
                  % os.path.basename(path))


def create_shortcut(settings, on_progress=None):
    """按界面给定的参数创建快捷方式，返回完整的结果字典。

    settings 里的每一个字段都会写进快捷方式，不会有任何硬编码覆盖；
    文件名就用界面上填的名字（默认 Internet Explorer），不做任何自动改名。
    """
    target = (settings.get("target") or "").strip().strip('"')
    if not target:
        raise ValueError("目标程序不能为空")
    args = (settings.get("args") or "").strip()
    name = sanitize_name(settings.get("name"))
    desc = settings.get("desc") or ""
    icon_mode = settings.get("icon_mode") or "system"
    location = settings.get("location") or "desktop"

    folder = resolve_target_folder(location, settings.get("custom_dir", ""))
    if not folder:
        raise ValueError("创建位置选的是“自定义目录”，但目录栏是空的，请填写或改选其它位置")

    if not os.path.isdir(folder):
        try:
            os.makedirs(folder)
        except OSError as exc:
            raise OSError("无法创建目录 %s：%s" % (folder, exc))

    if not os.access(folder, os.W_OK):
        raise PermissionError(
            "目录没有写入权限：%s\n%s"
            % (folder,
               "“所有用户桌面/开始菜单”需要管理员权限，"
               "请右键 ie_launcher.exe 选择“以管理员身份运行”。"
               if _needs_admin(location) else
               "请改选其它位置，或以管理员身份运行工具。"))

    requested_path = os.path.join(folder, name + ".lnk")
    path, candidates, notes = plan_shortcut(folder, name)

    errors = []
    how = ""

    existed = os.path.isfile(path)
    workdir = os.path.dirname(target) if os.path.dirname(target) else os.getcwd()

    if icon_mode == "bundled":
        icon = bundled_icon_path()
        if not icon:
            raise RuntimeError("内置图标释放失败，无法按界面设置使用内置图标")
    else:
        icon = "%s,0" % target

    # 1) 直接在目标位置创建
    denied = False
    try:
        _create_shortcut_com(path, target, args, workdir, icon, desc)
        how = "pywin32"
    except ImportError:
        errors.append("pywin32 未安装")
    except Exception as exc:
        errors.append("直接创建（pywin32）：%s" % _short_error(exc))
        denied = _is_access_denied(exc)

    # 2) 换 PowerShell 再试一次（若是“拒绝访问”，同一 COM 引擎会同样失败，跳过）
    if not how and not denied:
        try:
            _create_shortcut_powershell(path, target, args, workdir, icon, desc)
            how = "powershell"
        except Exception as exc:
            errors.append("直接创建（PowerShell）：%s" % _short_error(exc))
            if _is_access_denied(exc):
                denied = True

    # 3) 临时目录让 Windows 生成 + 把字节搬到目标目录
    #    （安全软件禁止在受保护目录“新建快捷方式”时，这一步仍然能得到格式完全正确的 .lnk）
    if not how:
        try:
            actual, note = _create_shortcut_via_temp(path, target, args, workdir,
                                                      icon, desc, candidates)
            path = actual
            how = "临时生成后写入"
            if note:
                notes.append(note)
            else:
                notes.append("目标目录禁止直接新建快捷方式（多半是安全软件拦截），"
                             "已在临时目录生成后把文件内容整体写入，字段完整。")
        except Exception as exc:
            errors.append("临时生成后写入：%s" % _short_error(exc))

    if not how:
        needs_admin = _needs_admin(location)
        raise RuntimeError(
            "创建快捷方式失败：\n%s\n\n%s"
            % ("\n".join(errors),
               ("这个目录需要管理员权限才能写入（%s）。\n"
                "建议：右键 ie_launcher.exe 选择“以管理员身份运行”，"
                "或者把“创建位置”改成“开始菜单”。" % folder)
               if needs_admin else
               ("该目录已连续拒绝创建快捷方式，多半是安全软件（浏览器快捷方式防护）拦截。\n"
                "建议：① 把“创建位置”改成“开始菜单”；② 在安全软件里放行；"
                "③ 或换一个保存目录。")))

    if not os.path.isfile(path):
        raise RuntimeError("快捷方式未生成：%s\n%s" % (path, "\n".join(errors)))

    # 文件属性对齐成“系统默认”的样子（时间戳、ARCHIVE 属性），
    # 避免看起来像是从外部塞进来的快捷方式而触发杀软误报。
    if not normalize_shortcut_properties(path):
        notes.append("文件属性未能对齐到系统默认值（不影响使用）。")

    # 桌面 / 开始菜单这类系统托管位置，Windows 自己会刷新 IE 同名快捷方式，
    # 这里复核一次并如实上报，由用户决定要不要换名字。
    rewritten = False
    if _in_rewrite_scope(folder):
        if on_progress:
            on_progress("正在复核系统是否改写了快捷方式……")
        rewritten, rewrite_msg = _check_system_rewrite(path, target, args)
        if rewritten:
            notes.append(rewrite_msg)

    refresh_explorer()
    return {
        "path": path,
        "name": os.path.splitext(os.path.basename(path))[0],
        "folder": folder,
        "target": target,
        "args": args,
        "workdir": workdir,
        "icon": icon,
        "desc": desc,
        "method": how,
        "existed": existed,
        "notes": notes,
        "rewritten": rewritten,
        "command": command_line(target, args),
    }


def _short_error(exc):
    """把 COM/PowerShell 的长报错压成一行。"""
    text = str(exc)
    if "WshShortcut.Save" in text:
        idx = text.find("(")
        text = "WshShortcut.Save 失败" + (text[idx:] if idx > 0 else "")
    return text.replace("\r", " ").replace("\n", " ")[:300]


def _norm(text):
    """比较用的归一化：去空白、转小写、去尾部斜杠。"""
    text = (text or "").strip()
    if text.endswith("\\"):
        text = text[:-1]
    return text.lower()


def _norm_icon(text):
    """图标路径归一化：去掉 WScript 自动追加的 ",0" 之类的索引后缀。"""
    text = (text or "").strip()
    head, sep, tail = text.rpartition(",")
    if sep and tail.strip().isdigit():
        text = head
    return _norm(text)


def verify_shortcut(path, expect):
    """把快捷方式读回来，逐项核对界面设置的参数是否真的写进去了。"""
    actual = read_shortcut(path)
    checks = []

    def add(field, expected, got, ignore_case=False, is_icon=False):
        if is_icon:
            ok = _norm_icon(expected) == _norm_icon(got)
        elif ignore_case:
            ok = _norm(expected) == _norm(got)
        else:
            ok = (expected or "") == (got or "")
        checks.append({"field": field, "expected": expected, "actual": got, "ok": ok})

    add("目标程序", expect.get("target"), actual.get("target", ""), True)
    add("启动参数", expect.get("args"), actual.get("arguments", ""))
    add("工作目录", expect.get("workdir"), actual.get("workdir", ""), True)
    add("图标", expect.get("icon"), actual.get("icon", ""), is_icon=True)
    add("描述", expect.get("desc"), actual.get("description", ""))
    add("快捷方式名", expect.get("name") + ".lnk", os.path.basename(path), True)
    add("保存位置", expect.get("folder"), os.path.dirname(path), True)
    return checks


def bundled_icon_path():
    """把内置 ie_icon.ico 释放到临时目录，返回路径。"""
    src = resource_path("ie_icon.ico")
    if not os.path.isfile(src):
        return ""
    dst_dir = os.path.join(tempfile.gettempdir(), "IELauncher")
    if not os.path.isdir(dst_dir):
        os.makedirs(dst_dir)
    dst = os.path.join(dst_dir, "ie_icon.ico")
    try:
        with open(src, "rb") as f:
            data = f.read()
        if not os.path.isfile(dst) or open(dst, "rb").read() != data:
            with open(dst, "wb") as f:
                f.write(data)
    except Exception:
        return src
    return dst


def delete_shortcut(path):
    """删除快捷方式：**先检测是否存在，存在才删**，删完再复核一遍。

    返回 (是否成功, 说明文字)。
    """
    if not path:
        return False, "没有指定要删除的快捷方式"
    if not os.path.isfile(path):
        return False, "快捷方式不存在：%s" % path
    try:
        os.remove(path)
    except OSError as exc:
        return False, "删除失败：%s（%s）" % (path, exc)
    if os.path.exists(path):
        return False, "删除失败：文件仍然存在（可能被安全软件锁住）：%s" % path
    refresh_explorer()
    return True, "已删除：%s" % path


def detect_for_delete(settings, extra_paths=None):
    """按界面参数检测“可删除的快捷方式”。

    返回 (可删除的列表, 系统自带需要跳过的列表, 会检查的位置列表)。
    """
    existing, expected = find_shortcuts_for_delete(settings, extra_paths)
    created = [os.path.normcase(p) for p in (extra_paths or [])]
    skipped = [p for p in existing
               if _is_system_ie_shortcut(p)
               and os.path.normcase(p) not in created]
    targets = [p for p in existing if p not in skipped]
    return targets, skipped, expected


def delete_paths(paths):
    """逐个删除并复核，返回 (已删除列表, 失败列表)。"""
    removed, failed = [], []
    for path in paths:
        ok, msg = delete_shortcut(path)
        if ok:
            removed.append((path, msg))
        else:
            failed.append((path, msg))
    return removed, failed


def find_shortcuts_for_delete(settings, extra_paths=None):
    """按界面参数找出“已经存在”的快捷方式，供删除操作使用。

    检测范围（和创建时的规则保持一致）：
      - 界面上的“创建位置 + 快捷方式名”算出来的路径；
      - 同一目录下可能被系统改写或被安全软件拦下后换过的备用名（安全名候选）；
      - 本次会话里刚创建出来的那个路径。

    返回 (已存在的文件列表, 预期会检查的路径列表)。
    """
    location = settings.get("location") or "desktop"
    custom_dir = settings.get("custom_dir") or ""
    name = sanitize_name(settings.get("name"))

    expected = []
    folder = resolve_target_folder(location, custom_dir)
    if folder:
        try:
            _path, candidates, _notes = plan_shortcut(folder, name,
                                                       check_existing=False)
        except Exception:
            candidates = []
        for item in candidates:
            if item and item not in expected:
                expected.append(item)
    for item in (extra_paths or []):
        if item and item not in expected:
            expected.append(item)

    existing = [p for p in expected if os.path.isfile(p)]
    return existing, expected


def read_shortcut(path):
    """读回快捷方式的目标与参数，用于校验。

    优先用 COM 读；COM 读不到时退回二进制解析，保证校验始终有结果。
    """
    info = {}
    try:
        import win32com.client  # noqa: WPS433
        shell = win32com.client.Dispatch("WScript.Shell")
        link = shell.CreateShortcut(path)
        info = {
            "target": link.TargetPath,
            "arguments": link.Arguments,
            "workdir": link.WorkingDirectory,
            "icon": link.IconLocation,
            "description": link.Description,
        }
    except Exception:
        info = {}
    if not info.get("target"):
        parsed = parse_shortcut(path)
        if parsed.get("target"):
            info.setdefault("target", parsed.get("target", ""))
            info.setdefault("arguments", parsed.get("args", ""))
            info.setdefault("workdir", parsed.get("workdir", ""))
            info.setdefault("icon", parsed.get("icon", ""))
    return info


# --------------------------------------------------------------------------------------
# 环境检测
# --------------------------------------------------------------------------------------

def find_ie():
    for p in IE_CANDIDATES:
        if os.path.isfile(p):
            return p
    return ""


def find_edge():
    for p in EDGE_CANDIDATES:
        if os.path.isfile(p):
            return p
    return ""


def os_caption():
    try:
        import ctypes

        class OSVERSIONINFOEX(ctypes.Structure):
            _fields_ = [
                ("dwOSVersionInfoSize", ctypes.c_uint32),
                ("dwMajorVersion", ctypes.c_uint32),
                ("dwMinorVersion", ctypes.c_uint32),
                ("dwBuildNumber", ctypes.c_uint32),
                ("dwPlatformId", ctypes.c_uint32),
                ("csdVersion", ctypes.c_ushort * 128),
                ("wServicePackMajor", ctypes.c_ushort),
                ("wServicePackMinor", ctypes.c_ushort),
                ("wSuiteMask", ctypes.c_ushort),
                ("wProductType", ctypes.c_ubyte),
                ("wReserved", ctypes.c_ubyte),
            ]

        info = OSVERSIONINFOEX()
        info.dwOSVersionInfoSize = ctypes.sizeof(OSVERSIONINFOEX)
        ctypes.windll.RtlGetVersion(ctypes.byref(info))
        build = info.dwBuildNumber
        name = "Windows 11" if build >= 22000 else "Windows 10"
        return "%s (内部版本 %d.%d.%d)" % (name, info.dwMajorVersion, info.dwMinorVersion, build)
    except Exception:
        import platform
        return platform.platform()


def file_version(path):
    """读取 exe 的文件版本号，失败返回空串。"""
    try:
        import win32api  # noqa: WPS433
        info = win32api.GetFileVersionInfo(path, "\\")
        ms = info["FileVersionMS"]
        ls = info["FileVersionLS"]
        return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return ""


def list_pids(image):
    """取某个镜像名的进程 PID 集合（无需 psutil）。"""
    pids = set()
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq %s" % image, "/NH", "/FO", "CSV"],
            stderr=subprocess.DEVNULL,
        )
        for line in out.decode("mbcs", "ignore").splitlines():
            parts = [p.strip('" ') for p in line.split('","')]
            if len(parts) > 1 and parts[1].isdigit():
                pids.add(int(parts[1]))
    except Exception:
        pass
    return pids


def ie_windows():
    """枚举 IEFrame 顶层窗口（真 Internet Explorer 的窗口类）。"""
    titles = []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _):
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if cls.value == "IEFrame":
                title = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(hwnd, title, 512)
                titles.append(title.value)
            return True

        user32.EnumWindows(proc_type(callback), 0)
    except Exception:
        pass
    return titles


def launch_and_classify(target, args="", wait=8.0):
    """启动目标并判断到底是真 IE 还是被 Edge 接管。

    判定依据是窗口类 IEFrame 是否出现（真 IE），而不是只看进程名。
    返回 (状态文字, 是否真IE)
    """
    before_ie = list_pids("iexplore.exe")
    before_edge = list_pids("msedge.exe")

    argv = split_command_line(command_line(target, args))
    if not argv:
        return ("启动失败：目标程序为空", False)

    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        return ("启动失败：%s" % exc, False)

    deadline = time.time() + wait
    saw_edge = False
    while time.time() < deadline:
        time.sleep(0.4)
        wins = ie_windows()
        if wins:
            title = wins[0] or "(无标题)"
            return ("已打开真正的 Internet Explorer 窗口（IEFrame）：%s" % title, True)
        if list_pids("msedge.exe") - before_edge:
            saw_edge = True
        if saw_edge and time.time() > deadline - wait + 3.0:
            break

    if saw_edge:
        return ("被 Microsoft Edge 接管了：本次没有出现 IEFrame 窗口，"
                "实际打开的是 Edge（msedge.exe）。"
                "提示：启动参数里保留 -Embedding 可强制走真 IE（Win10 实测有效）；"
                "也可以点界面上的“用 %s 位 IE”按钮换另一个位数的启动器再试。"
                % ("64" if ie_bitness(target) == "32" else "32"), False)
    return ("已发出启动命令，但未检测到 IE 窗口，请手动确认", False)


def detect_all():
    """收集环境信息，供界面与命令行使用。"""
    ie = find_ie()
    edge = find_edge()
    info = {
        "os": os_caption(),
        "ie_path": ie,
        "ie_exists": bool(ie),
        "edge_path": edge,
        "edge_exists": bool(edge),
        "edge_version": file_version(edge),
        "desktop": desktop_dir(),
        "startmenu": start_menu_dir(),
    }
    return info


# --------------------------------------------------------------------------------------
# DPI 缩放适配
# --------------------------------------------------------------------------------------
#
# 不做适配时的两个毛病：
#   1) 进程默认是 DPI-unaware，Windows 会把整张窗口按位图拉伸，
#      125% / 150% 下文字发虚、边缘发糊；
#   2) 界面按 96 DPI 排版再被系统整体放大，窗口高度容易超出工作区，
#      小屏上底部直接看不见（原来记在“已知限制”里）。
#
# 这里做三件事：
#   1) 在建任何窗口之前把进程声明成 DPI 感知（优先 Per-Monitor V2）；
#   2) 按显示器真实 DPI 设置 Tk 的缩放系数，界面里所有以像素为单位的外边距 /
#      内边距按同一系数换算；
#   3) 窗口放不下时先压缩文本行数（字号不变），再整体缩小，保证完整落在工作区内。
#
# 注意：Tk 的 `tk scaling` 只在**新建字体**时才把“磅”换算成像素，
# 运行中改它不会重排已有控件——所以运行中缩放必须自己重设字号（见 DpiLayout.apply_fonts）。

DPI_AWARENESS_PER_MONITOR_V2 = -4

BASE_DPI = 96.0          # 界面按 96 DPI 设计，所有像素量都以它为基准
MIN_FACTOR = 0.62        # 自动缩放的下限（相对 96 DPI 基准，太小就没法看了）
MIN_FONT_PX = 8          # 缩到最小也不能小于这个像素高度，否则看不清
DEFAULT_FONT_PX = (10, 16)   # 96 DPI 下系统默认字体的合理像素范围

_DPI_STATE = {"mode": ""}


def enable_dpi_awareness():
    """把当前进程声明为 DPI 感知——**必须在创建任何窗口之前调用**。

    依次尝试 Per-Monitor V2 → Per-Monitor → System；
    已经被 exe 清单声明过时 API 会返回 ERROR_ACCESS_DENIED，那也算已生效。
    返回实际生效的模式说明。
    """
    if _DPI_STATE["mode"]:
        return _DPI_STATE["mode"]

    mode = "未启用（窗口由系统按位图拉伸，文字会发虚）"
    done = False
    if os.name == "nt":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32

            if hasattr(user32, "SetProcessDpiAwarenessContext"):
                user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
                user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
                if user32.SetProcessDpiAwarenessContext(
                        ctypes.c_void_p(DPI_AWARENESS_PER_MONITOR_V2)):
                    mode, done = "Per-Monitor V2", True
                elif kernel32.GetLastError() == 5:   # ERROR_ACCESS_DENIED
                    mode, done = "已由 exe 清单声明", True
            if not done:
                try:
                    # shcore 返回 HRESULT，0 才是成功
                    if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
                        mode, done = "Per-Monitor", True
                except Exception:
                    pass
            if not done:
                try:
                    user32.SetProcessDPIAware.restype = ctypes.c_bool
                    if user32.SetProcessDPIAware():
                        mode, done = "System", True
                except Exception:
                    pass
        except Exception:
            pass

    _DPI_STATE["mode"] = mode
    return mode


def window_dpi(hwnd=None):
    """取窗口所在显示器的 DPI（每英寸像素数）。取不到按 96 处理。

    注意：进程必须已经是 DPI 感知状态，否则这里只会拿到虚拟化的 96。
    """
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            if hwnd and hasattr(user32, "GetDpiForWindow"):
                user32.GetDpiForWindow.argtypes = [wintypes.HWND]
                user32.GetDpiForWindow.restype = ctypes.c_uint
                dpi = int(user32.GetDpiForWindow(hwnd))
                if dpi > 0:
                    return dpi
            if hasattr(user32, "GetDpiForSystem"):
                user32.GetDpiForSystem.restype = ctypes.c_uint
                dpi = int(user32.GetDpiForSystem())
                if dpi > 0:
                    return dpi
            if hwnd:
                user32.GetDC.argtypes = [wintypes.HWND]
                user32.GetDC.restype = wintypes.HDC
                user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
                hdc = user32.GetDC(hwnd)
                if hdc:
                    try:
                        ctypes.windll.gdi32.GetDeviceCaps.restype = ctypes.c_int
                        dpi = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
                        if dpi > 0:
                            return dpi
                    finally:
                        user32.ReleaseDC(hwnd, hdc)
        except Exception:
            pass
    return int(BASE_DPI)


def work_area(hwnd=None):
    """返回窗口所在显示器的工作区 (left, top, right, bottom)，已排除任务栏。

    单位是物理像素（进程 DPI 感知时）。
    """
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD),
                            ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT),
                            ("dwFlags", wintypes.DWORD)]

            monitor = None
            if hwnd:
                user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
                user32.MonitorFromWindow.restype = wintypes.HANDLE
                monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
            if monitor:
                info = MONITORINFO()
                info.cbSize = ctypes.sizeof(MONITORINFO)
                user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE,
                                                   ctypes.POINTER(MONITORINFO)]
                if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                    rect = info.rcWork
                    return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:
            pass
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(
                0x0030, 0, ctypes.byref(rect), 0)   # SPI_GETWORKAREA
            return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:
            pass
    return (0, 0, 1920, 1080)


def _parse_len(value):
    """把 Tk 的尺寸取值解析成 int 或 (int, int)。"""
    if isinstance(value, (tuple, list)):
        parts = []
        for item in value:
            try:
                parts.append(int(float(item)))
            except (TypeError, ValueError):
                return 0
        if not parts:
            return 0
        return parts[0] if len(parts) == 1 else tuple(parts)
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        parts = []
        for item in str(value).strip().split():
            try:
                parts.append(int(float(item)))
            except ValueError:
                return 0
        if not parts:
            return 0
        return parts[0] if len(parts) == 1 else tuple(parts)


def _scale_len(value, factor):
    """按系数换算像素量。"""
    if isinstance(value, tuple):
        return tuple(max(0, int(round(v * factor))) for v in value)
    return max(0, int(round(value * factor)))


def _walk_widgets(widget):
    """遍历控件树。"""
    out = [widget]
    for child in widget.winfo_children():
        out.extend(_walk_widgets(child))
    return out


DEFAULT_FONT_NAMES = ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                      "TkHeadingFont", "TkCaptionFont", "TkSmallCaptionFont")


def _font_spec_info(spec):
    """从字体规格里取出 (字体名, 磅数)；取不到返回 None。

    支持三种形态：
      · ('Consolas', 9) 这样的元组；
      · 'Consolas 9' 这样的字符串；
      · ttk 控件 cget("font") 返回的 Font 对象。
    以负数给出的就是像素尺寸（已经是最终值），返回 None 不再缩放。
    """
    if isinstance(spec, (tuple, list)):
        parts = list(spec)
        if len(parts) >= 2 and isinstance(parts[1], int):
            return str(parts[0]), int(parts[1])
        return None
    if isinstance(spec, str):
        text = spec.strip()
        if not text or text.startswith("Tk"):
            return None          # Tk 默认字体，单独处理
        parts = text.split()
        if len(parts) >= 2:
            try:
                return " ".join(parts[:-1]), int(parts[-1])
            except ValueError:
                return None
        return None
    # tkinter.font.Font 对象（ttk 控件就是这样返回的）
    if hasattr(spec, "actual"):
        name = str(getattr(spec, "name", "") or "")
        if name.startswith("Tk"):
            return None          # Tk 默认字体，单独处理
        try:
            size = spec.actual("size")
            family = spec.actual("family")
        except Exception:
            return None
        if isinstance(family, (tuple, list)):
            family = family[0] if family else ""
        if isinstance(size, int) and size > 0:
            return str(family), int(size)
    return None


class DpiLayout:
    """记录界面里所有以像素为单位的大小与字体，并按缩放系数重新换算。

    字体这块有个坑：Tk 的 ``tk scaling`` 只在**新建**字体时才把“磅”换算成像素，
    运行中改它并不会重排已经建好的控件。所以运行中要缩放（小屏自适应、
    拖到缩放比例不同的显示器），必须自己把字体重设成像素尺寸。
    """

    GRID_OPTIONS = ("padx", "pady", "ipadx", "ipady")
    WIDGET_OPTIONS = ("padding", "wraplength", "padx", "pady")

    def __init__(self, root):
        self.root = root
        self.items = []          # (控件, 类别, 设计值)
        self.elastic = []        # [控件, 当前行数, 设计行数, 最少行数]
        self.fonts = []          # (控件, 字体名, 设计磅数, 设计像素数)
        self.default_fonts = []  # (字体对象, 设计像素数)
        self.factor = 1.0
        self.base_factor = 1.0

    def capture(self, base_factor=1.0):
        """在还没缩放前，把所有像素量与字体记成“设计值”。"""
        self.items = []
        self.fonts = []
        self.default_fonts = []
        self.base_factor = base_factor or 1.0
        scaling = 1.0
        try:
            scaling = float(self.root.tk.call("tk", "scaling"))
        except Exception:
            pass

        # Tk 自带默认字体（ttk 控件用的就是它）
        # 注意：不能用 tkfont.nametofont()——它绑定的是“默认根窗口”，
        # 多窗口时可能作用到别的解释器上，这里直接用本窗口的解释器调用。
        for name in DEFAULT_FONT_NAMES:
            try:
                size = self.root.tk.call("font", "configure", name, "-size")
            except Exception:
                continue
            try:
                points = int(str(size).strip())
            except (TypeError, ValueError):
                continue
            if points <= 0:
                continue
            # 磅数换算成像素，再折算到 96 DPI 基准做一次合理性钳制：
            # 个别环境里系统字体会被重复放大（同一进程里开第二个 Tk 时见过
            # 默认字体变成 18 磅），不钳一下排版会整个撑大。
            pixels = points * scaling
            base_px = pixels / (base_factor or 1.0)
            base_px = min(max(base_px, DEFAULT_FONT_PX[0]), DEFAULT_FONT_PX[1])
            self.default_fonts.append((name, int(round(base_px * base_factor))))

        for widget in _walk_widgets(self.root):
            try:
                info = widget.grid_info()
            except Exception:
                info = {}
            for key in self.GRID_OPTIONS:
                if key in info:
                    self.items.append((widget, "grid:" + key, _parse_len(info[key])))
            for key in self.WIDGET_OPTIONS:
                try:
                    value = widget.cget(key)
                except Exception:
                    continue          # 这个控件没有这个选项
                if value in (None, "", 0):
                    continue
                self.items.append((widget, key, _parse_len(value)))
            try:
                spec = widget.cget("font")
            except Exception:
                continue
            parsed = _font_spec_info(spec)
            if parsed:
                family, points = parsed
                if points > 0:
                    self.fonts.append((widget, family, points,
                                       int(round(points * scaling))))
        return self

    def apply(self, factor):
        """按系数重新换算并写回所有像素量与字体。"""
        self.factor = factor
        for widget, kind, design in self.items:
            value = _scale_len(design, factor)
            try:
                if kind.startswith("grid:"):
                    widget.grid_configure(**{kind.split(":", 1)[1]: value})
                else:
                    widget.configure(**{kind: value})
            except Exception:
                continue
        self.apply_fonts(factor)

    def apply_fonts(self, factor):
        """按系数重设字体。

        ``tk scaling`` 对已建好的字体无效，所以这里把字号换算成**像素**再写回去
        （Tk 里负数字号就是像素），这样运行中缩放才能真正生效。
        """
        ratio = factor / (self.base_factor or 1.0)
        for name, pixels in self.default_fonts:
            try:
                self.root.tk.call("font", "configure", name, "-size",
                                  -max(MIN_FONT_PX, int(round(pixels * ratio))))
            except Exception:
                continue
        for widget, family, _points, pixels in self.fonts:
            try:
                widget.configure(font=(family, -max(MIN_FONT_PX,
                                                    int(round(pixels * ratio)))))
            except Exception:
                continue

    def add_elastic(self, widget, rows, min_rows=4):
        """登记一个“行数可压缩”的文本框：屏幕放不下时先少显示几行。"""
        self.elastic.append([widget, rows, rows, min_rows])

    def reset_rows(self):
        """恢复文本框的设计行数（换到大屏时用）。"""
        for item in self.elastic:
            item[1] = item[2]
            try:
                item[0].configure(height=item[1])
            except Exception:
                continue

    def shrink_rows(self):
        """所有可压缩文本框各减一行；返回是否真的减了。"""
        changed = False
        for item in self.elastic:
            widget, rows, design, min_rows = item
            if rows > min_rows:
                item[1] = rows - 1
                try:
                    widget.configure(height=item[1])
                except Exception:
                    continue
                changed = True
        return changed


def fit_to_work_area(root, content, layout, factor, hwnd=None, min_factor=MIN_FACTOR):
    """把界面调整到能放进当前显示器的工作区。

    顺序：先压缩文本框行数（字号不变），再整体缩小（字号跟着缩）。
    content 是内容容器（滚动容器里的那个 Frame），量的是它的请求尺寸。
    返回 (最终系数, 内容宽, 内容高)。
    """
    left, top, right, bottom = work_area(hwnd)
    avail_w = max(240, right - left)
    avail_h = max(240, bottom - top)

    def apply(f):
        root.tk.call("tk", "scaling", f * BASE_DPI / 72.0)
        layout.apply(f)

    def measure():
        root.update_idletasks()
        return content.winfo_reqwidth(), content.winfo_reqheight()

    layout.reset_rows()
    apply(factor)
    width, height = measure()

    # 1) 先压行数：字号不变，只是少显示几行
    while (width > avail_w or height > avail_h) and layout.shrink_rows():
        width, height = measure()

    # 2) 还放不下就整体缩小
    while (width > avail_w or height > avail_h) and factor > min_factor:
        factor = max(min_factor, round(factor - 0.04, 4))
        apply(factor)
        width, height = measure()

    return factor, width, height


# --------------------------------------------------------------------------------------
# 图形界面
# --------------------------------------------------------------------------------------

def build_gui():
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog

    # 必须在建窗口之前声明 DPI 感知，否则窗口会先被系统按位图拉伸一次
    dpi_mode = enable_dpi_awareness()

    root = tk.Tk()
    root.title("IE 快捷方式创建工具")
    root.resizable(False, False)
    try:
        root.iconbitmap(bundled_icon_path() or resource_path("ie_icon.ico"))
    except Exception:
        pass

    # 窗口所在显示器的真实 DPI；字体按“磅”指定，会跟着 tk scaling 一起放大
    hwnd = root.winfo_id()
    state = {"path": "", "log": [], "dpi": window_dpi(hwnd),
             "factor": 1.0, "dpi_pending": False, "mode": dpi_mode}
    root.tk.call("tk", "scaling", state["dpi"] / 72.0)

    style = ttk.Style(root)
    try:
        style.theme_use("vista")
    except Exception:
        pass

    # ---------- 低分辨率适配：内容放进可滚动容器 ----------
    # 屏幕不够高 / 不够宽时，窗口会被压到工作区大小，
    # 内容通过纵向滚动条访问，不会被裁掉。
    outer = ttk.Frame(root)
    outer.pack(fill="both", expand=True)
    canvas = tk.Canvas(outer, borderwidth=0, highlightthickness=0,
                       background=style.lookup("TFrame", "background") or "#f0f0f0")
    vscroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    hscroll = ttk.Scrollbar(outer, orient="horizontal", command=canvas.xview)
    canvas.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)
    canvas.pack(side="left", fill="both", expand=True)
    inner = ttk.Frame(canvas)
    inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _on_inner_configure(_event):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _on_canvas_configure(event):
        # 画布比内容宽时把内容拉满（少留白），比内容窄时靠横向滚动条兜底
        if event.width > inner.winfo_reqwidth():
            canvas.itemconfigure(inner_id, width=event.width)

    def _on_wheel(event):
        canvas.yview_scroll(-1 * (event.delta // 120), "units")

    inner.bind("<Configure>", _on_inner_configure)
    canvas.bind("<Configure>", _on_canvas_configure)
    canvas.bind("<MouseWheel>", _on_wheel)
    inner.bind("<MouseWheel>", _on_wheel)

    # ---------- 紧凑模式：低分辨率下换更紧凑的布局 ----------
    # 完整布局在 96 DPI 下约需 740×746；放不下就换成紧凑版
    # （更窄的输入框、更少的环境信息行、单选按钮换行），
    # 尽量不用滚动就能看完整个界面。
    _wl, _wt, _wr, _wb = work_area(hwnd)
    _factor0 = state["dpi"] / BASE_DPI
    compact = ((_wr - _wl) < 780 * _factor0 or (_wb - _wt) < 770 * _factor0)
    entry_cols = 34 if compact else 52
    env_cols = 46 if compact else 68
    log_cols = 50 if compact else 72
    env_rows = 6 if compact else 10
    log_rows = 6 if compact else 9

    def log(text, kind="info"):
        state["log"].append((kind, text))
        ts = time.strftime("%H:%M:%S")
        color = {"info": "#000000", "ok": "#107C10", "warn": "#B05A00",
                 "err": "#C42B1C"}[kind]
        log_box.configure(state="normal")
        start = log_box.index("end-1c")
        log_box.insert("end", "[%s] %s\n" % (ts, text))
        end = log_box.index("end-1c")
        log_box.tag_configure(kind, foreground=color)
        log_box.tag_add(kind, start, end)
        log_box.see("end")
        log_box.configure(state="disabled")
        status_var.set(text[:120])

    # ---------- 顶部：环境检测 ----------
    env_frame = ttk.LabelFrame(inner, text="  环境检测  ", padding=8)
    env_frame.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 6))
    env_scroll = ttk.Scrollbar(env_frame, orient="vertical")
    env_scroll.grid(row=0, column=1, sticky="ns")
    env_text = tk.Text(env_frame, height=env_rows, width=env_cols, relief="flat",
                       wrap="word", bg="#f5f5f5", font=("Consolas", 9))
    env_text.grid(row=0, column=0, sticky="ew")
    env_text.configure(yscrollcommand=env_scroll.set)

    def env_detail_text():
        """环境面板的“详细说明”部分（紧凑模式下收进按钮里）。"""
        return [
            "",
            "关键点        : 参数中的 -Embedding 会让 iexplore.exe 打开真正的 IE 窗口"
            "（窗口类 IEFrame）；",
            "                去掉它，在 Windows 10 上会被 Microsoft Edge 接管。",
            "系统现状      : Windows 11 已移除 IE 桌面程序，只能用 Edge 的 IE 模式；",
            "                Windows 10 上 IE11 组件仍在时可用本方案。",
        ]

    def show_env():
        info = detect_all()
        factor = state.get("factor") or 1.0
        lines = [
            "操作系统      : %s" % info["os"],
            "显示缩放      : %d%%（%d DPI，%s）"
            % (int(round(factor * 100)), state.get("dpi", 96), state.get("mode", "")),
            "IE 程序       : %s" % (info["ie_path"] if info["ie_exists"]
                                    else "未找到 iexplore.exe"),
            "Edge 程序     : %s" % (info["edge_path"] if info["edge_exists"]
                                    else "未找到 msedge.exe"),
            "桌面目录      : %s" % info["desktop"],
            "开始菜单目录  : %s" % info["startmenu"],
        ]
        if not compact:
            lines += env_detail_text()
        env_text.configure(state="normal")
        env_text.delete("1.0", "end")
        env_text.insert("1.0", "\n".join(lines))
        env_text.configure(state="disabled")

    def show_env_detail():
        messagebox.showinfo("环境说明", "\n".join(env_detail_text()).strip())

    _env_buttons = [
        ("重新检测", show_env),
        ("打开 Edge 的 IE 模式设置", lambda: open_url("edge://settings/defaultBrowser")),
        ("打开 IE 模式诊断页", lambda: open_url("edge://compat/iediagnostic")),
    ]
    if compact:
        # 低分辨率：按钮改成两列两行，别把环境框撑得太宽
        _env_buttons.insert(1, ("查看说明", show_env_detail))
        for _i, (_txt, _cmd) in enumerate(_env_buttons):
            _row, _col = divmod(_i, 2)
            ttk.Button(env_frame, text=_txt, command=_cmd).grid(
                row=1 + _row, column=_col, sticky="w", padx=(0, 8), pady=(6, 0))
    else:
        for _i, (_txt, _cmd) in enumerate(_env_buttons):
            ttk.Button(env_frame, text=_txt, command=_cmd).grid(
                row=1, column=_i, sticky="w", pady=(6, 0))

    # ---------- 设置区 ----------
    form = ttk.LabelFrame(inner, text="  快捷方式设置  ", padding=8)
    form.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10)

    var_target = tk.StringVar(value=DEFAULT_TARGET)
    var_args = tk.StringVar(value=DEFAULT_ARGS)
    var_name = tk.StringVar(value=DEFAULT_NAME)
    var_desc = tk.StringVar(value=DEFAULT_DESC)
    var_custom = tk.StringVar(value="")
    var_loc = tk.StringVar(value="desktop")
    var_icon = tk.StringVar(value="system")

    ttk.Label(form, text="目标程序：").grid(row=0, column=0, sticky="w", pady=2)
    ttk.Entry(form, textvariable=var_target, width=entry_cols).grid(row=0, column=1, sticky="ew", pady=2)
    ttk.Button(form, text="浏览…", width=8,
               command=lambda: _pick_file(var_target)).grid(row=0, column=2, padx=(6, 0))
    ie_switch_btn = ttk.Button(form, text="用 32 位 IE", width=12,
                                command=lambda: _switch_ie_bit(var_target))
    ie_switch_btn.grid(row=0, column=3, padx=(4, 0))

    def _update_ie_switch_label(*_):
        """按钮文案跟着目标程序走：当前是 64 位就显示“用 32 位 IE”，反之亦然。"""
        bits = ie_bitness(var_target.get())
        if bits == "64":
            ie_switch_btn.configure(text="用 32 位 IE")
        elif bits == "32":
            ie_switch_btn.configure(text="用 64 位 IE")
        else:
            ie_switch_btn.configure(text="切换 IE 位数")

    _update_ie_switch_label()

    ttk.Label(form, text="启动参数：").grid(row=1, column=0, sticky="w", pady=2)
    ttk.Entry(form, textvariable=var_args, width=entry_cols).grid(row=1, column=1, sticky="ew", pady=2)

    ttk.Label(form, text="快捷方式名：").grid(row=2, column=0, sticky="w", pady=2)
    ttk.Entry(form, textvariable=var_name, width=entry_cols).grid(row=2, column=1, sticky="ew", pady=2)

    ttk.Label(form, text="描述：").grid(row=3, column=0, sticky="w", pady=2)
    ttk.Entry(form, textvariable=var_desc, width=entry_cols).grid(row=3, column=1, sticky="ew", pady=2)

    ttk.Label(form, text="创建位置：").grid(row=4, column=0, sticky="w", pady=2)
    loc_box = ttk.Frame(form)
    loc_box.grid(row=4, column=1, sticky="w")
    _loc_items = [("桌面", "desktop"), ("开始菜单", "startmenu"),
                  ("所有用户桌面", "publicdesktop"),
                  ("所有用户开始菜单", "allusersstartmenu"),
                  ("自定义目录", "custom")]
    for _i, (_txt, _val) in enumerate(_loc_items):
        if compact:
            # 低分辨率：一行放不下就换成三列两行
            _row, _col = divmod(_i, 3)
            ttk.Radiobutton(loc_box, text=_txt, value=_val,
                            variable=var_loc).grid(row=_row, column=_col,
                                                   padx=(0, 10), pady=(0, 2),
                                                   sticky="w")
        else:
            ttk.Radiobutton(loc_box, text=_txt, value=_val,
                            variable=var_loc).grid(row=0, column=_i, padx=(0, 10))
    custom_entry = ttk.Entry(form, textvariable=var_custom, width=30)
    custom_entry.grid(row=5, column=1, sticky="w", pady=2)
    pick_dir_btn = ttk.Button(form, text="选择目录…", width=10,
                               command=lambda: _pick_dir(var_custom))
    pick_dir_btn.grid(row=5, column=2, padx=(6, 0))

    ttk.Label(form, text="图标：").grid(row=6, column=0, sticky="w", pady=2)
    icon_box = ttk.Frame(form)
    icon_box.grid(row=6, column=1, sticky="w")
    if compact:
        ttk.Radiobutton(icon_box, text="使用 IE 程序自带图标", value="system",
                        variable=var_icon).grid(row=0, column=0, padx=(0, 10))
        ttk.Radiobutton(icon_box, text="使用工具内置 IE 图标", value="bundled",
                        variable=var_icon).grid(row=1, column=0, sticky="w")
    else:
        ttk.Radiobutton(icon_box, text="使用 IE 程序自带图标", value="system",
                        variable=var_icon).grid(row=0, column=0, padx=(0, 10))
        ttk.Radiobutton(icon_box, text="使用工具内置 IE 图标", value="bundled",
                        variable=var_icon).grid(row=0, column=1)

    form.columnconfigure(1, weight=1)

    # ---------- 实际生效预览（完全对应界面上的参数） ----------
    preview_frame = ttk.LabelFrame(inner, text="  实际生效的命令  ", padding=8)
    preview_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 0))
    preview_var = tk.StringVar(value="")
    ttk.Label(preview_frame, textvariable=preview_var, font=("Consolas", 9),
              justify="left", wraplength=720).grid(row=0, column=0, sticky="w")

    def collect_settings():
        """界面上的全部参数，快照成一份设置。"""
        return {
            "target": var_target.get().strip(),
            "args": var_args.get().strip(),
            "name": var_name.get(),
            "desc": var_desc.get(),
            "location": var_loc.get(),
            "custom_dir": var_custom.get().strip(),
            "icon_mode": var_icon.get(),
        }

    def _update_preview(*_):
        s = collect_settings()
        folder = resolve_target_folder(s["location"], s["custom_dir"])
        name = sanitize_name(s["name"])
        cmd = command_line(s["target"], s["args"])
        notes = []
        if _needs_admin(s["location"]):
            notes.append("“所有用户桌面 / 所有用户开始菜单”只有管理员才能写入，"
                         "权限不足时创建会提示一键以管理员身份重试。")
        if folder:
            where, _cands, more = plan_shortcut(folder, name, check_existing=False)
            text = "实际执行：%s\n保存位置：%s" % (cmd or "（请先填写目标程序）", where)
            if "internet explorer" in name.lower() and _in_rewrite_scope(folder):
                notes.append("提醒：这个位置由系统托管，名字里含“Internet Explorer”的"
                             "快捷方式会被 Windows 刷新成旧版 IE 条目"
                             "（与创建方式无关，实测原生创建也一样）；"
                             "想保留这个名字，可把“创建位置”改成自定义目录。")
            for note in more + notes:
                text += "\n说明：%s" % note
        else:
            text = ("实际执行：%s\n保存位置：（创建位置选了“自定义目录”，但还没填写目录）"
                    % (cmd or "（请先填写目标程序）"))
        preview_var.set(text)

    def _on_custom_changed(*_):
        """自定义目录里填了内容就自动切到“自定义目录”，保证填了就生效。"""
        if var_custom.get().strip() and var_loc.get() != "custom":
            var_loc.set("custom")
        _update_preview()

    for var in (var_target, var_args, var_name, var_desc, var_icon):
        var.trace_add("write", _update_preview)
    var_target.trace_add("write", _update_ie_switch_label)
    var_custom.trace_add("write", _on_custom_changed)
    var_loc.trace_add("write", _update_preview)

    # ---------- 操作区 ----------
    act = ttk.Frame(inner)
    act.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=8)

    def _do_create(settings):
        """按当前界面参数创建快捷方式，返回 (结果, 错误信息)。"""
        try:
            return create_shortcut(settings, on_progress=log), ""
        except Exception as exc:
            return None, str(exc)

    def do_create():
        settings = collect_settings()
        target = settings["target"]
        if not target:
            messagebox.showwarning("提示", "请填写目标程序路径")
            return
        if settings["custom_dir"] and settings["location"] != "custom":
            # 自定义目录已填，位置以它为准
            settings["location"] = "custom"
        if settings["location"] == "custom" and not settings["custom_dir"]:
            messagebox.showwarning("提示", "创建位置选了“自定义目录”，请填写目录或改选其它位置")
            return
        if not os.path.isfile(target):
            if not messagebox.askyesno(
                    "目标不存在",
                    "目标程序不存在：\n%s\n\n仍然按界面参数创建快捷方式吗？" % target):
                return

        result, error = _do_create(settings)
        if result is None and _needs_admin(settings["location"]):
            log("创建失败：%s" % error, "err")
            if messagebox.askyesno(
                    "需要管理员权限",
                    "“所有用户桌面 / 所有用户开始菜单”只有管理员才能写入。\n\n"
                    "是否以管理员身份重新运行本工具，并按当前界面上的参数创建？\n\n%s" % error):
                if relaunch_elevated(settings_to_cli(settings)):
                    log("已请求管理员权限，正在以管理员身份重新启动……", "ok")
                    root.destroy()
                    return
                messagebox.showerror(
                    "提权失败",
                    "没能以管理员身份重新启动。\n"
                    "请右键 ie_launcher.exe 选择“以管理员身份运行”，再点一次“创建快捷方式”。")
            return

        if result is None and ("拒绝访问" in error or "拦截" in error
                               or "PermissionError" in error):
            log("创建失败：%s" % error, "err")
            if messagebox.askyesno(
                    "该目录被安全软件拦截",
                    "当前目录禁止创建快捷方式（多半是安全软件拦截）。\n\n"
                    "是否改为保存到“开始菜单”？开始菜单不受此限制，\n"
                    "同样可以搜索“Internet Explorer”直接启动。\n\n%s" % error):
                settings["location"] = "startmenu"
                settings["custom_dir"] = ""
                var_loc.set("startmenu")
                var_custom.set("")
                result, error = _do_create(settings)

        if result is None:
            log("创建失败：%s" % error, "err")
            messagebox.showerror("创建失败", error)
            return

        state["path"] = result["path"]
        if result["existed"]:
            log("已覆盖同名快捷方式：%s" % result["path"], "warn")
        log("快捷方式已创建：%s" % result["path"], "ok")
        log("写入方式：%s" % result["method"])
        for note in result.get("notes") or []:
            log(note, "warn")

        checks = verify_shortcut(result["path"], result)
        failed = []
        for c in checks:
            mark = "√" if c["ok"] else "×"
            if c["ok"]:
                log("  %s %s：%s" % (mark, c["field"], c["actual"]), "ok")
            else:
                failed.append(c)
                log("  %s %s：实际=%s，期望=%s" % (mark, c["field"], c["actual"],
                                                c["expected"]), "warn")

        if failed:
            msg = "快捷方式已生成，但以下参数没有按界面设置写入：\n\n"
            msg += "\n".join("· %s：实际=%r，期望=%r"
                             % (c["field"], c["actual"], c["expected"]) for c in failed)
        else:
            msg = "快捷方式已创建，界面上的全部参数均已写入：\n%s" % result["path"]

        if result.get("rewritten"):
            # 系统把 IE 同名的快捷方式刷新成了旧版 IE 条目，如实告诉用户并给出选择
            msg += ("\n\n注意：Windows 已经把这个快捷方式替换成了旧版 IE 条目"
                    "（系统对 IE 同名快捷方式的刷新行为，与创建方式无关）。"
                    "点开它不会按你的参数打开页面。")
            messagebox.showwarning("系统改写了快捷方式", msg)
            if messagebox.askyesno(
                    "换个名字重建？",
                    "系统把你指定的“%s”替换成了旧版 IE 条目。\n\n"
                    "· 保留原名：链接会被系统改写，点开不是你要的页面\n"
                    "· 换个名字：自动改名为不含“Internet Explorer”的名字，"
                    "参数和功能完全正常\n\n"
                    "（想保留原名又要有功能，可以把“创建位置”改成自定义目录——"
                    "实测非系统托管的目录不会被改写）\n\n"
                    "是否换个名字重建？" % os.path.basename(result["path"])):
                new_name = safe_name_for(result["folder"],
                                         os.path.splitext(
                                             os.path.basename(result["path"]))[0])
                settings2 = dict(settings, name=new_name)
                result2, error2 = _do_create(settings2)
                if result2 is None:
                    messagebox.showerror("重建失败", error2)
                    return
                state["path"] = result2["path"]
                log("已另存为：%s" % result2["path"], "ok")
                messagebox.showinfo("创建结果", "已另存为：\n%s" % result2["path"])
                return
            messagebox.showinfo("创建结果", msg)
            return

        messagebox.showinfo("创建结果", msg)

    def do_test():
        settings = collect_settings()
        target = settings["target"]
        if not target:
            messagebox.showwarning("提示", "请填写目标程序路径")
            return
        if not os.path.isfile(target):
            messagebox.showwarning("提示", "目标程序不存在：\n%s" % target)
            return
        cmd = command_line(target, settings["args"])
        log("正在按界面参数启动：%s" % cmd)
        text, real = launch_and_classify(target, settings["args"])
        log(text, "ok" if real else "warn")

    def do_delete():
        """按界面参数检测快捷方式是否存在，存在就执行删除。"""
        settings = collect_settings()
        extra = [state["path"]] if state.get("path") else []
        targets, skipped, expected = detect_for_delete(settings, extra)

        if not targets:
            if skipped:
                messagebox.showinfo(
                    "提示",
                    "只找到系统自带的 IE 条目，为安全起见不自动删除：\n%s\n\n"
                    "如需删除请手动操作。" % "\n".join(skipped))
                log("只找到系统自带条目，已跳过：%s" % "、".join(skipped), "warn")
                return
            hint = "\n".join(expected[:5]) if expected else "（界面参数还没填完整）"
            log("没有找到可删除的快捷方式", "warn")
            messagebox.showinfo(
                "没有找到可删除的快捷方式",
                "按当前界面的“创建位置 + 快捷方式名”没有找到已存在的快捷方式。\n\n"
                "会检查这些位置：\n%s\n\n"
                "请确认“创建位置”和“快捷方式名”与实际保存的一致。" % hint)
            return

        listing = "\n".join(targets)
        if not messagebox.askyesno(
                "确认删除", "检测到以下已存在的快捷方式：\n\n%s\n\n确定删除吗？" % listing):
            log("已取消删除", "warn")
            return

        removed, failed = delete_paths(targets)
        for _path, msg in removed:
            log(msg, "ok")
        for _path, msg in failed:
            log(msg, "err")

        if state.get("path") in [p for p, _ in removed]:
            state["path"] = ""

        msg = "已删除 %d 个快捷方式：\n%s" % (
            len(removed), "\n".join(p for p, _ in removed) or "（无）")
        if skipped:
            msg += "\n\n以下系统自带条目已跳过：\n%s" % "\n".join(skipped)
        if failed:
            msg += "\n\n以下没能删掉：\n%s" % "\n".join(
                "%s —— %s" % (p, m) for p, m in failed)
        messagebox.showinfo("删除结果", msg)

    def do_open_dir():
        path = state["path"]
        if not path or not os.path.isfile(path):
            # 没创建过就按界面参数定位到那个目录
            settings = collect_settings()
            folder = resolve_target_folder(settings["location"],
                                           settings["custom_dir"])
            if folder and os.path.isdir(folder):
                subprocess.Popen(["explorer", os.path.normpath(folder)])
                log("已打开目录：%s" % folder, "ok")
            else:
                messagebox.showinfo("提示", "还没有快捷方式")
            return
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])

    ttk.Button(act, text="创建快捷方式", width=14,
               command=do_create).grid(row=0, column=0, padx=3)
    ttk.Button(act, text="测试启动", width=14,
               command=do_test).grid(row=0, column=1, padx=3)
    ttk.Button(act, text="删除快捷方式", width=14,
               command=do_delete).grid(row=0, column=2, padx=3)
    ttk.Button(act, text="打开所在文件夹", width=14,
               command=do_open_dir).grid(row=0, column=3, padx=3)

    # ---------- 日志 ----------
    log_frame = ttk.LabelFrame(inner, text="  运行日志  ", padding=6)
    log_frame.grid(row=4, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
    scroll = ttk.Scrollbar(log_frame, orient="vertical")
    log_box = tk.Text(log_frame, height=log_rows, width=log_cols, wrap="word",
                      font=("Consolas", 9), state="disabled")
    log_box.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")
    log_box.configure(yscrollcommand=scroll.set)

    status_var = tk.StringVar(value="就绪")
    ttk.Label(inner, textvariable=status_var, anchor="w",
              relief="sunken").grid(row=5, column=0, columnspan=2,
                                    sticky="ew", padx=10, pady=(0, 8))

    def _pick_file(var):
        p = filedialog.askopenfilename(title="选择目标程序",
                                        filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        if p:
            var.set(p)

    def _switch_ie_bit(var):
        """在 64 位 / 32 位 IE 启动器之间切换。"""
        path, label = ie_switch_target(var.get())
        if not os.path.isfile(path):
            log("未找到 %s IE 启动器：%s" % (label, path), "warn")
            messagebox.showwarning("提示", "未找到 %s IE 启动器：\n%s" % (label, path))
            return
        var.set(path)
        log("已切换到 %s IE 启动器：%s" % (label, path), "info")

    def _pick_dir(var):
        p = filedialog.askdirectory(title="选择保存目录")
        if p:
            var.set(p)
            var_loc.set("custom")   # 手动选了目录，就一定是自定义目录

    def open_url(url):
        try:
            edge = find_edge()
            if edge:
                subprocess.Popen([edge, url])
            else:
                os.startfile(url)
        except Exception as exc:
            log("打开失败：%s" % exc, "err")

    # ---------- DPI 适配：按真实 DPI 排版，并保证整窗落在工作区内 ----------
    layout = DpiLayout(inner)
    layout.capture(state["dpi"] / BASE_DPI)  # 记下“96 DPI 设计值”，后面按系数换算
    layout.add_elastic(env_text, env_rows, 4)   # 环境框：空间不够时先减行数
    layout.add_elastic(log_box, log_rows, 4)    # 日志框：同理

    def _relayout(dpi, center=False):
        """按指定 DPI 重新排版，并把窗口摆到工作区里。

        内容比工作区还大时，窗口压到工作区大小，多出来的部分用滚动条访问，
        不会被裁掉。
        """
        factor, need_w, need_h = fit_to_work_area(root, inner, layout,
                                                  dpi / BASE_DPI, hwnd)
        state["dpi"] = dpi
        state["factor"] = factor
        left, top, right, bottom = work_area(hwnd)
        avail_w = max(240, right - left)
        avail_h = max(240, bottom - top)
        win_w = min(need_w, avail_w)
        win_h = min(need_h, avail_h)

        # 滚动条按需出现，并从可用空间里扣掉
        need_v = need_h > win_h
        need_hbar = need_w > win_w
        vscroll.pack_forget()
        hscroll.pack_forget()
        if need_v:
            vscroll.pack(side="right", fill="y")
        if need_hbar:
            hscroll.pack(side="bottom", fill="x")
        canvas.configure(width=max(1, win_w - (vscroll.winfo_reqwidth() if need_v else 0)),
                         height=max(1, win_h - (hscroll.winfo_reqheight() if need_hbar else 0)))

        if center:
            x = left + max(0, (avail_w - win_w) // 2)
            y = top + max(0, (avail_h - win_h) // 2)
        else:
            # 拖到别的显示器后，把窗口拉回工作区里，但不动用户摆的位置
            x = min(max(root.winfo_x(), left), max(left, right - win_w))
            y = min(max(root.winfo_y(), top), max(top, bottom - win_h))
        root.geometry("%dx%d+%d+%d" % (win_w, win_h, x, y))
        return factor

    def _watch_dpi():
        """窗口被拖到缩放比例不同的显示器时，按新 DPI 重新排版。"""
        try:
            new_dpi = window_dpi(root.winfo_id())
        except Exception:
            return
        if abs(new_dpi - state["dpi"]) < 2:
            return
        _relayout(new_dpi)
        log("显示缩放变为 %d%%（%d DPI），已重新排版"
            % (int(round(new_dpi / BASE_DPI * 100)), new_dpi), "info")

    def _schedule_dpi_check():
        """<Configure> 在移动窗口时很频繁，这里做个去抖。"""
        if state.get("dpi_pending"):
            return
        state["dpi_pending"] = True

        def run():
            state["dpi_pending"] = False
            _watch_dpi()

        root.after(200, run)

    root.bind("<Configure>", lambda _event: _schedule_dpi_check())

    _relayout(state["dpi"], center=True)

    show_env()
    _update_preview()
    log("工具就绪。默认命令：\"%s\" %s" % (DEFAULT_TARGET, DEFAULT_ARGS))

    # 环境面板的文本是内容驱动的（文本超出 -height 后请求高度会变），
    # 所以内容填好之后要重量一次，窗口尺寸才和实际内容对得上。
    _relayout(state["dpi"], center=True)
    show_env()

    root.mainloop()


# --------------------------------------------------------------------------------------
# 命令行模式
# --------------------------------------------------------------------------------------

def enable_cli_output():
    """本程序以窗口模式打包（console=False），命令行输出默认无处可去。

    这里把输出接回去：优先继承父进程的重定向句柄（管道/文件，便于脚本捕获），
    其次挂接父控制台。

    输出到管道/文件时统一改用 UTF-8：中文 Windows 上默认会写成 GBK，脚本按 UTF-8
    读取就是乱码；直接连控制台时保留控制台自身编码，保证 cmd / PowerShell 显示正常。
    """
    stream = getattr(sys, "stdout", None)
    if stream is not None:
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        return True
    try:
        sys.stdout = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
        sys.stderr = os.fdopen(os.dup(2), "w", encoding="utf-8", buffering=1)
        return True
    except Exception:
        pass
    try:
        import ctypes
        if ctypes.windll.kernel32.AttachConsole(-1):
            sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
            return True
    except Exception:
        pass
    return False


def run_cli_delete(settings, has_out, opts):
    """命令行删除：按参数定位快捷方式，检测到存在才删。"""
    targets, skipped, expected = detect_for_delete(settings)
    if not targets:
        if skipped:
            text = ("只找到系统自带的 IE 条目，为安全起见不自动删除：\n%s"
                    % "\n".join(skipped))
        else:
            text = ("没有找到已存在的快捷方式。\n会检查这些位置：\n%s"
                    % ("\n".join(expected[:6]) if expected else "（参数不完整）"))
        if has_out:
            print(text)
        else:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, text, "删除快捷方式", 0x40)
            except Exception:
                pass
        return 1

    removed, failed = delete_paths(targets)

    lines = ["检测到 %d 个已存在的快捷方式：" % len(targets)]
    for path in targets:
        lines.append("  %s" % path)
    if removed:
        lines.append("已删除：")
        for path, _msg in removed:
            lines.append("  √ %s" % path)
    for path, msg in failed:
        lines.append("  × %s —— %s" % (path, msg))
    for path in skipped:
        lines.append("  ! 系统自带条目已跳过：%s" % path)
    text = "\n".join(lines)

    if has_out:
        print(text)
    else:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "删除快捷方式", 0x40)
        except Exception:
            pass
    if removed and not failed:
        return 0
    return 2 if removed else 1


def run_cli(opts):
    has_out = enable_cli_output()
    target = opts.target or find_ie() or DEFAULT_TARGET
    settings = {
        "target": target,
        "args": opts.args,
        "name": opts.name,
        "desc": opts.desc,
        "location": opts.location,
        "custom_dir": opts.custom_dir,
        "icon_mode": opts.icon,
    }

    if opts.delete:
        return run_cli_delete(settings, has_out, opts)

    if opts.safe_name and "internet explorer" in (settings.get("name") or "").lower():
        folder = resolve_target_folder(settings["location"], settings["custom_dir"])
        settings["name"] = safe_name_for(folder, settings["name"])

    try:
        result = create_shortcut(
            settings,
            on_progress=(lambda m: print(m)) if has_out else None)
    except Exception as exc:
        message = str(exc)
        if opts.fallback_startmenu and settings["location"] != "startmenu":
            print("创建失败：%s\n改存到开始菜单重试……" % message)
            settings["location"] = "startmenu"
            settings["custom_dir"] = ""
            try:
                result = create_shortcut(
                    settings,
                    on_progress=(lambda m: print(m)) if has_out else None)
            except Exception as exc2:
                message = str(exc2)
                result = None
        else:
            result = None
        if result is None:
            text = "创建失败：%s" % message
            if has_out:
                print(text)
            else:
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(None, text, "IE 快捷方式工具", 0x10)
                except Exception:
                    pass
            return 1

    checks = verify_shortcut(result["path"], result)
    failed = [c for c in checks if not c["ok"]]

    if opts.json:
        payload = dict(result)
        payload["verified"] = read_shortcut(result["path"])
        payload["checks"] = checks
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    else:
        lines = [
            "快捷方式已创建：%s" % result["path"],
            "实际执行：%s" % result["command"],
            "保存位置：%s" % result["folder"],
            "写入方式：%s" % result["method"],
            "参数核对：",
        ]
        for c in checks:
            lines.append("  %s %s：%s" % ("√" if c["ok"] else "×", c["field"], c["actual"]))
        for note in result.get("notes") or []:
            lines.append("  ! %s" % note)
        if result.get("rewritten"):
            lines.append("  ! 这个快捷方式已被系统替换成旧版 IE 条目，"
                         "点开不会按上面的参数打开页面。")
            lines.append("    换个名字重建请加 --safe-name；"
                         "想保留原名可把 --location 改成 custom 存到自定义目录。")
        text = "\n".join(lines)

    if has_out:
        print(text)
    else:
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, text, "IE 快捷方式工具", 0x40)
        except Exception:
            pass
    if opts.test:
        info, real = launch_and_classify(target, opts.args)
        if has_out:
            print(info)
        else:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, info, "启动测试", 0x40)
            except Exception:
                pass
    return 0 if not failed else 2


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("-h", "--help", "/?") for a in argv):
        print(__doc__)
        return 0

    # 先声明 DPI 感知：命令行模式弹的提示框也要跟着系统缩放清晰显示
    enable_dpi_awareness()

    parser = argparse.ArgumentParser(add_help=False, description="IE 快捷方式创建工具")
    parser.add_argument("--create", action="store_true", help="命令行创建快捷方式")
    parser.add_argument("--target", default="", help="目标程序路径")
    parser.add_argument("--args", default=DEFAULT_ARGS, help="启动参数")
    parser.add_argument("--name", default=DEFAULT_NAME, help="快捷方式名称")
    parser.add_argument("--location", default="desktop",
                        choices=["desktop", "publicdesktop", "startmenu",
                                 "allusersstartmenu", "custom"], help="创建位置")
    parser.add_argument("--custom-dir", dest="custom_dir", default="", help="自定义目录")
    parser.add_argument("--icon", default="system", choices=["system", "bundled"], help="图标来源")
    parser.add_argument("--desc", default=DEFAULT_DESC, help="快捷方式描述")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    parser.add_argument("--test", action="store_true", help="创建后立即测试启动")
    parser.add_argument("--fallback-startmenu", dest="fallback_startmenu",
                        action="store_true",
                        help="目标位置被安全软件拦截时，自动改存到开始菜单")
    parser.add_argument("--delete", action="store_true",
                        help="按参数定位并删除已存在的快捷方式（检测到存在才删）")
    parser.add_argument("--safe-name", dest="safe_name", action="store_true",
                        help="名字含 Internet Explorer 时改用不含该字样的安全名"
                             "（避免被 Windows 刷新成旧版 IE 条目）")
    opts = parser.parse_args(argv)

    if opts.create or opts.delete:
        return run_cli(opts)

    build_gui()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # 冻结后避免静默退出
        try:
            ctypes.windll.user32.MessageBoxW(None, str(exc), "IE 快捷方式工具", 0x10)
        except Exception:
            pass
        sys.exit(1)
