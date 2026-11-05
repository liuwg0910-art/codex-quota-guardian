"""Watch the desktop application lifecycle, independent of model turns."""
import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from .core import STATE
from .instance import SingleInstance

ROOT = Path(__file__).resolve().parents[1]
CONFIG = STATE / 'autostart.json'


def enabled():
    try:
        return json.loads(CONFIG.read_text(encoding='utf-8-sig')).get('enabled') is True
    except (OSError, ValueError):
        return False


def desktop_roots(processes):
    candidates = {pid: parent for pid, parent, name, path in processes
                  if name.lower() in ('chatgpt.exe', 'codex.exe') and path
                  and (Path(path).parent / 'resources' / 'app.asar').is_file()}
    # Chromium renderers churn constantly; only the actual application roots
    # define a restart. CLI app-server binaries lack resources/app.asar.
    return {pid for pid, parent in candidates.items() if parent not in candidates}


def desktop_pids():
    if os.name != 'nt':
        return set()
    from ctypes import wintypes as W
    class Entry(ctypes.Structure):
        _fields_ = [('size', W.DWORD), ('usage', W.DWORD), ('pid', W.DWORD),
                    ('heap', ctypes.c_size_t), ('module', W.DWORD), ('threads', W.DWORD),
                    ('parent', W.DWORD), ('priority', W.LONG), ('flags', W.DWORD), ('name', W.WCHAR*260)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = W.HANDLE
    kernel.Process32FirstW.argtypes = [W.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [W.HANDLE, ctypes.POINTER(Entry)]
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    kernel.CloseHandle.argtypes = [W.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError('无法读取桌面进程列表')
    result = []
    entry = Entry()
    entry.size = ctypes.sizeof(entry)
    try:
        ok = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.name.lower() in ('chatgpt.exe', 'codex.exe'):
                handle = kernel.OpenProcess(0x1000, False, entry.pid)
                if handle:
                    try:
                        path = ctypes.create_unicode_buffer(32768)
                        length = W.DWORD(len(path))
                        if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                            result.append((entry.pid, entry.parent, entry.name, path.value))
                    finally:
                        kernel.CloseHandle(handle)
            ok = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return desktop_roots(result)


def lock_held(directory):
    lock = SingleInstance(directory)
    try:
        return not lock.acquire()
    finally:
        lock.close()


def spawn(script, independent=False):
    pythonw = Path(sys.base_prefix) / 'pythonw.exe'
    if not pythonw.is_file():
        raise RuntimeError('找不到原生无控制台 Python 运行时')
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = subprocess.SW_HIDE
    if independent:
        # The Windows task service owns the installed watcher, independently
        # of the desktop app's child-process job. Explorer is a legacy fallback.
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        command = ("$ErrorActionPreference='Stop'; if (Get-ScheduledTask -TaskName 'CodexQuotaGuardianWatcher' -ErrorAction SilentlyContinue) { Start-ScheduledTask -TaskName 'CodexQuotaGuardianWatcher' } else { "
                   + '(New-Object -ComObject Shell.Application).ShellExecute('
                   + quote(pythonw) + ',' + quote('"' + str(ROOT / script) + '"')
                   + ',' + quote(ROOT) + ",'open',0) }")
        return subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
                              startupinfo=startup, check=True, timeout=20)
    return subprocess.Popen([str(pythonw), str(ROOT / script)], cwd=ROOT,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startup)


def request_show():
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / 'show-request').write_text(str(time.time_ns()), encoding='ascii')


class DesktopLifecycle:
    def __init__(self, running, launch, clock=time.monotonic, show=None):
        self.running, self.launch, self.clock = running, launch, clock
        self.show = show or (lambda: None)
        self.previous = set()
        self.pending = False
        self.next_try = 0

    def step(self, pids):
        pids = set(pids)
        new_session = bool(pids and not pids.intersection(self.previous))
        if new_session:
            self.pending = True
            self.next_try = 0
        self.previous = pids
        if not pids:
            self.pending = False
            return
        if self.running():
            if self.pending:
                self.show()
            self.pending = False
        elif self.pending and self.clock() >= self.next_try:
            self.next_try = self.clock()+5
            self.launch()
        # A deliberate close stays closed for the rest of this desktop session;
        # a subsequent application restart re-arms it.


def ensure_watcher():
    if os.name == 'nt' and enabled() and not lock_held(STATE / 'startup-watcher'):
        spawn('watch.pyw', independent=True)


def main():
    lock = SingleInstance(STATE / 'startup-watcher')
    try:
        if not lock.acquire() or not enabled():
            return
        print('启动监听已运行', flush=True)
        def launch():
            spawn('launch.pyw')
            print('检测到 ChatGPT 启动，正在拉起悬浮条', flush=True)
        watcher = DesktopLifecycle(lambda: lock_held(STATE), launch, show=request_show)
        while enabled():
            try:
                watcher.step(desktop_pids())
            except Exception as exc:
                print(f'启动监听将重试：{type(exc).__name__}: {exc}', flush=True)
            time.sleep(2)
    finally:
        lock.close()
