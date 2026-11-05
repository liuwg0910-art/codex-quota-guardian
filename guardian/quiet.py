"""Suppress console windows belonging only to this application's process tree."""
import ctypes
import os
from ctypes import wintypes as W


def hide_backend_consoles():
    if os.name != 'nt':
        return
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    user = ctypes.WinDLL('user32', use_last_error=True)
    class Entry(ctypes.Structure):
        _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD),
                    ('heap', ctypes.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                    ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD),
                    ('name', W.WCHAR*260)]
    kernel.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = W.HANDLE
    kernel.Process32FirstW.argtypes = [W.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [W.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return
    parents = {}
    entry = Entry()
    entry.size = ctypes.sizeof(entry)
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[entry.pid] = entry.parent
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    ours = {os.getpid()}
    while True:
        children = {pid for pid, parent in parents.items() if parent in ours}
        if children <= ours:
            break
        ours |= children
    callback = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
    user.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    user.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
    user.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
    def visit(hwnd, _):
        pid = W.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in ours:
            name = ctypes.create_unicode_buffer(256)
            user.GetClassNameW(hwnd, name, len(name))
            if name.value == 'ConsoleWindowClass':
                user.ShowWindow(hwnd, 0)
        return True
    user.EnumWindows.argtypes = [callback, W.LPARAM]
    user.EnumWindows(callback(visit), 0)
