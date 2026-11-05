"""Reconnect only to executor-provided endpoints; never guess or scan desktop pipes."""
import time
from .desktop import CONFIG, Desktop


class DesktopConnection:
    def __init__(self, record, factory=Desktop, config=CONFIG, clock=time.monotonic):
        self.record, self.factory, self.config, self.clock = record, factory, config, clock
        self.client = None
        self.signature = self.config_signature()
        self.next_retry = 0
        self.failures = 0
        self.error = None
        self.state = None

    def config_signature(self):
        try:
            stat = self.config.stat()
            return stat.st_mtime_ns, stat.st_size
        except OSError:
            return None

    def get(self):
        signature = self.config_signature()
        if signature != self.signature:
            self.close()
            self.signature = signature
            self.next_retry = 0
            self.failures = 0
            self.record('connection_config', '检测到 Codex 提供的新连接配置，立即重新连接')
        if self.client:
            return self.client
        if self.clock() < self.next_retry:
            return None
        try:
            self.client = self.factory()
            return self.client
        except Exception as exc:
            self.failed(exc)
            return None

    def healthy(self):
        if self.state != 'connected':
            self.record('connection_restored', '桌面连接已验证，恢复任务状态与额度检查')
        self.state = 'connected'
        self.failures = 0
        self.error = None

    def failed(self, error):
        self.close()
        self.failures += 1
        self.error = str(error)[:500]
        self.next_retry = self.clock()+min(30, 2**min(self.failures, 5))
        if self.state != 'disconnected':
            self.record('connection_lost', '桌面连接暂不可用，自动重试；恢复请求不会重复发送')
        self.state = 'disconnected'

    def retry_in(self):
        return max(0, self.next_retry-self.clock())

    def close(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
            finally:
                self.client = None
