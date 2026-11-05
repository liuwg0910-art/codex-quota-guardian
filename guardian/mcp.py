"""Small stdio MCP server. Persistent scheduler lives in the separate desktop process."""
import json
import sys
from .core import Store

TOOLS = [
    {'name': 'guardian_status', 'description': '读取额度守卫自动恢复开关和监控任务状态，不返回账户凭据。', 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'guardian_watch', 'description': '可选：恢复指定会话的监控。悬浮条已自动发现本机未归档任务，不需要手动添加。需要独立悬浮条保持运行。', 'inputSchema': {'type': 'object', 'properties': {'thread_id': {'type': 'string'}, 'title': {'type': 'string'}}, 'required': ['thread_id'], 'additionalProperties': False}},
    {'name': 'guardian_pause', 'description': '暂停全部自动恢复。', 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
]


def main():
    # Desktop supplies real caller context to its plugin process on a new session.
    import os
    if os.environ.get('CODEX_APP_TOOLS_PIPE_PATH') and os.environ.get('CODEX_THREAD_ID'):
        try:
            from .desktop import save_executor_bridge
            save_executor_bridge()
        except Exception:
            pass
    try:
        from .autostart import ensure_watcher
        ensure_watcher()
    except Exception:
        pass
    store = Store()
    for line in sys.stdin:
        msg = {}
        try:
            msg = json.loads(line)
            if 'id' not in msg:
                continue
            method = msg.get('method')
            if method == 'initialize':
                result = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'quota-guardian', 'version': '0.4.1'}}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': TOOLS}
            elif method == 'tools/call':
                if os.environ.get('CODEX_APP_TOOLS_PIPE_PATH') and os.environ.get('CODEX_THREAD_ID'):
                    try:
                        from .desktop import save_executor_bridge
                        save_executor_bridge()
                    except Exception:
                        pass
                name = msg['params']['name']
                args = msg['params'].get('arguments') or {}
                if name == 'guardian_watch':
                    store.watch(args['thread_id'], args.get('title', ''))
                elif name == 'guardian_pause':
                    store.set_auto(False)
                elif name != 'guardian_status':
                    raise ValueError('未知工具')
                result = {'content': [{'type': 'text', 'text': json.dumps({'auto': store.enabled(), 'tasks': store.tasks(), 'recoveryHistory': store.history(30), 'pendingRecoveries': len(store.pending()), 'note': '悬浮条必须保持运行；全局恢复开关在悬浮条中开启。'}, ensure_ascii=False)}]}
            else:
                print(json.dumps({'id': msg['id'], 'error': {'code': -32601, 'message': 'Unknown method'}}), flush=True)
                continue
            print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': result}, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({'jsonrpc': '2.0', 'id': msg.get('id'), 'error': {'code': -32603, 'message': str(exc)}}), flush=True)


if __name__ == '__main__':
    main()
