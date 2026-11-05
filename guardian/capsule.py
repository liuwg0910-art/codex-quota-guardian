"""Compact, animated canvas UI; no external assets or GUI dependencies."""
import math
import os
import time
import tkinter as tk

BG = '#171b22'
MUTED = '#8691a3'
FG = '#e9edf4'
MINT = '#87dfbc'
AMBER = '#f2bd78'
PALETTE = ('#72edc4', '#71cfff', '#b09aff', '#ef9fd5', '#ffcd8a')
KEY = '#010203'


def blend(a, b, t):
    aa, bb = [int(a[i:i+2], 16) for i in (1, 3, 5)], [int(b[i:i+2], 16) for i in (1, 3, 5)]
    return '#' + ''.join(f'{round(x+(y-x)*t):02x}' for x, y in zip(aa, bb))


def spectrum(position):
    phase = position % len(PALETTE)
    index = int(phase)
    return blend(PALETTE[index], PALETTE[(index+1) % len(PALETTE)], phase-index)


def countdown(reset, now=None):
    if not isinstance(reset, (int, float)) or not math.isfinite(reset):
        return '--:--'
    seconds = max(0, math.ceil(reset-(time.time() if now is None else now)))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}'


class Capsule(tk.Canvas):
    compact = (300, 40)
    expanded = (340, 158)

    def __init__(self, root, on_menu):
        super().__init__(root, bg=KEY, highlightthickness=0, bd=0)
        self.root, self.on_menu = root, on_menu
        self.width, self.height = self.compact
        self.open = False
        self.progress = 0.0
        self.quotas = []
        self.displayed = [0.0, 0.0]
        self.count = self.running = self.waiting = 0
        self.status = '正在连接'
        self.info = '自动发现任务 · 每 30 秒更新'
        self.warning = False
        self.last = time.monotonic()
        self.pack(fill='both', expand=True)
        root.configure(bg=KEY)
        if os.name == 'nt':
            root.attributes('-transparentcolor', KEY)
        self.bind('<Enter>', lambda e: self.set_open(True))
        self.bind('<Leave>', self.leave)
        self.bind('<ButtonRelease-1>', self.click)
        self.animate()

    def set_open(self, value):
        self.open = value

    def leave(self, event):
        self.after(180, self.check_leave)

    def check_leave(self):
        try:
            x, y = self.winfo_pointerx()-self.root.winfo_x(), self.winfo_pointery()-self.root.winfo_y()
            if not (0 <= x < self.width and 0 <= y < self.height):
                self.open = False
        except tk.TclError:
            pass

    def click(self, event):
        if event.x >= self.width-38 and event.y <= 40:
            self.on_menu(event)

    def update_state(self, quotas, tasks, auto=False, monitor_only=False, error=None):
        if error:
            self.status = '连接重试中'
            self.info = '连接暂不可用，正在自动重试'
            self.warning = True
            return
        self.quotas = []
        for w in quotas[:2]:
            used = w.get('usedPercent')
            if not isinstance(used, (int, float)) or not math.isfinite(used):
                continue
            duration = w.get('windowDurationMins')
            label = '每周' if duration == 10080 else f'{duration//60:g}h' if isinstance(duration, (int, float)) and duration >= 60 else '额度'
            reset = w.get('resetsAt')
            when = time.strftime('%m-%d %H:%M', time.localtime(reset)) if reset else '未知'
            self.quotas.append((label, max(0, min(100, 100-used)), when, reset, duration == 300))
        monitored = [t for t in tasks if t['enabled'] and t['state'] != 'inactive']
        self.count = len(monitored)
        self.running = sum(t['state'] == 'running' for t in monitored)
        self.waiting = sum(t['state'] in ('waiting', 'ready', 'queued', 'verifying') for t in monitored)
        self.warning = bool(self.quotas and min(q[1] for q in self.quotas) <= 20)
        self.status = '确认恢复中' if any(t['state'] == 'verifying' for t in monitored) else '等待恢复' if self.waiting else '正在执行' if self.running else '监控中'
        self.info = '桌面连接重试中 · 仅监控' if monitor_only else '桌面已连接 · 自动恢复开启' if auto else '桌面已连接 · 自动恢复暂停'
        if not self.quotas:
            self.status = '暂无额度数据'

    def rounded(self, x1, y1, x2, y2, radius, fill, outline=''):
        self.create_polygon(x1+radius, y1, x2-radius, y1, x2, y1, x2, y1+radius,
                            x2, y2-radius, x2, y2, x2-radius, y2, x1+radius, y2,
                            x1, y2, x1, y2-radius, x1, y1+radius, x1, y1,
                            smooth=True, splinesteps=24, fill=fill, outline=outline)

    def text(self, x, y, value, color=FG, size=9, anchor='w', bold=False):
        self.create_text(x, y, text=value, fill=color, anchor=anchor,
                         font=('Microsoft YaHei UI', size, 'bold' if bold else 'normal'))

    def animate(self):
        now = time.monotonic()
        dt = min(now-self.last, .1)
        self.last = now
        target = 1.0 if self.open else 0.0
        self.progress += (target-self.progress)*min(1, dt*14)
        if abs(target-self.progress) < .003:
            self.progress = target
        eased = self.progress*self.progress*(3-2*self.progress)
        width = round(300+40*eased)
        height = round(40+118*eased)
        if (width, height) != (self.width, self.height):
            center = self.root.winfo_x()+self.width/2
            self.width, self.height = width, height
            self.root.geometry(f'{width}x{height}+{max(0,round(center-width/2))}+{self.root.winfo_y()}')
        for i, q in enumerate(self.quotas):
            self.displayed[i] += (q[1]-self.displayed[i])*min(1, dt*9)
        self.draw(now)
        self.after(50, self.animate)

    def draw(self, now):
        self.delete('all')
        w, h = self.width, self.height
        # Four-second luminance breath; hue drifts more slowly through the palette.
        pulse = (1-math.cos(now*math.tau/4))/2
        hue = spectrum(now/9)
        accent = AMBER if self.warning else hue
        self.rounded(0, 0, w, h, 20, blend('#202431', hue, .18+.24*pulse))
        self.rounded(1, 1, w-1, h-1, 19, blend('#303344', hue, .25+.32*pulse))
        self.rounded(2, 2, w-2, h-2, 18, BG)
        # Subtle colored edge segments give the capsule a multicolor aura.
        for i in range(40):
            x1 = 20+(w-40)*i/40
            x2 = 20+(w-40)*(i+1)/40
            tint = spectrum(i/12+now/12)
            self.create_line(x1, 2, x2+1, 2, fill=blend(BG, tint, .3+.4*pulse), width=2)
            self.create_line(x1, h-3, x2+1, h-3, fill=blend(BG, tint, .12+.2*pulse), width=1)
        radius = 10+3*pulse
        self.create_oval(23-radius, 20-radius, 23+radius, 20+radius,
                         fill=blend(BG, accent, .08+.12*pulse), outline='')
        self.create_oval(15, 12, 31, 28, fill=blend(BG, accent, .12+.16*pulse), outline='')
        # A quiet three-stroke activity mark replaces the oversized icon.
        for i in range(3):
            size = 4+(3 if i == 1 else 0)+(math.sin(now*3+i)*2 if self.running else 3*pulse)
            x = 18+i*4
            self.create_line(x, 20-size/2, x, 20+size/2, fill=blend(BG, accent, .55+.45*pulse), width=2, capstyle='round')
        if self.quotas:
            for i, q in enumerate(self.quotas):
                x = 45 if i == 0 else 140
                timer = q[4]
                tint = PALETTE[0] if i == 0 else PALETTE[2]
                self.text(x, 18, countdown(q[3]) if timer else q[0], blend(MUTED, tint, .55), 8)
                self.text(x+(52 if timer else 29), 18, f'{self.displayed[i]:.0f}%', FG, 9, bold=True)
                track = 84 if i == 0 else 64
                self.rounded(x, 28, x+track, 30, 1, '#2c3440')
                if self.displayed[i] > 1:
                    length = track*self.displayed[i]/100
                    for j in range(max(1, int(length/2))):
                        offset = j*2
                        tint = spectrum(i*2+offset/60)
                        # A broad, low-contrast shimmer moves along the actual quota fill.
                        shine = (math.sin(offset/16-now*1.4)+1)/2
                        self.create_line(x+offset, 29, x+min(length,offset+2), 29,
                                         fill=blend(tint, '#ffffff', .05+.2*shine), width=2)
        else:
            self.text(45, 20, self.status, MUTED, 9)
        self.create_line(w-91, 13, w-91, 27, fill='#343c47')
        self.text(w-80, 19, f'{self.count} 任务', '#e6c4a0', 8)
        for x in (w-26, w-22, w-18):
            self.create_oval(x, 18, x+2, 20, fill=MUTED, outline='')
        if self.progress > .82:
            self.create_line(18, 42, w-18, 42, fill='#2b333e')
            self.text(18, 60, self.status, AMBER if self.warning else PALETTE[0], 9, bold=True)
            self.text(w-18, 60, f'运行 {self.running}  /  等待 {self.waiting}', MUTED, 8, 'e')
            for i, q in enumerate(self.quotas):
                self.text(18, 82+i*19, f'{q[0]}额度重置', MUTED, 8)
                self.text(w-18, 82+i*19, q[2], '#c7cfda', 8, 'e')
            self.text(18, 133, self.info, MUTED, 8)
