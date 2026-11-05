"""An OS-released lock prevents duplicate monitors even after crashes."""
import hashlib
import os


class SingleInstance:
    def __init__(self, directory):
        self.handle = None
        self.file = None
        self.acquired = False
        self.directory = directory.resolve()

    def acquire(self):
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.CreateMutexW.restype = wintypes.HANDLE
            name = 'Local\\CodexQuotaGuardian-'+hashlib.sha256(str(self.directory).lower().encode()).hexdigest()[:24]
            ctypes.set_last_error(0)
            self.handle = kernel.CreateMutexW(None, False, name)
            if not self.handle:
                raise OSError(ctypes.get_last_error(), '无法创建额度条单实例锁')
            self.acquired = ctypes.get_last_error() != 183
        else:
            import fcntl
            self.directory.mkdir(parents=True, exist_ok=True)
            self.file = open(self.directory / 'instance.lock', 'a')
            try:
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.acquired = True
            except BlockingIOError:
                self.acquired = False
        return self.acquired

    def close(self):
        if self.handle:
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL('kernel32')
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle(self.handle)
            self.handle = None
        if self.file:
            self.file.close()
            self.file = None
