"""Codex app-server JSON-RPC transport. Never reads credentials."""
import json
import os
import queue
import shutil
import subprocess
import threading
from pathlib import Path


def command():
    override = os.environ.get('CODEX_GUARDIAN_CODEX')
    if override:
        return [override]
    exe = shutil.which('codex.exe')
    if exe:
        return [exe]
    npm = Path(os.environ.get('APPDATA', '')) / 'npm/node_modules/@openai/codex/bin/codex.js'
    if npm.is_file() and shutil.which('node'):
        return [shutil.which('node'), str(npm)]
    exe = shutil.which('codex')
    if exe and not exe.endswith(('.cmd', '.ps1', '.bat')):
        return [exe]
    raise RuntimeError('找不到 Codex CLI，请安装 CLI 或设置 CODEX_GUARDIAN_CODEX 为可执行文件路径。')


class Rpc:
    def __init__(self, standalone=False, process_args=None, mcp=False, process_env=None):
        self.mcp = mcp
        self.pending = {}
        self.lock = threading.Lock()
        self.serial = 0
        self.events = queue.Queue()
        args = ['app-server'] + ([] if standalone else ['proxy'])
        startup = None
        if os.name == 'nt':
            startup = subprocess.STARTUPINFO()
            startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = subprocess.SW_HIDE
        self.proc = subprocess.Popen(process_args or command() + args, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding='utf-8', bufsize=1,
                                     env=process_env,
                                     startupinfo=startup,
                                     creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            params = {'clientInfo': {'name': 'quota_guardian', 'version': '0.1.0'}}
            if mcp:
                params.update(protocolVersion='2024-11-05', capabilities={})
            self.call('initialize', params)
            self.send({'method': 'notifications/initialized' if mcp else 'initialized'})
        except Exception:
            self.close()
            raise

    def send(self, msg):
        if self.mcp:
            msg = dict(msg, jsonrpc='2.0')
        with self.lock:
            self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + '\n')
            self.proc.stdin.flush()

    def call(self, method, params=None, timeout=25):
        with self.lock:
            self.serial += 1
            ident = self.serial
            box = queue.Queue()
            self.pending[ident] = box
        try:
            self.send({'id': ident, 'method': method, 'params': params or {}})
            try:
                msg = box.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError(f'{method} 超时；若为提交任务，请核对会话后再重新启用。')
            if 'error' in msg:
                raise RuntimeError(str(msg['error']))
            return msg.get('result', {})
        finally:
            with self.lock:
                self.pending.pop(ident, None)

    def _read(self):
        try:
            for line in self.proc.stdout:
                msg = json.loads(line)
                if 'method' in msg:
                    # Never grant an approval on the user's behalf.
                    if 'id' in msg:
                        self.send({'id': msg['id'], 'error': {'code': -32000,
                                   'message': 'Quota Guardian cannot answer approvals. Use Codex.'}})
                    self.events.put(msg)
                else:
                    with self.lock:
                        box = self.pending.get(msg.get('id'))
                    if box:
                        box.put(msg)
        finally:
            with self.lock:
                boxes = list(self.pending.values())
            for box in boxes:
                box.put({'error': 'Codex 连接已断开；检查共享 daemon 是否运行。'})

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        self.reader.join(timeout=2)
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                stream.close()
            except (OSError, ValueError):
                pass

