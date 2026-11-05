import argparse
import json
import os
import queue
import threading
import time
from .core import Store, windows, tick, discover_tasks
from .rpc import Rpc


def worker(out, stop, standalone=False):
    rpc = None
    desktop = None
    store = Store()
    from .connection import DesktopConnection
    connection = DesktopConnection(store.event)
    try:
        while not stop.is_set():
            try:
                if os.name == 'nt' and not standalone:
                    if rpc is None:
                        rpc = Rpc(True)
                    desktop = connection.get()
                    live = None
                    if desktop:
                        try:
                            live = desktop.threads()
                            limits = desktop.limits()
                            connection.healthy()
                        except Exception as exc:
                            connection.failed(exc)
                            desktop = None
                    if desktop is None:
                        limits = rpc.call('account/rateLimits/read')
                    current_ids = discover_tasks(rpc, store)
                    if desktop is not None:
                        tick(rpc, store, limits, allow_resume=True,
                             current_ids=current_ids, desktop=desktop, live_threads=live)
                    else:
                        # The standalone reader cannot read desktop paginated history.
                        # Preserve recovery records and expose the actual connection block.
                        for task in store.tasks():
                            if task['enabled'] and task['id'] in current_ids:
                                store.update(task['id'], 'attention', '桌面连接失效，无法自动续跑；等待 Codex 刷新真实连接地址')
                    out.put({'limits': limits, 'tasks': store.tasks(), 'auto': store.enabled(),
                             'monitorOnly': desktop is None, 'desktopError': connection.error})
                    from .core import STATE
                    read_errors = [t for t in store.tasks() if t['enabled'] and t['state'] == 'attention']
                    health = {'desktopConnected': desktop is not None, 'autoResumeEnabled': store.enabled(),
                              'recoveryChecksHealthy': not read_errors,
                              'taskCheckErrors': [{'title': t['title'], 'detail': t['detail']} for t in read_errors],
                              'taskCount': len(current_ids), 'checkedAt': time.time(),
                              'desktopError': connection.error, 'retryInSeconds': round(connection.retry_in()),
                              'pendingRecoveries': len(store.pending()), 'recentEvents': store.history(5)}
                    (STATE / 'health.json').write_text(json.dumps(health, ensure_ascii=False), encoding='utf-8')
                    interval = 5 if store.pending() else 30
                    if desktop is None:
                        interval = min(interval, max(1, connection.retry_in()))
                    # Also watch trusted configuration changes promptly after desktop restart.
                    signature = connection.signature
                    until = time.monotonic()+interval
                    while not stop.is_set() and time.monotonic() < until:
                        if connection.config_signature() != signature:
                            break
                        stop.wait(min(1, max(0, until-time.monotonic())))
                    continue
                shared = not standalone
                if rpc is None:
                    try:
                        rpc = Rpc(standalone)
                    except Exception:
                        if standalone:
                            raise
                        rpc = Rpc(True)
                        shared = False
                limits = rpc.call('account/rateLimits/read')
                current_ids = discover_tasks(rpc, store)
                tick(rpc, store, limits, allow_resume=shared or standalone, current_ids=current_ids)
                out.put({'limits': limits, 'tasks': store.tasks(), 'auto': store.enabled(),
                         'monitorOnly': not shared and not standalone})
                if not shared and not standalone:
                    rpc.close()
                    rpc = None
            except Exception as exc:
                out.put({'error': str(exc)})
                try:
                    from .core import STATE
                    failure_health = {'desktopConnected': False, 'autoResumeEnabled': store.enabled(),
                                      'checkedAt': time.time(), 'error': str(exc)[:500],
                                      'pendingRecoveries': len(store.pending())}
                    (STATE / 'health.json').write_text(json.dumps(failure_health, ensure_ascii=False), encoding='utf-8')
                except OSError:
                    pass
                if rpc:
                    rpc.close()
                    rpc = None
            stop.wait(30)
    finally:
        connection.close()
        if rpc:
            rpc.close()
        store.db.close()


def gui(demo=False, standalone=False):
    import os
    import sys
    from pathlib import Path
    if os.name == 'nt':
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass
    # Some Windows venvs do not propagate Tcl/Tk's library search path.
    tcl_root = Path(sys.base_prefix) / 'tcl'
    for env, folder in (('TCL_LIBRARY', 'tcl8.6'), ('TK_LIBRARY', 'tk8.6')):
        if (tcl_root / folder).is_dir():
            os.environ.setdefault(env, str(tcl_root / folder))
    import tkinter as tk
    root = tk.Tk()
    root.title('Codex 额度守卫')
    root.overrideredirect(True)
    root.geometry('300x40+100+8')
    root.attributes('-topmost', True)
    if os.name == 'nt':
        root.attributes('-toolwindow', True)
    root.configure(bg='#383e46')
    root.resizable(False, False)
    store = Store()
    stop = threading.Event()
    out = queue.Queue()
    from .capsule import Capsule
    auto = tk.BooleanVar(value=store.enabled() and not demo)
    def toggle():
        if not demo:
            store.set_auto(auto.get())
    docked = tk.BooleanVar(value=True)
    drag = [0, 0]
    def popup(event):
        menu.tk_popup(event.x_root, event.y_root)
    view = Capsule(root, popup)
    def begin_drag(event):
        drag[:] = [event.x_root-root.winfo_x(), event.y_root-root.winfo_y()]
    def move(event):
        if event.x < view.width-38:
            docked.set(False)
            root.geometry(f'+{max(0,event.x_root-drag[0])}+{max(0,event.y_root-drag[1])}')
    view.bind('<ButtonPress-1>', begin_drag)
    view.bind('<B1-Motion>', move)
    view.bind('<Double-Button-1>', lambda e: docked.set(True))
    view.bind('<Button-3>', popup)
    show_seen = [None]
    def follow():
        from .dock import codex_bounds, dock_position
        from .core import STATE
        try:
            stamp = (STATE / 'show-request').stat().st_mtime_ns
            if stamp != show_seen[0]:
                show_seen[0] = stamp
                root.deiconify()
                root.attributes('-topmost', True)
                root.lift()
                root.geometry(f'+{max(4,(root.winfo_screenwidth()-view.width)//2)}+8')
                docked.set(True)
        except OSError:
            pass
        if docked.get():
            bounds = codex_bounds()
            if bounds:
                x, y = dock_position(bounds, view.width, 40, root.winfo_screenwidth())
                root.geometry(f'{view.width}x{view.height}+{x}+{y}')
        root.after(500, follow)

    def quiet_backend():
        from .quiet import hide_backend_consoles
        hide_backend_consoles()
        root.after(1000, quiet_backend)

    def manage():
        win = tk.Toplevel(root)
        win.title('自动监控任务')
        win.geometry('700x370')
        listing = tk.Listbox(win, width=90)
        listing.pack(fill='both', expand=True, padx=10, pady=10)
        rows = []
        def refresh():
            nonlocal rows
            rows = store.tasks()
            listing.delete(0, 'end')
            for row in rows:
                states = {'running': '运行中', 'completed': '本轮完成', 'stopped': '已停止',
                          'failed': '其他错误', 'waiting': '等待额度', 'ready': '等待恢复',
                          'watching': '监控中', 'inactive': '不在列表', 'paused': '已忽略',
                          'attention': '需要处理', 'dispatching': '正在恢复',
                          'verifying': '确认恢复中', 'queued': '排队恢复'}
                listing.insert('end', f"{'●' if row['enabled'] else '○'} {row['title']} | {states.get(row['state'],row['state'])} | {row['detail']}")
        tk.Label(win, text='自动发现本机未归档任务，每 30 秒更新；可忽略不需要监控的任务。').pack()
        controls = tk.Frame(win)
        controls.pack(pady=10)
        def remove():
            if listing.curselection() and not demo:
                store.update(rows[listing.curselection()[0]]['id'], 'paused', '用户忽略', 0)
                refresh()
        def restore():
            if listing.curselection() and not demo:
                row = rows[listing.curselection()[0]]
                store.watch(row['id'], row['title'])
                refresh()
        tk.Button(controls, text='忽略所选', command=remove).pack(side='left', padx=5)
        tk.Button(controls, text='恢复监控', command=restore).pack(side='left', padx=5)
        def refresh_loop():
            if win.winfo_exists():
                selection = listing.curselection()
                selected_id = rows[selection[0]]['id'] if selection else None
                refresh()
                for i, row in enumerate(rows):
                    if row['id'] == selected_id:
                        listing.selection_set(i)
                        break
                win.after(2000, refresh_loop)
        refresh_loop()

    def recovery_history():
        from tkinter import ttk
        win = tk.Toplevel(root)
        win.title('恢复与连接记录')
        win.geometry('860x410')
        tk.Label(win, text='提交指令后会核对真实执行轮次；仅有运行标记不代表恢复成功。').pack(pady=8)
        columns = ('time', 'task', 'status', 'detail')
        table = ttk.Treeview(win, columns=columns, show='headings')
        for col, title, width in zip(columns, ('时间', '任务', '结果', '说明'), (130, 210, 110, 370)):
            table.heading(col, text=title)
            table.column(col, width=width, stretch=col in ('task', 'detail'))
        scroll = ttk.Scrollbar(win, orient='vertical', command=table.yview)
        table.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        table.pack(fill='both', expand=True, padx=8, pady=5)
        labels = {'quota_stopped': '额度中断', 'claimed': '准备恢复', 'submitted': '已提交待确认',
                  'uncertain': '提交结果待核对', 'confirmed_running': '确认正在执行',
                  'completed': '恢复轮次完成', 'failed': '恢复执行失败', 'interrupted': '恢复被中断',
                  'unconfirmed': '未能确认', 'cancelled': '停止确认',
                  'connection_lost': '连接断开', 'connection_restored': '连接恢复', 'connection_config': '连接配置更新'}
        labels['resume_deferred'] = '等待恢复条件'
        labels['monitor_restored'] = '恢复自动监控'
        def refresh():
            if not win.winfo_exists():
                return
            selection = table.selection()
            table.delete(*table.get_children())
            for row in store.history():
                table.insert('', 'end', iid=str(row['id']), values=(
                    time.strftime('%m-%d %H:%M:%S', time.localtime(row['created'])),
                    row['title'] or ('桌面连接' if not row['thread'] else row['thread']),
                    labels.get(row['kind'], row['kind']), row['detail']))
            if selection and table.exists(selection[0]):
                table.selection_set(selection)
            win.after(3000, refresh)
        refresh()

    menu = tk.Menu(root, tearoff=False, bg='#202327', fg='#dce1e7', activebackground='#363d46')
    menu.add_command(label='查看自动监控任务…', command=manage)
    menu.add_command(label='恢复与连接记录…', command=recovery_history)
    menu.add_checkbutton(label='额度恢复后自动继续', variable=auto, command=toggle)
    menu.add_checkbutton(label='吸附到 Codex 窗口上方', variable=docked)
    menu.add_separator()
    menu.add_command(label='退出', command=lambda: close())
    def render():
        try:
            while True:
                msg = out.get_nowait()
                if 'error' in msg:
                    view.update_state([], [], error=msg['error'])
                else:
                    view.update_state(windows(msg['limits']), msg['tasks'], msg['auto'], msg.get('monitorOnly', False))
        except queue.Empty:
            pass
        root.after(500, render)
    if demo:
        out.put({'limits': {'rateLimits': {'primary': {'usedPercent': 68, 'windowDurationMins': 300, 'resetsAt': time.time()+3600}, 'secondary': {'usedPercent': 32, 'windowDurationMins': 10080, 'resetsAt': time.time()+86400}}}, 'tasks': [], 'auto': False})
    else:
        threading.Thread(target=worker, args=(out, stop, standalone), daemon=True).start()
    def close():
        stop.set()
        store.db.close()
        root.destroy()
    root.protocol('WM_DELETE_WINDOW', close)
    root.bind('<Escape>', lambda e: close())
    follow()
    quiet_backend()
    render()
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description='Codex 额度守卫')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--standalone', action='store_true', help='独立 app-server，仅用于独立 CLI 会话')
    parser.add_argument('--check', action='store_true', help='只读连接诊断')
    parser.add_argument('--connect-desktop', action='store_true', help='保存 Codex 执行环境提供的桌面连接并只读验证')
    args = parser.parse_args()
    if args.connect_desktop:
        from .desktop import save_executor_bridge, Desktop
        save_executor_bridge()
        bridge = Desktop()
        try:
            print(json.dumps({'desktopConnected': True, 'localTasks': len(bridge.threads()),
                              'usageReadable': bool(bridge.limits())}, ensure_ascii=False))
        finally:
            bridge.close()
    elif args.check:
        rpc = Rpc(args.standalone)
        try:
            print(json.dumps(rpc.call('account/rateLimits/read'), ensure_ascii=False, indent=2))
        finally:
            rpc.close()
    else:
        if args.demo:
            gui(True, args.standalone)
            return
        from .core import STATE
        from .instance import SingleInstance
        instance = SingleInstance(STATE)
        try:
            if instance.acquire():
                gui(False, args.standalone)
        finally:
            instance.close()


if __name__ == '__main__':
    main()
