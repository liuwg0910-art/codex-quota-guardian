"""Read the native Codex window bounds without focusing or modifying it."""
import ctypes
import os
from pathlib import Path
from ctypes import wintypes


def codex_bounds():
    if os.name != 'nt':
        return None
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsIconic.argtypes = [wintypes.HWND]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    found = []
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if not user.IsWindowVisible(hwnd) or user.IsIconic(hwnd):
            return True
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return True
        try:
            name = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(name))
            if (kernel.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size))
                and os.path.basename(name.value).lower() in ('codex.exe', 'chatgpt.exe')
                and (Path(name.value).parent / 'resources' / 'app.asar').is_file()):
                rect = wintypes.RECT()
                if user.GetWindowRect(hwnd, ctypes.byref(rect)) and rect.right-rect.left > 400 and rect.bottom-rect.top > 250:
                    found.append((rect.left, rect.top, rect.right, rect.bottom))
        finally:
            kernel.CloseHandle(handle)
        return True
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.EnumWindows(callback(visit), 0)
    return found[0] if found else None


def dock_position(bounds, width, height, screen_width):
    left, top, right, _ = bounds
    x = max(0, min((left+right-width)//2, screen_width-width))
    # Maximized windows have no space above: tuck into the top edge instead.
    return x, max(4, top-height-5)
