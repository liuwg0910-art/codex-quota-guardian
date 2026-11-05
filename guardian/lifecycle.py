"""Publish current executor context from lifecycle hooks without model work."""
import json
import os
import time
from .core import STATE
from .desktop import save_executor_bridge


def refresh(payload=None):
    payload = payload or {}
    # session_id is supplied by Codex's hook input, never invented.
    caller = os.environ.get('CODEX_THREAD_ID') or payload.get('session_id')
    if caller and not os.environ.get('CODEX_THREAD_ID'):
        os.environ['CODEX_THREAD_ID'] = caller
    result = {'checkedAt': time.time(), 'event': payload.get('hook_event_name')}
    try:
        save_executor_bridge()
        from .autostart import ensure_watcher
        ensure_watcher()
        result['published'] = True
    except Exception as exc:
        result.update(published=False, error=str(exc))
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE/'lifecycle-health.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    return result
