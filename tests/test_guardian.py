import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from guardian.core import Store, available, tick, quota_failure, discover_tasks, verify_recoveries

LIMITS = {'rateLimitsByLimitId': {'codex': {'primary': {'usedPercent': 30}, 'secondary': {'usedPercent': 40}}}}
FAILED = {'id': 't1', 'status': 'failed', 'error': {'codexErrorInfo': 'usageLimitExceeded'}}


class Fake:
    def __init__(self, turn=None, active=False, lost=False, fresh=None):
        self.turn = copy.deepcopy(turn or FAILED)
        self.active, self.lost, self.fresh = active, lost, fresh
        self.calls = []
        self.reads = 0

    def call(self, method, params):
        self.calls.append((method, params))
        if method == 'thread/read':
            self.reads += 1
            turn = self.fresh if self.reads > 1 and self.fresh else self.turn
            return {'thread': {'status': {'type': 'active' if self.active else 'idle'}, 'turns': [turn]}}
        if method == 'turn/start' and self.lost:
            raise TimeoutError('response lost')
        return {}

    def starts(self):
        return sum(m == 'turn/start' for m, _ in self.calls)


class GuardianTests(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / 'data' / 'tests'
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.path = Path(self.temp.name) / 'state.db'
        self.store = Store(self.path)
        self.store.watch('thread1')
        self.store.set_auto(True)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_unknown_limits_fail_closed(self):
        for data in ({}, {'rateLimits': None}, {'rateLimitsByLimitId': {'other': {}}}):
            self.assertFalse(available(data))

    def test_both_windows_required(self):
        data = copy.deepcopy(LIMITS)
        data['rateLimitsByLimitId']['codex']['secondary']['usedPercent'] = 100
        data['rateLimitsByLimitId']['codex']['secondary']['resetsAt'] = 1
        self.assertFalse(available(data))

    def test_billing_block(self):
        data = copy.deepcopy(LIMITS)
        data['ordinaryUsageAllowed'] = False
        self.assertFalse(available(data))

    def test_quota_only(self):
        for error in ('contextWindowExceeded', 'sessionBudgetExceeded', 'other'):
            rpc = Fake({'id': 't1', 'status': 'failed', 'error': {'codexErrorInfo': error}})
            tick(rpc, self.store, LIMITS)
            self.assertEqual(rpc.starts(), 0)

    def test_manual_stop(self):
        rpc = Fake({'id': 't1', 'status': 'interrupted'})
        tick(rpc, self.store, LIMITS)
        self.assertEqual(rpc.starts(), 0)

    def test_resume_once_across_restarts(self):
        rpc = Fake()
        tick(rpc, self.store, LIMITS)
        self.store.db.close()
        self.store = Store(self.path)
        tick(rpc, self.store, LIMITS)
        self.assertEqual(rpc.starts(), 1)

    def test_lost_ack_never_retries(self):
        rpc = Fake(lost=True)
        tick(rpc, self.store, LIMITS)
        self.store.watch('thread1')
        tick(rpc, self.store, LIMITS)
        self.assertEqual(rpc.starts(), 1)

    def test_active_not_resumed(self):
        rpc = Fake(active=True)
        tick(rpc, self.store, LIMITS)
        self.assertEqual(rpc.starts(), 0)

    def test_paused(self):
        self.store.set_auto(False)
        rpc = Fake()
        tick(rpc, self.store, LIMITS)
        self.assertEqual(rpc.starts(), 0)

    def test_monitor_only(self):
        rpc = Fake()
        tick(rpc, self.store, LIMITS, allow_resume=False)
        self.assertEqual(rpc.starts(), 0)

    def test_changed_thread_not_resumed(self):
        rpc = Fake(fresh={'id': 't2', 'status': 'completed'})
        tick(rpc, self.store, LIMITS)
        self.assertEqual(rpc.starts(), 0)

    def test_two_processes_claim_once(self):
        second = Store(self.path)
        try:
            self.assertTrue(self.store.claim('thread1', 't1'))
            self.assertFalse(second.claim('thread1', 't1'))
        finally:
            second.db.close()

    def test_mcp_handshake(self):
        messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize'},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
        p = subprocess.run([sys.executable, 'scripts/mcp_server.py'],
                           input=''.join(json.dumps(m)+'\n' for m in messages),
                           text=True, capture_output=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)
        results = [json.loads(s) for s in p.stdout.splitlines()]
        self.assertEqual(len(results[1]['result']['tools']), 3)

    def test_automatic_discovery_paginates_and_keeps_exclusions(self):
        self.store.update('thread1', 'paused', '用户忽略', 0)
        class Pages:
            def call(self, method, params):
                self_method = method
                assert self_method == 'thread/list' and params['archived'] is False
                if not params.get('cursor'):
                    return {'data': [{'id': 'thread1', 'name': '新标题'}], 'nextCursor': 'page2'}
                return {'data': [{'id': 'thread2', 'preview': '另一项目任务'},
                                 {'id': 'archived', 'archived': True},
                                 {'id': 'child', 'parentThreadId': 'thread2'}]}
        ids = discover_tasks(Pages(), self.store)
        self.assertEqual(ids, {'thread1', 'thread2'})
        tasks = {t['id']: t for t in self.store.tasks()}
        self.assertEqual(tasks['thread1']['enabled'], 0)
        self.assertEqual(tasks['thread1']['title'], '新标题')
        self.assertEqual(tasks['thread2']['enabled'], 1)

    def test_archived_task_not_resumed(self):
        rpc = Fake()
        tick(rpc, self.store, LIMITS, current_ids=set())
        self.assertEqual(rpc.calls, [])

    def test_new_task_discovered_later(self):
        class Pages:
            def __init__(self):
                self.items = []
            def call(self, method, params):
                return {'data': self.items}
        rpc = Pages()
        self.assertEqual(discover_tasks(rpc, self.store), set())
        rpc.items = [{'id': 'new'}]
        self.assertEqual(discover_tasks(rpc, self.store), {'new'})

    def test_completed_status(self):
        rpc = Fake({'id': 't1', 'status': 'completed'})
        tick(rpc, self.store, LIMITS)
        self.assertEqual(self.store.tasks()[0]['state'], 'completed')

    def test_transient_read_failure_remains_monitored(self):
        class Offline:
            def call(self, method, params):
                raise RuntimeError('temporary disconnect')
        tick(Offline(), self.store, LIMITS)
        self.assertEqual(self.store.tasks()[0]['enabled'], 1)

    def test_desktop_resume_uses_native_channel_only(self):
        class Desktop:
            def __init__(self):
                self.sent = []
            def threads(self):
                return {'thread1': {'status': 'idle'}}
            def resume(self, ident, prompt):
                self.sent.append(ident)
        desktop = Desktop()
        rpc = Fake()
        tick(rpc, self.store, LIMITS, desktop=desktop, live_threads=desktop.threads())
        self.assertEqual(desktop.sent, ['thread1'])
        self.assertFalse(any(m in ('thread/resume', 'turn/start') for m, _ in rpc.calls))

    def test_desktop_active_status_overrides_stale_disk(self):
        rpc = Fake()
        tick(rpc, self.store, LIMITS, desktop=object(), live_threads={'thread1': {'status': 'active'}})
        self.assertEqual(rpc.calls, [])
        self.assertEqual(self.store.tasks()[0]['state'], 'running')

    def test_desktop_unlisted_never_started(self):
        rpc = Fake()
        tick(rpc, self.store, LIMITS, desktop=object(), live_threads={})
        self.assertEqual(rpc.calls, [])

    def test_desktop_becomes_active_before_send(self):
        class Desktop:
            def threads(self):
                return {'thread1': {'status': 'active'}}
            def resume(self, ident, prompt):
                raise AssertionError('must not send')
        rpc = Fake()
        tick(rpc, self.store, LIMITS, desktop=Desktop(), live_threads={'thread1': {'status': 'idle'}})
        self.assertEqual(rpc.starts(), 0)
        self.assertTrue(self.store.claim('thread1', 't1'))

    def test_desktop_lost_ack_not_retried(self):
        class Desktop:
            def __init__(self):
                self.count = 0
            def threads(self):
                return {'thread1': {'status': 'idle'}}
            def resume(self, ident, prompt):
                self.count += 1
                raise TimeoutError('lost desktop acknowledgment')
        desktop = Desktop()
        for _ in range(2):
            tick(Fake(), self.store, LIMITS, desktop=desktop, live_threads=desktop.threads())
        self.assertEqual(desktop.count, 1)

    def test_desktop_result_decoder(self):
        from guardian.desktop import unwrap
        self.assertEqual(unwrap({'content': [{'type': 'text', 'text': '{"rateLimits":{}}'}]}), {'rateLimits': {}})
        with self.assertRaises(RuntimeError):
            unwrap({'isError': True, 'content': [{'type': 'text', 'text': 'denied'}]})

    def submitted(self, outcome='inProgress', marker=True):
        self.store.claim('thread1', 't1')
        attempt = self.store.recovery('thread1', 't1')
        self.store.recovery_state('thread1', 't1', 'submitted', 'waiting')
        turn = {'id': 'new', 'status': outcome, 'items': []}
        if marker:
            turn['items'] = [{'type': 'userMessage', 'content': [{'type': 'text', 'text': attempt['marker']}]}]
        return Fake(turn), attempt

    def test_submit_not_reported_as_running(self):
        tick(Fake(), self.store, LIMITS)
        self.assertEqual(self.store.tasks()[0]['state'], 'verifying')
        self.assertEqual(self.store.recovery('thread1', 't1')['status'], 'submitted')

    def test_exact_turn_confirmation_survives_restart(self):
        rpc, _ = self.submitted()
        self.store.db.close()
        self.store = Store(self.path)
        verify_recoveries(rpc, self.store)
        self.assertEqual(self.store.recovery('thread1', 't1')['status'], 'confirmed_running')
        self.assertEqual(self.store.tasks()[0]['state'], 'running')

    def test_completed_recovery_record(self):
        rpc, _ = self.submitted('completed')
        verify_recoveries(rpc, self.store)
        self.assertFalse(self.store.pending())
        self.assertEqual(self.store.history()[0]['kind'], 'completed')

    def test_unrelated_manual_turn_is_not_success(self):
        rpc, _ = self.submitted('inProgress', marker=False)
        verify_recoveries(rpc, self.store)
        self.assertEqual(self.store.recovery('thread1', 't1')['status'], 'submitted')

    def test_confirmation_timeout_pauses_without_retry(self):
        rpc, attempt = self.submitted(marker=False)
        verify_recoveries(rpc, self.store, now=attempt['created']+121)
        self.assertEqual(self.store.recovery('thread1', 't1')['status'], 'unconfirmed')
        self.assertFalse(self.store.tasks()[0]['enabled'])
        tick(rpc, self.store, LIMITS)
        self.assertEqual(rpc.starts(), 0)

    def test_lost_ack_can_be_confirmed(self):
        rpc, _ = self.submitted('completed')
        self.store.recovery_state('thread1', 't1', 'uncertain', 'lost acknowledgment')
        verify_recoveries(rpc, self.store)
        self.assertEqual(self.store.recovery('thread1', 't1')['status'], 'completed')

    def test_failed_and_interrupted_recovery_records(self):
        for status in ('failed', 'interrupted'):
            rpc, _ = self.submitted(status)
            verify_recoveries(rpc, self.store)
            self.assertEqual(self.store.recovery('thread1', 't1')['status'], status)

    def test_multiple_tasks_queue_one_dispatch(self):
        self.store.watch('thread2')
        rpc = Fake()
        tick(rpc, self.store, LIMITS)
        self.assertEqual(rpc.starts(), 1)
        self.assertEqual(sum(t['state'] == 'queued' for t in self.store.tasks()), 1)
        tick(rpc, self.store, LIMITS)
        self.assertEqual(rpc.starts(), 1)

    def test_recovery_events_do_not_repeat_each_poll(self):
        rpc, _ = self.submitted()
        verify_recoveries(rpc, self.store)
        count = len(self.store.history())
        verify_recoveries(rpc, self.store)
        self.assertEqual(len(self.store.history()), count)

    def test_connection_backoff_and_config_refresh(self):
        from guardian.connection import DesktopConnection
        config = Path(self.temp.name)/'bridge.json'
        config.write_text('{}')
        events, calls = [], []
        now = [100.0]
        class Client:
            closed = False
            def close(self):
                self.closed = True
        def factory():
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError('offline')
            return Client()
        link = DesktopConnection(lambda *args: events.append(args), factory, config, lambda: now[0])
        self.assertIsNone(link.get())
        self.assertEqual(link.retry_in(), 2)
        self.assertIsNone(link.get())
        config.write_text('{"new":1}')
        self.assertIsNotNone(link.get())
        link.healthy()
        self.assertEqual(link.state, 'connected')
        self.assertEqual([event[0] for event in events], ['connection_lost', 'connection_config', 'connection_restored'])
        link.close()

    def test_connection_reloads_live_client(self):
        from guardian.connection import DesktopConnection
        config = Path(self.temp.name)/'bridge.json'
        config.write_text('{}')
        clients = []
        class Client:
            closed = False
            def close(self):
                self.closed = True
        def factory():
            client = Client()
            clients.append(client)
            return client
        link = DesktopConnection(lambda *args: None, factory, config)
        first = link.get()
        config.write_text('{"updated":true}')
        second = link.get()
        self.assertTrue(first.closed)
        self.assertIsNot(first, second)
        link.close()

    def test_single_instance_lock_releases(self):
        from guardian.instance import SingleInstance
        first, second = SingleInstance(Path(self.temp.name)), SingleInstance(Path(self.temp.name))
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
        finally:
            second.close()
            first.close()
        third = SingleInstance(Path(self.temp.name))
        try:
            self.assertTrue(third.acquire())
        finally:
            third.close()

    def test_pause_while_rechecking_prevents_dispatch(self):
        store = self.store
        class Desktop:
            def threads(self):
                store.set_auto(False)
                return {'thread1': {'status': 'idle'}}
            def resume(self, ident, prompt):
                raise AssertionError('paused task must not be sent')
        tick(Fake(), store, LIMITS, desktop=Desktop(), live_threads={'thread1': {'status': 'idle'}})
        self.assertFalse(store.pending())

    def test_quota_failed_desktop_system_error_resumes(self):
        class Desktop:
            def __init__(self):
                self.sent = []
            def threads(self):
                return {'thread1': {'status': 'systemError'}}
            def resume(self, ident, prompt):
                self.sent.append(ident)
        bridge = Desktop()
        tick(Fake(), self.store, LIMITS, desktop=bridge, live_threads=bridge.threads())
        self.assertEqual(bridge.sent, ['thread1'])
        self.assertEqual(self.store.tasks()[0]['state'], 'verifying')

    def test_ordinary_desktop_system_error_not_resumed(self):
        class Desktop:
            def resume(self, ident, prompt):
                raise AssertionError('ordinary failures must not resume')
        rpc = Fake({'id': 't1', 'status': 'failed', 'error': {'codexErrorInfo': 'internalServerError'}})
        tick(rpc, self.store, LIMITS, desktop=Desktop(), live_threads={'thread1': {'status': 'systemError'}})
        self.assertFalse(self.store.pending())
        self.assertEqual(self.store.tasks()[0]['state'], 'failed')

    def test_unexpected_desktop_status_has_visible_reason(self):
        class Desktop:
            def threads(self):
                return {'thread1': {'status': 'unknown'}}
            def resume(self, ident, prompt):
                raise AssertionError('unknown states must not resume')
        tick(Fake(), self.store, LIMITS, desktop=Desktop(), live_threads={'thread1': {'status': 'systemError'}})
        self.assertIn('unknown', self.store.tasks()[0]['detail'])
        self.assertEqual(self.store.history()[0]['kind'], 'resume_deferred')

    def test_desktop_live_turn_overrides_interrupted_disk_replay(self):
        rpc, _ = self.submitted('interrupted')
        class Desktop:
            def turn_status(self, ident, turn_id):
                self_ident, self_turn = ident, turn_id
                assert self_ident == 'thread1' and self_turn == 'new'
                return 'inProgress'
        verify_recoveries(rpc, self.store, desktop=Desktop())
        self.assertEqual(self.store.recovery('thread1', 't1')['status'], 'confirmed_running')
        self.assertEqual(self.store.tasks()[0]['state'], 'running')

    def test_desktop_missing_turn_does_not_infer_success_or_interruption(self):
        rpc, _ = self.submitted('interrupted')
        class Desktop:
            def turn_status(self, ident, turn_id):
                return None
        verify_recoveries(rpc, self.store, desktop=Desktop())
        self.assertEqual(self.store.recovery('thread1', 't1')['status'], 'submitted')
        self.assertEqual(self.store.tasks()[0]['state'], 'verifying')

    def test_desktop_confirmed_completion_overrides_stale_replay(self):
        rpc, _ = self.submitted('inProgress')
        class Desktop:
            def turn_status(self, ident, turn_id):
                return 'completed'
        verify_recoveries(rpc, self.store, desktop=Desktop())
        self.assertEqual(self.store.recovery('thread1', 't1')['status'], 'completed')

    def test_real_desktop_followup_tool_output_is_confirmed(self):
        rpc, attempt = self.submitted('interrupted', marker=False)
        rpc.turn['items'] = [{'type': 'functionCallOutput', 'name': 'send_message_to_thread',
                              'namespace': 'codex_app', 'output': [{'type': 'inputText', 'text': attempt['marker']}]}]
        class Desktop:
            def turn_status(self, ident, turn_id):
                return 'inProgress'
        verify_recoveries(rpc, self.store, desktop=Desktop())
        self.assertEqual(self.store.recovery('thread1', 't1')['status'], 'confirmed_running')
        self.assertEqual(self.store.recovery('thread1', 't1')['new_turn'], 'new')

    def test_unrelated_tool_output_cannot_confirm_recovery(self):
        rpc, attempt = self.submitted('completed', marker=False)
        rpc.turn['items'] = [{'type': 'functionCallOutput', 'name': 'some_other_tool',
                              'namespace': 'codex_app', 'output': attempt['marker']}]
        verify_recoveries(rpc, self.store)
        self.assertEqual(self.store.recovery('thread1', 't1')['status'], 'submitted')

    def test_desktop_recovery_during_disconnect_never_uses_disk_terminal_state(self):
        rpc, _ = self.submitted('interrupted')
        self.store.db.execute("UPDATE recoveries SET channel='desktop'")
        self.store.db.commit()
        verify_recoveries(rpc, self.store, desktop=None)
        self.assertEqual(self.store.recovery('thread1', 't1')['status'], 'submitted')
        self.assertEqual(self.store.tasks()[0]['state'], 'verifying')

    def test_legacy_tool_followup_marks_desktop_channel_while_offline(self):
        rpc, attempt = self.submitted('interrupted', marker=False)
        self.store.db.execute("UPDATE recoveries SET channel='unknown'")
        self.store.db.commit()
        rpc.turn['items'] = [{'type': 'functionCallOutput', 'name': 'send_message_to_thread',
                              'namespace': 'codex_app', 'output': attempt['marker']}]
        verify_recoveries(rpc, self.store, desktop=None, now=attempt['created']+300)
        row=self.store.recovery('thread1', 't1')
        self.assertEqual(row['channel'], 'desktop')
        self.assertEqual(row['status'], 'submitted')

    def test_desktop_channel_survives_guardian_restart(self):
        self.store.claim('thread1', 't1', channel='desktop')
        self.store.db.close()
        self.store=Store(self.path)
        self.assertEqual(self.store.recovery('thread1', 't1')['channel'], 'desktop')

    def test_offline_desktop_attempt_does_not_expire_before_history_is_visible(self):
        self.store.claim('thread1', 't1', channel='desktop')
        attempt=self.store.recovery('thread1','t1')
        rpc=Fake()
        verify_recoveries(rpc,self.store,desktop=None,now=attempt['created']+1000)
        self.assertEqual(rpc.calls,[])
        self.assertEqual(self.store.recovery('thread1','t1')['status'],'claimed')

    def test_completed_recovery_clears_safety_pause(self):
        self.store.claim('thread1','t1',channel='desktop')
        self.store.update('thread1','attention','old safeguard',0)
        self.store.recovery_state('thread1','t1','completed','confirmed')
        self.store.update('thread1','completed','confirmed')
        self.store.restore_resolved_safeguards()
        self.assertTrue(self.store.tasks()[0]['enabled'])

    def test_user_exclusion_survives_recovery_completion(self):
        self.store.claim('thread1','t1',channel='desktop')
        self.store.update('thread1','paused','user ignored',0)
        self.store.recovery_state('thread1','t1','completed','confirmed')
        self.store.update('thread1','completed','confirmed')
        self.store.restore_resolved_safeguards()
        self.assertFalse(self.store.tasks()[0]['enabled'])
        self.assertEqual(self.store.tasks()[0]['pause_reason'],'user')

    def test_unconfirmed_safeguard_remains_until_result_known(self):
        self.store.claim('thread1','t1')
        self.store.recovery_state('thread1','t1','unconfirmed','unknown')
        self.store.update('thread1','attention','unknown',0)
        self.store.restore_resolved_safeguards()
        self.assertFalse(self.store.tasks()[0]['enabled'])

    def test_later_quota_failure_resumes_after_previous_safeguard_resolved(self):
        self.store.claim('thread1','old-failure',channel='desktop')
        self.store.update('thread1','attention','old bug',0)
        self.store.recovery_state('thread1','old-failure','completed','confirmed')
        self.store.update('thread1','completed','confirmed')
        class Desktop:
            def __init__(self):self.sent=[]
            def threads(self):return {'thread1':{'status':'systemError'}}
            def resume(self,ident,prompt):self.sent.append(ident)
        bridge=Desktop()
        tick(Fake(),self.store,LIMITS,desktop=bridge,live_threads=bridge.threads())
        self.assertEqual(bridge.sent,['thread1'])
        self.assertTrue(self.store.tasks()[0]['enabled'])
        self.assertEqual(self.store.recovery('thread1','t1')['status'],'submitted')

    def test_chatgpt_start_and_restart_launch_overlay(self):
        from guardian.autostart import DesktopLifecycle
        launched=[]
        watcher=DesktopLifecycle(lambda:False,lambda:launched.append(True))
        watcher.step(set())
        self.assertFalse(launched)
        watcher.step({100})
        self.assertEqual(len(launched),1)
        watcher.step(set())
        watcher.step({200})
        self.assertEqual(len(launched),2)

    def test_chatgpt_renderer_churn_does_not_repeat_launch(self):
        from guardian.autostart import DesktopLifecycle
        now=[100.0];running=[False];launched=[]
        watcher=DesktopLifecycle(lambda:running[0],lambda:launched.append(True),lambda:now[0])
        watcher.step({100});running[0]=True;watcher.step({100})
        # A deliberate close does not trigger repeated relaunch in the same session.
        running[0]=False;now[0]+=30;watcher.step({100})
        self.assertEqual(len(launched),1)

    def test_existing_overlay_is_shown_on_chatgpt_restart(self):
        from guardian.autostart import DesktopLifecycle
        shown=[];launched=[]
        watcher=DesktopLifecycle(lambda:True,lambda:launched.append(True),show=lambda:shown.append(True))
        watcher.step({100});watcher.step({100});watcher.step(set());watcher.step({200})
        self.assertEqual(len(shown),2)
        self.assertFalse(launched)

    def test_failed_overlay_start_retries(self):
        from guardian.autostart import DesktopLifecycle
        now=[100.0];launched=[]
        watcher=DesktopLifecycle(lambda:False,lambda:launched.append(True),lambda:now[0])
        watcher.step({100});watcher.step({100})
        self.assertEqual(len(launched),1)
        now[0]+=6;watcher.step({100})
        self.assertEqual(len(launched),2)

    def test_failed_show_retries_in_same_session(self):
        from guardian.autostart import DesktopLifecycle
        shown=[]
        def show():
            shown.append(True)
            if len(shown)==1:
                raise OSError('temporary failure')
        watcher=DesktopLifecycle(lambda:True,lambda:None,show=show)
        with self.assertRaises(OSError):
            watcher.step({100})
        watcher.step({100})
        watcher.step({100})
        self.assertEqual(len(shown),2)

    def test_desktop_detection_excludes_cli_and_renderers(self):
        from guardian.autostart import desktop_roots
        root=Path(self.temp.name)/'desktop';(root/'resources').mkdir(parents=True)
        (root/'resources'/'app.asar').write_bytes(b'')
        processes=[(1,0,'ChatGPT.exe',str(root/'ChatGPT.exe')),
                   (2,1,'ChatGPT.exe',str(root/'ChatGPT.exe')),
                   (3,0,'codex.exe',str(Path(self.temp.name)/'bin'/'codex.exe'))]
        self.assertEqual(desktop_roots(processes),{1})

    def test_confirmed_running_task_does_not_block_other_quota_tasks(self):
        self.store.claim('thread1','previous-failure',channel='desktop')
        attempt=self.store.recovery('thread1','previous-failure')
        self.store.recovery_state('thread1','previous-failure','confirmed_running','running','restored-turn')
        self.store.watch('thread2')
        class Rpc:
            def call(self,method,params):
                assert method=='thread/read'
                if params['threadId']=='thread1':
                    return {'thread':{'status':{'type':'notLoaded'},'turns':[{'id':'restored-turn','status':'interrupted','items':[{'type':'functionCallOutput','name':'send_message_to_thread','namespace':'codex_app','output':attempt['marker']}]}]}}
                return {'thread':{'status':{'type':'notLoaded'},'turns':[copy.deepcopy(FAILED)]}}
        class Desktop:
            def __init__(self):self.sent=[]
            def turn_status(self,ident,turn_id):return 'inProgress'
            def threads(self):return {'thread1':{'status':'active'},'thread2':{'status':'systemError'}}
            def resume(self,ident,prompt):self.sent.append(ident)
        bridge=Desktop()
        tick(Rpc(),self.store,LIMITS,desktop=bridge,live_threads=bridge.threads())
        self.assertEqual(bridge.sent,['thread2'])
        self.assertEqual(self.store.recovery('thread1','previous-failure')['status'],'confirmed_running')
        self.assertEqual(self.store.recovery('thread2','t1')['status'],'submitted')

    def test_unacknowledged_recovery_still_blocks_next_dispatch(self):
        self.store.claim('thread1','old',channel='desktop')
        self.store.recovery_state('thread1','old','submitted','pending')
        self.store.watch('thread2')
        tick(Fake(),self.store,LIMITS,allow_resume=True)
        self.assertEqual(next(t['state'] for t in self.store.tasks() if t['id']=='thread2'),'queued')
        self.assertIsNone(self.store.recovery('thread2','t1'))


if __name__ == '__main__':
    unittest.main()
