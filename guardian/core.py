import math
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

STATE = Path(os.environ.get('CODEX_GUARDIAN_HOME', str(Path(__file__).resolve().parents[1] / 'data')))
PROMPT = '继续完成本会话中因 Codex 使用额度耗尽而中断的原任务。先核对已有进展和当前文件，避免重复执行已完成操作；遵守原有范围、权限及待确认事项。若任务已完成或需要用户输入，请停止并说明。'


def windows(result, limit_id='codex'):
    buckets = result.get('rateLimitsByLimitId')
    bucket = buckets.get(limit_id) if buckets else result.get('rateLimits')
    if not isinstance(bucket, dict):
        return []
    return [dict(bucket[k], label=k) for k in ('primary', 'secondary')
            if isinstance(bucket.get(k), dict)]


def available(result, limit_id='codex'):
    if result.get('ordinaryUsageAllowed') is False:
        return False
    buckets = result.get('rateLimitsByLimitId')
    bucket = (buckets.get(limit_id) if buckets else result.get('rateLimits')) or {}
    if bucket.get('spendControlReached') or bucket.get('rateLimitReachedType'):
        return False
    ws = windows(result, limit_id)
    # A reset timestamp alone is not proof that allowance has recovered.
    return bool(ws) and all(isinstance(w.get('usedPercent'), (int, float))
                            and math.isfinite(w['usedPercent'])
                            and 0 <= w['usedPercent'] < 100 for w in ws)


def quota_failure(turn):
    if turn.get('status') != 'failed':
        return False
    error = turn.get('error') or {}
    code = error.get('codexErrorInfo')
    if code is not None:
        return code == 'usageLimitExceeded'
    # Desktop history may omit the structured code. Match only the explicit
    # service quota message in the error field, never assistant/user content.
    message = error.get('message', '').replace('\u2019', "'").lower().strip()
    return message.startswith("you've hit your usage limit.")


class Store:
    def __init__(self, path=None):
        path = Path(path) if path else STATE / 'guardian.sqlite3'
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, title TEXT, enabled INTEGER DEFAULT 1,
            state TEXT DEFAULT 'watching', detail TEXT DEFAULT '', updated REAL);
          CREATE TABLE IF NOT EXISTS attempts(thread TEXT, turn TEXT, created REAL,
            PRIMARY KEY(thread, turn));
          CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
          CREATE TABLE IF NOT EXISTS recoveries(thread TEXT, turn TEXT, marker TEXT,
            status TEXT, detail TEXT, created REAL, updated REAL, new_turn TEXT,
            channel TEXT DEFAULT 'unknown',
            PRIMARY KEY(thread,turn));
          CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread TEXT, turn TEXT, kind TEXT, detail TEXT, created REAL);
        ''')
        if 'channel' not in {row[1] for row in self.db.execute('PRAGMA table_info(recoveries)')}:
            self.db.execute("ALTER TABLE recoveries ADD COLUMN channel TEXT DEFAULT 'unknown'")
            self.db.commit()
        if 'pause_reason' not in {row[1] for row in self.db.execute('PRAGMA table_info(tasks)')}:
            self.db.execute("ALTER TABLE tasks ADD COLUMN pause_reason TEXT DEFAULT ''")
            self.db.execute("UPDATE tasks SET pause_reason=CASE WHEN state='paused' THEN 'user' ELSE 'safety' END WHERE enabled=0")
            self.db.commit()

    def tasks(self):
        return [dict(r) for r in self.db.execute('SELECT * FROM tasks ORDER BY updated DESC')]

    def watch(self, ident, title=''):
        if not ident or len(ident) > 160:
            raise ValueError('会话 ID 无效')
        self.db.execute("INSERT INTO tasks(id,title,updated) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET enabled=1,pause_reason='',state='watching',updated=excluded.updated", (ident, title or ident, time.time()))
        self.db.commit()

    def discover(self, ident, title=''):
        """Refresh metadata without undoing user exclusions or recovery safeguards."""
        self.db.execute("INSERT INTO tasks(id,title,updated) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title",
                        (ident, title or ident, time.time()))
        self.db.commit()

    def update(self, ident, state, detail='', enabled=None):
        self.db.execute('UPDATE tasks SET state=?,detail=?,updated=?,enabled=COALESCE(?,enabled) WHERE id=?',
                        (state, detail, time.time(), enabled, ident))
        if enabled is not None:
            reason = ('user' if state == 'paused' else 'safety') if enabled == 0 else ''
            self.db.execute('UPDATE tasks SET pause_reason=? WHERE id=?', (reason, ident))
        self.db.commit()

    def restore_resolved_safeguards(self):
        rows = self.db.execute("""SELECT id FROM tasks WHERE enabled=0 AND pause_reason='safety'
            AND EXISTS(SELECT 1 FROM recoveries r WHERE r.thread=tasks.id AND r.status IN ('completed','failed','interrupted','cancelled'))
            AND NOT EXISTS(SELECT 1 FROM recoveries r WHERE r.thread=tasks.id AND r.status IN ('claimed','submitted','uncertain','confirmed_running','unconfirmed'))""").fetchall()
        for row in rows:
            self.update(row['id'], 'watching', '此前恢复结果已确认，自动恢复监控', 1)
            self.event('monitor_restored', '旧恢复结果已确认，清除程序暂停标记；用户忽略不受影响', row['id'])

    def enabled(self):
        row = self.db.execute("SELECT value FROM settings WHERE key='auto'").fetchone()
        return bool(row and row[0] == '1')

    def set_auto(self, enabled):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES('auto',?)", ('1' if enabled else '0',))
        self.db.commit()

    def can_resume(self, ident):
        row = self.db.execute('SELECT enabled FROM tasks WHERE id=?', (ident,)).fetchone()
        return self.enabled() and bool(row and row[0])

    def claim(self, ident, turn, channel='cli'):
        try:
            now = time.time()
            self.db.execute('INSERT INTO attempts VALUES(?,?,?)', (ident, turn, now))
            self.db.execute('INSERT INTO recoveries(thread,turn,marker,status,detail,created,updated,new_turn,channel) VALUES(?,?,?,?,?,?,?,?,?)',
                            (ident, turn, uuid.uuid4().hex, 'claimed', '准备恢复', now, now, None, channel))
            self.db.execute('INSERT INTO events(thread,turn,kind,detail,created) VALUES(?,?,?,?,?)',
                            (ident, turn, 'claimed', '已记录恢复意图，防止重复提交', now))
            self.db.commit()
            return True
        except sqlite3.IntegrityError:
            self.db.rollback()
            return False

    def recovery(self, ident, turn):
        row = self.db.execute('SELECT * FROM recoveries WHERE thread=? AND turn=?', (ident, turn)).fetchone()
        return dict(row) if row else None

    def pending(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM recoveries WHERE status IN ('claimed','submitted','uncertain','confirmed_running') ORDER BY created")]

    def recovery_state(self, ident, turn, status, detail, new_turn=None):
        old = self.recovery(ident, turn)
        if not old:
            return
        now = time.time()
        self.db.execute('UPDATE recoveries SET status=?,detail=?,updated=?,new_turn=COALESCE(?,new_turn) WHERE thread=? AND turn=?',
                        (status, detail, now, new_turn, ident, turn))
        if old['status'] != status:
            self.db.execute('INSERT INTO events(thread,turn,kind,detail,created) VALUES(?,?,?,?,?)',
                            (ident, turn, status, detail, now))
        self.db.commit()

    def event(self, kind, detail, ident=None, turn=None):
        self.db.execute('INSERT INTO events(thread,turn,kind,detail,created) VALUES(?,?,?,?,?)',
                        (ident, turn, kind, detail, time.time()))
        self.db.commit()

    def history(self, limit=200):
        return [dict(row) for row in self.db.execute('''SELECT e.*, t.title FROM events e
            LEFT JOIN tasks t ON e.thread=t.id ORDER BY e.id DESC LIMIT ?''', (limit,))]


def read_task(rpc, ident, desktop=None):
    if desktop is not None and hasattr(desktop, 'read_thread'):
        return desktop.read_thread(ident)
    return rpc.call('thread/read', {'threadId': ident, 'includeTurns': True})['thread']


def verify_recoveries(rpc, store, current_ids=None, now=None, desktop=None):
    """Confirm the exact submitted turn, including after a lost acknowledgment/restart."""
    protected = set()
    now = time.time() if now is None else now
    for attempt in store.pending():
        ident, failed = attempt['thread'], attempt['turn']
        protected.add(ident)
        if current_ids is not None and ident not in current_ids:
            store.recovery_state(ident, failed, 'cancelled', '任务已不在监控列表，停止恢复确认')
            continue
        if attempt['channel'] == 'desktop' and desktop is None:
            store.update(ident, 'verifying', '桌面连接暂不可用，等待重连后核对恢复结果')
            continue
        try:
            thread = read_task(rpc, ident, desktop)
            match = None
            for turn in thread.get('turns') or []:
                if turn.get('id') == failed:
                    continue
                if attempt['new_turn'] and turn.get('id') == attempt['new_turn']:
                    match = turn
                    break
                for item in turn.get('items', []):
                    # Desktop App Tools delivers follow-ups as a named tool
                    # output, whereas CLI starts use a userMessage item.
                    payload = None
                    if item.get('type') == 'userMessage':
                        payload = item.get('content', [])
                    elif (item.get('type') == 'functionCallOutput'
                          and item.get('name') == 'send_message_to_thread'
                          and item.get('namespace') == 'codex_app'):
                        payload = item.get('output')
                    if payload is not None and attempt['marker'] in json.dumps(payload, ensure_ascii=False):
                        match = turn
                        break
                if match:
                    break
            if match:
                desktop_owned = attempt['channel'] == 'desktop' or desktop is not None or any(
                    item.get('type') == 'functionCallOutput' and item.get('name') == 'send_message_to_thread'
                    and item.get('namespace') == 'codex_app' for item in match.get('items', []))
                if desktop_owned and attempt['channel'] != 'desktop':
                    store.db.execute("UPDATE recoveries SET channel='desktop' WHERE thread=? AND turn=?", (ident, failed))
                    store.db.commit()
                if not attempt['new_turn']:
                    store.recovery_state(ident, failed, attempt['status'], attempt['detail'], match['id'])
                if desktop_owned and desktop is None:
                    store.update(ident, 'verifying', '桌面连接暂不可用；等待桌面确认，不使用历史回放判断执行结果')
                    continue
                # Independent app-server disk replay cannot authoritatively report
                # a turn owned by the desktop: in-flight turns may look interrupted.
                status = desktop.turn_status(ident, match['id']) if desktop is not None else match.get('status')
                outcomes = {'inProgress': ('confirmed_running', 'running', '已确认恢复轮次正在执行'),
                            'completed': ('completed', 'completed', '已确认恢复轮次完成'),
                            'failed': ('failed', 'failed', '恢复轮次执行失败，请查看原会话'),
                            'interrupted': ('interrupted', 'stopped', '恢复轮次已中断，不重复发送')}
                if status in outcomes:
                    outcome, task_state, detail = outcomes[status]
                    store.recovery_state(ident, failed, outcome, detail, match['id'])
                    store.update(ident, task_state, detail)
                    continue
            # An unrelated manual turn or an active badge alone does not prove success.
            if now-attempt['created'] >= 120 and attempt['status'] != 'confirmed_running':
                detail = '两分钟内未确认恢复轮次；暂停该任务恢复，请核对原会话'
                store.recovery_state(ident, failed, 'unconfirmed', detail)
                store.update(ident, 'attention', detail, 0)
            else:
                store.update(ident, 'verifying', '已发出恢复请求，正在核对实际执行结果')
        except Exception:
            store.update(ident, 'verifying', '暂时无法读取恢复结果，连接恢复后继续核对')
    return protected


def discover_tasks(rpc, store):
    """Enumerate all local, non-archived interactive sessions, including later pages."""
    ids = set()
    cursor = None
    cursors = set()
    while True:
        params = {'limit': 100, 'archived': False, 'sortKey': 'updated_at', 'useStateDbOnly': True}
        if cursor:
            params['cursor'] = cursor
        page = rpc.call('thread/list', params)
        for thread in page.get('data', []):
            if thread.get('archived') or thread.get('parentThreadId'):
                continue
            ident = thread['id']
            ids.add(ident)
            store.discover(ident, thread.get('name') or (thread.get('preview') or '')[:60])
        cursor = page.get('nextCursor')
        if not cursor:
            return ids
        if cursor in cursors:
            raise RuntimeError('任务列表分页游标重复，暂停本轮检查')
        cursors.add(cursor)


def tick(rpc, store, limits, allow_resume=True, current_ids=None, desktop=None, live_threads=None):
    protected = verify_recoveries(rpc, store, current_ids, desktop=desktop)
    store.restore_resolved_safeguards()
    # Serialize dispatch/acknowledgment, not the entire execution lifetime.
    # A long-running recovered task must not block other quota-failed chats.
    queued = any(a['status'] in ('claimed', 'submitted', 'uncertain') for a in store.pending())
    for task in store.tasks():
        ident = task['id']
        if ident in protected:
            continue
        if current_ids is not None and ident not in current_ids:
            if task['enabled']:
                store.update(ident, 'inactive', '已归档或不在当前本地任务列表中')
            continue
        if not task['enabled']:
            continue
        submitted = False
        try:
            if desktop is not None:
                live = (live_threads or {}).get(ident)
                if not live:
                    store.update(ident, 'watching', '未在桌面最近任务中确认，保持监控')
                    continue
                if live.get('status') == 'active':
                    store.update(ident, 'running', '桌面任务正在执行或等待用户处理')
                    continue
            thread = read_task(rpc, ident, desktop)
            turns = thread.get('turns') or []
            if thread.get('status', {}).get('type') == 'active' or any(t['status'] == 'inProgress' for t in turns):
                store.update(ident, 'running', '正在执行或等待用户处理')
                continue
            if not turns or not quota_failure(turns[-1]):
                status = turns[-1].get('status') if turns else None
                state, detail = {
                    'completed': ('completed', '本轮执行已完成'),
                    'interrupted': ('stopped', '用户停止或中断，不自动恢复'),
                    'failed': ('failed', '非额度耗尽错误，不自动恢复'),
                }.get(status, ('watching', '等待任务执行'))
                store.update(ident, state, detail)
                continue
            if task['state'] not in ('waiting', 'ready', 'queued') and not store.recovery(ident, turns[-1]['id']):
                store.event('quota_stopped', '检测到额度耗尽中断', ident, turns[-1]['id'])
            if not available(limits):
                store.update(ident, 'waiting', '等待接口确认额度恢复')
                continue
            if not store.enabled():
                store.update(ident, 'ready', '额度可用，自动恢复已暂停')
                continue
            if not allow_resume:
                store.update(ident, 'ready', '额度已恢复，但桌面控制连接不可用；连接恢复后自动继续')
                continue
            if queued:
                store.update(ident, 'queued', '等待上一恢复请求确认启动后继续，无需等待任务执行结束')
                continue
            # The desktop bridge never resumes or starts a competing CLI runtime.
            if desktop is None:
                rpc.call('thread/resume', {'threadId': ident})
            fresh = read_task(rpc, ident, desktop)
            last = (fresh.get('turns') or [{}])[-1]
            if fresh.get('status', {}).get('type') == 'active' or last.get('id') != turns[-1]['id'] or not quota_failure(last):
                store.update(ident, 'watching', '会话状态或轮次已变化，已跳过本次恢复')
                continue
            if desktop is not None:
                latest = desktop.threads().get(ident)
                # A quota-failed desktop session stays systemError after allowance
                # resets. It is eligible only after the exact quota error and
                # unchanged latest failed turn have both been checked above.
                if not latest or latest.get('status') not in ('idle', 'notLoaded', 'systemError'):
                    status = latest.get('status') if latest else 'notListed'
                    detail = f'额度已恢复；桌面状态 {status}，等待可恢复状态'
                    if task['detail'] != detail:
                        store.event('resume_deferred', detail, ident, last['id'])
                    store.update(ident, 'ready', detail)
                    continue
            if not store.can_resume(ident):
                continue
            if not store.claim(ident, last['id'], channel='desktop' if desktop is not None else 'cli'):
                store.update(ident, 'attention', '该中断已尝试恢复；请核对会话', 0)
                continue
            # Commit intent before dispatch: a lost response never causes duplicate retries.
            store.update(ident, 'dispatching', '已记录恢复请求')
            submitted = True
            queued = True
            marker = store.recovery(ident, last['id'])['marker']
            prompt = PROMPT + f'\n\n（额度守卫自动恢复标识：{marker}）'
            if desktop is not None:
                desktop.resume(ident, prompt)
                new_turn = None
            else:
                result = rpc.call('turn/start', {'threadId': ident, 'input': [{'type': 'text', 'text': prompt}]})
                new_turn = (result.get('turn') or {}).get('id')
            store.recovery_state(ident, last['id'], 'submitted', '继续指令已提交，等待实际执行确认', new_turn)
            store.update(ident, 'verifying', '继续指令已提交，等待实际执行确认')
        except Exception as exc:
            if submitted:
                store.recovery_state(ident, last['id'], 'uncertain', '提交结果不确定，继续核对且不重复发送')
            store.update(ident, 'verifying' if submitted else 'attention', str(exc)[:500])

