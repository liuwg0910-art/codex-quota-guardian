"""Windows bridge through the installed first-party Codex App Tools MCP.

Use the executor-provided pipe and caller identity. No credentials, private
protocol, synthetic identities, UI clicks, or changes to desktop settings.
"""
import json
import os
import shutil
import tempfile
from pathlib import Path
from .core import STATE
from .rpc import Rpc

CONFIG = STATE / 'desktop-bridge.json'
REQUIRED = {'list_threads', 'read_thread', 'get_usage_limits', 'send_message_to_thread'}


def save_executor_bridge():
    """Capture actual executor context so Explorer launches can reconnect."""
    pipe = os.environ.get('CODEX_APP_TOOLS_PIPE_PATH')
    caller = os.environ.get('CODEX_THREAD_ID')
    if not pipe or not caller:
        raise RuntimeError('需要从 Codex 执行环境配置桌面连接，缺少真实管道或调用会话信息。')
    root = Path.home() / '.codex/plugins/cache/openai-bundled/codex-app-tools'
    candidates = list(root.glob('*/server.mjs'))
    if not candidates:
        raise RuntimeError('未找到已安装的 Codex App Tools 插件。')
    def version(path):
        return tuple(int(v) if v.isdigit() else 0 for v in path.parent.name.split('.'))
    server = max(candidates, key=version)
    node = os.environ.get('CODEX_MCP_NODE_PATH') or shutil.which('node')
    if not node or not Path(node).is_file():
        raise RuntimeError('未找到桌面连接需要的 Node 运行时。')
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    config = {'pipe': pipe, 'callerThread': caller, 'server': str(server), 'node': node}
    try:
        if json.loads(CONFIG.read_text(encoding='utf-8')) == config:
            return config
    except (OSError, ValueError):
        pass
    with tempfile.NamedTemporaryFile(mode='w', dir=CONFIG.parent, suffix='.json',
                                     encoding='utf-8', delete=False) as handle:
        json.dump(config, handle, ensure_ascii=False)
        temp_path = handle.name
    os.replace(temp_path, CONFIG)
    return config


def unwrap(result):
    if result.get('isError'):
        raise RuntimeError('桌面接口拒绝请求：' + ' '.join(c.get('text', '') for c in result.get('content', []))[:300])
    for content in result.get('content', []):
        if content.get('type') == 'text':
            try:
                value = json.loads(content['text'])
            except (ValueError, KeyError):
                continue
            if isinstance(value, dict):
                return value
    raise RuntimeError('桌面接口返回了无法识别的数据。')


def runtime_paths(config):
    """Repair removed executable paths after app/plugin upgrades; keep endpoint identity."""
    config = dict(config)
    if not Path(config['server']).is_file():
        candidates = list((Path.home()/'.codex/plugins/cache/openai-bundled/codex-app-tools').glob('*/server.mjs'))
        if not candidates:
            raise RuntimeError('桌面插件已移除，等待 Codex 刷新连接配置')
        config['server'] = str(max(candidates, key=lambda p: tuple(int(v) if v.isdigit() else 0 for v in p.parent.name.split('.'))))
    if not Path(config['node']).is_file():
        node = shutil.which('node.exe') or shutil.which('node')
        if not node:
            candidates = list((Path.home()/'AppData/Local/OpenAI/Codex/runtimes/cua_node').glob('*/bin/node.exe'))
            node = str(max(candidates, key=lambda p: p.stat().st_mtime)) if candidates else None
        if not node:
            raise RuntimeError('桌面运行时已更新，等待 Codex 刷新连接配置')
        config['node'] = node
    return config


class Desktop:
    def __init__(self):
        if not CONFIG.is_file():
            raise RuntimeError('桌面连接尚未配置')
        config = runtime_paths(json.loads(CONFIG.read_text(encoding='utf-8')))
        self.caller = config['callerThread']
        # This address is supplied by Codex, not a guessed/discovered pipe.
        child_env = dict(os.environ, CODEX_APP_TOOLS_PIPE_PATH=config['pipe'])
        self.rpc = Rpc(process_args=[config['node'], config['server']], mcp=True, process_env=child_env)
        try:
            self.tools = {t['name'] for t in self.rpc.call('tools/list').get('tools', [])}
            if not REQUIRED <= self.tools:
                raise RuntimeError('当前桌面插件不提供所需的任务恢复接口。')
        except Exception:
            self.close()
            raise

    def tool(self, name, arguments, raw=False):
        result = self.rpc.call('tools/call', {'name': name, 'arguments': arguments,
                               '_meta': {'threadId': self.caller}}, timeout=45)
        if raw:
            if result.get('isError'):
                unwrap(result)
            return result
        return unwrap(result)

    def threads(self):
        result = self.tool('list_threads', {'limit': 50})
        if result.get('unavailableHosts'):
            raise RuntimeError('部分桌面执行主机不可用，暂停恢复检查。')
        return {t['id']: t for t in result.get('pinnedThreads', [])+result.get('threads', [])
                if t.get('kind') == 'codex' and t.get('hostId', 'local') == 'local'}

    def limits(self):
        return self.tool('get_usage_limits', {})

    def resume(self, ident, prompt):
        # Route to the desktop's own runtime; never create a competing app-server turn.
        return self.tool('send_message_to_thread', {'threadId': ident, 'hostId': 'local', 'prompt': prompt}, raw=True)

    def turn_status(self, ident, turn_id):
        """Read actual desktop-owned turn state; disk replay may synthesize interrupted."""
        page = self.tool('read_thread', {'threadId': ident, 'hostId': 'local',
                         'turnLimit': 5, 'includeOutputs': False, 'maxOutputCharsPerItem': 100})
        for turn in page.get('turns', []):
            if turn.get('id') == turn_id:
                return turn.get('status')
        return None

    def read_thread(self, ident):
        """Read recent turns from their owning desktop, including paginated history."""
        try:
            page = self.tool('read_thread', {'threadId': ident, 'hostId': 'local',
                             'turnLimit': 5, 'includeOutputs': True, 'maxOutputCharsPerItem': 20000})
        except RuntimeError as exc:
            if 'read size limit' not in str(exc):
                raise
            page = self.tool('read_thread', {'threadId': ident, 'hostId': 'local',
                             'turnLimit': 1, 'includeOutputs': False, 'maxOutputCharsPerItem': 1000})
        thread = dict(page['thread'])
        turns = page.get('turns', [])
        if page.get('page', {}).get('order') == 'newest_first':
            turns = list(reversed(turns))
        thread['turns'] = turns
        return thread

    def close(self):
        self.rpc.close()
