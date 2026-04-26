"""Windows Terminal UI 树诊断脚本

在 WT 的某个 tab 里跑这个脚本(继承 WT_SESSION 环境变量),
打印出 WT 主窗口的控件树和 tab 列表的 AutomationId/Name,帮我们判断
windows_term._find_wt_tab_index() 该匹配哪个字段。
"""

import os
import sys

print(f"WT_SESSION = {os.environ.get('WT_SESSION', '(empty)')}")
print()

try:
    import uiautomation as auto
except ImportError:
    print("uiautomation 未安装")
    sys.exit(1)

try:
    import psutil
except ImportError:
    print("psutil 未安装")
    sys.exit(1)

# 找父进程链里的 WindowsTerminal.exe
proc = psutil.Process(os.getpid())
wt_pid = 0
for a in [proc, *proc.parents()]:
    name = (a.name() or "").lower()
    print(f"  ancestor pid={a.pid} name={name}")
    if name in ("windowsterminal.exe", "wt.exe"):
        wt_pid = a.pid
        break

if not wt_pid:
    print("\n没找到 WindowsTerminal.exe 父进程 — 你可能不是在 WT 里跑的?")
    sys.exit(1)

print(f"\nWT pid = {wt_pid}")
print()

# 找 WT 的主窗口
wt_window = None
for w in auto.GetRootControl().GetChildren():
    try:
        if w.ProcessId == wt_pid and w.ControlType == auto.ControlType.WindowControl:
            wt_window = w
            break
    except Exception:
        continue

if wt_window is None:
    print("没找到 WT 的顶层 WindowControl")
    sys.exit(1)

print(f"WT window: ClassName={wt_window.ClassName!r} Name={wt_window.Name!r} hwnd={wt_window.NativeWindowHandle}")
print()

# 递归打印整棵树(深度限制),重点是带 Tab 的
def walk(ctrl, depth=0, max_depth=6):
    if depth > max_depth:
        return
    try:
        ct = ctrl.ControlTypeName
        name = ctrl.Name
        aid = ctrl.AutomationId
        cn = ctrl.ClassName
    except Exception:
        return
    indent = "  " * depth
    interesting = "Tab" in ct or "Tab" in (cn or "")
    marker = "★ " if interesting else "  "
    print(f"{indent}{marker}{ct}  Name={name!r}  AutomationId={aid!r}  ClassName={cn!r}")
    try:
        for child in ctrl.GetChildren():
            walk(child, depth + 1, max_depth)
    except Exception:
        pass

walk(wt_window)
