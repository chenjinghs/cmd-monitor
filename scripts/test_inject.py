"""
测试 SendInput 能否向 WT 注入 Ctrl+V。
运行后有 4 秒时间，手动点击 WT terminal 使其获得焦点，
然后脚本自动发 Ctrl+V（剪贴板里需要有内容）。
"""
import ctypes
import ctypes.wintypes
import time

user32 = ctypes.windll.user32

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_V = 0x56
VK_RETURN = 0x0D


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_uint64),
        ("_pad", ctypes.c_uint64),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("union", INPUT_UNION),
    ]


def send_key(vk, up=False):
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.union.ki.wVk = vk
    if up:
        inp.union.ki.dwFlags = KEYEVENTF_KEYUP
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


print(f"INPUT struct size = {ctypes.sizeof(INPUT)} bytes (expect 40 on 64-bit)")
print("4秒后发送 Ctrl+V，请在这期间手动点击 WT terminal 给它焦点...")
for i in range(4, 0, -1):
    print(f"  {i}...")
    time.sleep(1)

fg = user32.GetForegroundWindow()
print(f"当前前台窗口 hwnd={fg}")

# 先把 hello 写入剪贴板
text = "hello from test_inject"
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
kernel32 = ctypes.windll.kernel32
kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
user32.OpenClipboard.argtypes = [ctypes.wintypes.HWND]
user32.OpenClipboard.restype = ctypes.wintypes.BOOL
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p

ok = user32.OpenClipboard(0)
if not ok:
    print(f"OpenClipboard 失败! LastError={ctypes.GetLastError()}")
    raise SystemExit(1)
user32.EmptyClipboard()
data = text.encode("utf-16-le") + b"\x00\x00"
h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
if not h:
    print(f"GlobalAlloc 失败! LastError={ctypes.GetLastError()}")
    user32.CloseClipboard()
    raise SystemExit(1)
ptr = kernel32.GlobalLock(h)
if not ptr:
    print(f"GlobalLock 失败!")
    user32.CloseClipboard()
    raise SystemExit(1)
ctypes.memmove(ptr, data, len(data))
kernel32.GlobalUnlock(h)
user32.SetClipboardData(CF_UNICODETEXT, h)
user32.CloseClipboard()
print("剪贴板已写入:", text)
time.sleep(0.1)

print("发送 Ctrl+V ...")
send_key(VK_CONTROL)
send_key(VK_V)
send_key(VK_V, up=True)
send_key(VK_CONTROL, up=True)
time.sleep(0.1)
send_key(VK_RETURN)
send_key(VK_RETURN, up=True)
print("完成，WT 里应该出现 'hello from test_inject'")
