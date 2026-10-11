"""Shared UI toolkit, networking and alert engine for the AdminRouter desktop apps."""
import concurrent.futures
import ipaddress
import json
import os
import re
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
import tkinter as tk

DISCOVERY_PORT = 39872
POLL_SECONDS = 2
MAX_LOCK_SECONDS = 600
COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')
FONT = 'Segoe UI'

BASE_THEME = {
    'bg': '#0b1220', 'card': '#131c2e', 'border': '#26324a', 'input': '#0b1220',
    'text': '#e6ebf5', 'muted': '#8d9ab3', 'good': '#34d399', 'bad': '#f87171', 'warn': '#fbbf24',
}
ACCENTS = {
    'client': ('#3b82f6', '#2563eb'),
    'pro': ('#8b5cf6', '#7c3aed'),
    'diag': ('#14b8a6', '#0d9488'),
    'dash': ('#6366f1', '#4f46e5'),
}


def palette(kind='client'):
    theme = dict(BASE_THEME)
    theme['accent'], theme['accent_hover'] = ACCENTS[kind]
    return theme


# --------------------------------------------------------------------------
# Files and window helpers
# --------------------------------------------------------------------------
def resource_path(name):
    """Find a bundled file, both from source and from a PyInstaller exe."""
    base = getattr(sys, '_MEIPASS', None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


def apply_window_icon(window):
    """Use the bundled .ico for the title bar and taskbar (replaces the default Tk feather)."""
    ico = resource_path('app.ico')
    try:
        if os.name == 'nt' and os.path.exists(ico):
            window.iconbitmap(default=ico)
    except tk.TclError:
        pass


def center_window(window, width, height, lift=0):
    window.update_idletasks()
    height = min(height, window.winfo_screenheight() - 70)
    x = max(0, (window.winfo_screenwidth() - width) // 2)
    y = max(0, (window.winfo_screenheight() - height) // 2 - lift)
    window.geometry(f'{width}x{height}+{x}+{y}')


def config_dir(app_name):
    base = os.environ.get('APPDATA') or str(Path.home() / '.config')
    path = Path(base) / app_name
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def load_json(path, default):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def save_json(path, data):
    try:
        Path(path).write_text(json.dumps(data, indent=2), encoding='utf-8')
    except OSError:
        pass


def load_client_id(app_name='AdminRouterClient'):
    """A stable id so the server recognises this PC between runs."""
    path = config_dir(app_name) / 'client.json'
    data = load_json(path, {})
    value = str(data.get('client_id', ''))
    if re.fullmatch(r'[A-Za-z0-9-]{8,64}', value):
        return value
    value = uuid.uuid4().hex
    data['client_id'] = value
    save_json(path, data)
    return value


def device_name():
    return socket.gethostname().encode('ascii', 'replace').decode('ascii')


def play_alert_sound():
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:  # noqa: BLE001 - not on Windows
        pass


# --------------------------------------------------------------------------
# Networking
# --------------------------------------------------------------------------
def get_local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(('8.8.8.8', 80))
        return sock.getsockname()[0]
    except OSError:
        return '127.0.0.1'
    finally:
        sock.close()


def local_candidates():
    address = get_local_ip()
    if address.startswith('127.'):
        return ['127.0.0.1']
    return [str(h) for h in ipaddress.ip_network(f'{address}/24', strict=False).hosts()]


def probe_host(host, timeout=0.6):
    """Ask a host's discovery beacon whether AdminRouter lives there."""
    try:
        with urllib.request.urlopen(f'http://{host}:{DISCOVERY_PORT}/info/creditngtgtu', timeout=timeout) as response:
            info = json.loads(response.read())
        if info.get('service') == 'AdminRouter':
            return {'host': host, 'port': int(info.get('port')), 'username': str(info.get('username', '')),
                    'remote_alerting': bool(info.get('remote_alerting', False))}
    except (OSError, ValueError, TypeError, urllib.error.URLError):
        pass
    return None


def find_server():
    with concurrent.futures.ThreadPoolExecutor(max_workers=48) as pool:
        for found in pool.map(probe_host, local_candidates()):
            if found:
                return found
    return None


def split_address(address):
    address = address.strip().replace('http://', '').rstrip('/')
    host, _, port = address.partition(':')
    return host, (int(port) if port.isdigit() else None)


def http_request(url, headers=None, timeout=5):
    """Returns (status, body, milliseconds). Network failures raise OSError."""
    started = time.monotonic()
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, body = response.status, response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read().decode('utf-8', 'replace')
    return status, body, int((time.monotonic() - started) * 1000)


# --------------------------------------------------------------------------
# Widgets
# --------------------------------------------------------------------------
def rounded_rect(canvas, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return canvas.create_polygon(pts, smooth=True, **kw)


class RoundButton(tk.Canvas):
    """Flat rounded button with hover state. style: primary | ghost | danger."""

    def __init__(self, parent, text, command, theme, style='primary', width=150, height=40, size=10):
        super().__init__(parent, width=width, height=height, bg=parent.cget('bg'), highlightthickness=0, bd=0)
        self.theme, self.command, self.style = theme, command, style
        self.w, self.h = width, height
        self.enabled, self.hover = True, False
        self.shape = rounded_rect(self, 1, 1, width - 1, height - 1, 12, fill='', outline='')
        self.label = self.create_text(width / 2, height / 2, text=text, font=(FONT, size, 'bold'))
        self.bind('<Enter>', lambda e: self._set_hover(True))
        self.bind('<Leave>', lambda e: self._set_hover(False))
        self.bind('<ButtonRelease-1>', self._click)
        self._paint()

    def _colors(self):
        t = self.theme
        if not self.enabled:
            return t['border'], t['muted']
        if self.style == 'ghost':
            return (t['border'] if self.hover else t['card']), t['text']
        if self.style == 'danger':
            return ('#dc2626' if self.hover else t['bad']), '#ffffff'
        return (t['accent_hover'] if self.hover else t['accent']), '#ffffff'

    def _paint(self):
        fill, fg = self._colors()
        self.itemconfigure(self.shape, fill=fill, outline=fill if self.style != 'ghost' else self.theme['border'])
        self.itemconfigure(self.label, fill=fg)
        self.configure(cursor='hand2' if self.enabled else 'arrow')

    def _set_hover(self, value):
        self.hover = value
        self._paint()

    def _click(self, event):
        if self.enabled and 0 <= event.x <= self.w and 0 <= event.y <= self.h:
            self.command()

    def set_text(self, text):
        self.itemconfigure(self.label, text=text)

    def set_enabled(self, enabled):
        self.enabled = enabled
        self._paint()


class Card(tk.Frame):
    """Bordered surface. Put children in `.body`."""

    def __init__(self, parent, theme, pad=16):
        super().__init__(parent, bg=theme['border'])
        self.body = tk.Frame(self, bg=theme['card'], padx=pad, pady=pad)
        self.body.pack(fill='both', expand=True, padx=1, pady=1)


class Field(tk.Frame):
    """Labelled text input whose border lights up on focus."""

    def __init__(self, parent, theme, label, show=None):
        super().__init__(parent, bg=parent.cget('bg'))
        self.theme = theme
        tk.Label(self, text=label, bg=self.cget('bg'), fg=theme['muted'], font=(FONT, 9, 'bold')).pack(anchor='w')
        self.box = tk.Frame(self, bg=theme['border'], padx=1, pady=1)
        self.box.pack(fill='x', pady=(5, 0))
        self.entry = tk.Entry(self.box, bg=theme['input'], fg=theme['text'], insertbackground=theme['text'],
                              relief='flat', font=(FONT, 11), show=show, bd=0, highlightthickness=0,
                              disabledbackground=theme['card'], disabledforeground=theme['muted'])
        self.entry.pack(fill='x', ipady=8, padx=1)
        self.entry.bind('<FocusIn>', lambda e: self.box.configure(bg=theme['accent']))
        self.entry.bind('<FocusOut>', lambda e: self.box.configure(bg=theme['border']))

    def get(self):
        return self.entry.get().strip()

    def set(self, value):
        state = str(self.entry.cget('state'))
        self.entry.configure(state='normal')
        self.entry.delete(0, 'end')
        self.entry.insert(0, value)
        self.entry.configure(state=state)

    def set_enabled(self, enabled):
        self.entry.configure(state='normal' if enabled else 'disabled')


class Spinner(tk.Canvas):
    """Rotating arc used as a loading indicator."""

    def __init__(self, parent, theme, size=26, thickness=3, color=None):
        super().__init__(parent, width=size, height=size, bg=parent.cget('bg'), highlightthickness=0, bd=0)
        pad = thickness
        self.create_oval(pad, pad, size - pad, size - pad, outline=theme['border'], width=thickness)
        self.arc = self.create_arc(pad, pad, size - pad, size - pad, start=90, extent=95, style='arc',
                                   outline=color or theme['accent'], width=thickness)
        self.angle, self.running, self.job = 90, False, None
        self.hide()

    def start(self):
        self.itemconfigure('all', state='normal')
        if not self.running:
            self.running = True
            self._step()

    def stop(self):
        self.running = False
        if self.job:
            try:
                self.after_cancel(self.job)
            except tk.TclError:
                pass
        self.hide()

    def hide(self):
        self.itemconfigure('all', state='hidden')

    def _step(self):
        if not self.running:
            return
        self.angle = (self.angle - 14) % 360
        self.itemconfigure(self.arc, start=self.angle)
        self.job = self.after(28, self._step)


class StatusLine(tk.Frame):
    """Coloured dot plus text."""

    def __init__(self, parent, theme, size=10):
        super().__init__(parent, bg=parent.cget('bg'))
        self.theme = theme
        self.dot = tk.Canvas(self, width=12, height=12, bg=self.cget('bg'), highlightthickness=0, bd=0)
        self.dot_id = self.dot.create_oval(1, 1, 11, 11, fill=theme['muted'], outline='')
        self.dot.pack(side='left')
        self.label = tk.Label(self, text='', bg=self.cget('bg'), fg=theme['text'], font=(FONT, size), anchor='w',
                              justify='left')
        self.label.pack(side='left', padx=(9, 0))

    def set(self, text, level='muted'):
        color = {'good': self.theme['good'], 'bad': self.theme['bad'], 'warn': self.theme['warn'],
                 'accent': self.theme['accent']}.get(level, self.theme['muted'])
        self.dot.itemconfigure(self.dot_id, fill=color)
        self.label.configure(text=text)


class ScrollFrame(tk.Frame):
    """Vertical scroll container; the scrollbar only appears when the content does not fit."""

    def __init__(self, parent, theme):
        super().__init__(parent, bg=theme['bg'])
        self.canvas = tk.Canvas(self, bg=theme['bg'], highlightthickness=0, bd=0)
        self.bar = tk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self._on_scroll)
        self.inner = tk.Frame(self.canvas, bg=theme['bg'])
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.inner.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.bind('<Enter>', lambda e: self._bind_wheel(True))
        self.bind('<Leave>', lambda e: self._bind_wheel(False))

    def _on_scroll(self, low, high):
        if float(low) <= 0.0 and float(high) >= 1.0:
            self.bar.pack_forget()
        else:
            self.bar.pack(side='right', fill='y')
        self.bar.set(low, high)

    def _bind_wheel(self, on):
        if on:
            self.canvas.bind_all('<MouseWheel>', self._wheel)
            self.canvas.bind_all('<Button-4>', lambda e: self.canvas.yview_scroll(-2, 'units'))
            self.canvas.bind_all('<Button-5>', lambda e: self.canvas.yview_scroll(2, 'units'))
        else:
            for seq in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                self.canvas.unbind_all(seq)

    def _wheel(self, event):
        if self.bar.winfo_ismapped():
            self.canvas.yview_scroll(int(-event.delta / 120) * 2 or (-2 if event.delta > 0 else 2), 'units')


def draw_logo(parent, theme, size=44, letter='A'):
    canvas = tk.Canvas(parent, width=size, height=size, bg=parent.cget('bg'), highlightthickness=0, bd=0)
    rounded_rect(canvas, 1, 1, size - 1, size - 1, size // 4, fill=theme['accent'], outline='')
    canvas.create_text(size / 2, size / 2, text=letter, fill='#ffffff', font=(FONT, int(size * 0.42), 'bold'))
    return canvas


def make_header(parent, theme, title, subtitle, badge=None):
    """Header with logo, title, subtitle and an optional badge. Returns the frame (use `.right`)."""
    frame = tk.Frame(parent, bg=theme['bg'])
    draw_logo(frame, theme).pack(side='left')
    text = tk.Frame(frame, bg=theme['bg'])
    text.pack(side='left', padx=(14, 0))
    row = tk.Frame(text, bg=theme['bg'])
    row.pack(anchor='w')
    tk.Label(row, text=title, bg=theme['bg'], fg=theme['text'], font=(FONT, 17, 'bold')).pack(side='left')
    if badge:
        tk.Label(row, text=f' {badge} ', bg=theme['accent'], fg='#ffffff', font=(FONT, 8, 'bold')).pack(
            side='left', padx=(10, 0), pady=(4, 0))
    tk.Label(text, text=subtitle, bg=theme['bg'], fg=theme['muted'], font=(FONT, 10)).pack(anchor='w')
    frame.right = tk.Frame(frame, bg=theme['bg'])
    frame.right.pack(side='right')
    return frame


def style_check(widget, theme):
    widget.configure(bg=theme['card'], fg=theme['text'], selectcolor=theme['input'], activebackground=theme['card'],
                     activeforeground=theme['text'], font=(FONT, 10), anchor='w', bd=0, highlightthickness=0,
                     cursor='hand2')
    return widget


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------
def color(value, fallback):
    return value if isinstance(value, str) and COLOR_RE.match(value) else fallback


class AlertWindow:
    """One alert. Unexitable alerts hide the mouse, swallow keys and always use a timer."""

    def __init__(self, presenter, alert):
        self.presenter = presenter
        self.closed = False
        self.jobs = []
        root = presenter.root
        self.fg = color(alert.get('fg'), '#000000')
        self.bg = color(alert.get('bg'), '#ffffff')
        self.full = bool(alert.get('fullscreen'))
        self.lock = bool(alert.get('unexitable'))
        self.button_mode = (alert.get('dismiss') == 'button') and not self.lock
        try:
            seconds = int(alert.get('duration') or 10)
        except (TypeError, ValueError):
            seconds = 10
        self.remaining = max(1, min(seconds, MAX_LOCK_SECONDS if self.lock else 3600))

        win = self.win = tk.Toplevel(root)
        win.title('AdminRouter alert')
        win.configure(bg=self.bg)
        win.attributes('-topmost', True)
        screen_w, screen_h = root.winfo_screenwidth(), root.winfo_screenheight()
        if self.lock:
            win.overrideredirect(True)
        if self.full:
            if self.lock:
                win.geometry(f'{screen_w}x{screen_h}+0+0')
            else:
                win.attributes('-fullscreen', True)
            size, wrap = 44, max(300, screen_w - 160)
        else:
            width, height = 560, 280
            win.geometry(f'{width}x{height}+{(screen_w - width) // 2}+{(screen_h - height) // 3}')
            size, wrap = 18, width - 80

        body = tk.Label(win, text=str(alert.get('message', '')), bg=self.bg, fg=self.fg,
                        font=(FONT, size, 'bold'), wraplength=wrap, justify='center')
        body.pack(expand=True, fill='both', padx=40, pady=(40, 10))
        self.footer = tk.Label(win, text='', bg=self.bg, fg=self.fg, font=(FONT, 11))
        self.footer.pack(pady=(0, 24))

        if self.button_mode:
            tk.Button(win, text='Hide', command=self.close, bg=self.fg, fg=self.bg, activebackground=self.fg,
                      activeforeground=self.bg, relief='flat', font=(FONT, 12, 'bold'),
                      padx=24, pady=6, cursor='hand2').pack(pady=(0, 28))
        else:
            self.tick()

        if self.lock:
            win.protocol('WM_DELETE_WINDOW', lambda: None)
            for widget in (win, body, self.footer):
                try:
                    widget.configure(cursor='none')
                except tk.TclError:
                    pass
            swallow = lambda event: 'break'  # noqa: E731
            for sequence in ('<Key>', '<Button>', '<Alt-F4>', '<Escape>'):
                root.bind_all(sequence, swallow)
            self.jobs.append(win.after(60, self.grab))
            self.jobs.append(win.after(250, self.keep_on_top))
        else:
            win.protocol('WM_DELETE_WINDOW', self.close)
            win.bind('<Escape>', lambda event: self.close())
        win.focus_force()

    def grab(self):
        try:
            self.win.grab_set()
        except tk.TclError:
            pass

    def keep_on_top(self):
        if self.closed:
            return
        try:
            self.win.lift()
            self.win.attributes('-topmost', True)
            self.win.focus_force()
        except tk.TclError:
            return
        self.jobs.append(self.win.after(250, self.keep_on_top))

    def tick(self):
        if self.closed:
            return
        self.footer.configure(text=f'Closing in {self.remaining}s')
        if self.remaining <= 0:
            self.close()
            return
        self.remaining -= 1
        self.jobs.append(self.win.after(1000, self.tick))

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.lock:
            for sequence in ('<Key>', '<Button>', '<Alt-F4>', '<Escape>'):
                self.presenter.root.unbind_all(sequence)
        try:
            self.win.grab_release()
        except tk.TclError:
            pass
        for job in self.jobs:
            try:
                self.win.after_cancel(job)
            except tk.TclError:
                pass
        try:
            self.win.destroy()
        except tk.TclError:
            pass
        self.presenter.alert_closed()


class AlertPresenter:
    """Shows alerts one at a time, even while the main window is hidden."""

    def __init__(self, root, sound=lambda: False):
        self.root, self.sound = root, sound
        self.queue, self.current = [], None

    def enqueue(self, alert):
        self.queue.append(alert)
        self._next()

    def _next(self):
        if self.current is None and self.queue:
            alert = self.queue.pop(0)
            if self.sound():
                play_alert_sound()
            self.current = AlertWindow(self, alert)

    def alert_closed(self):
        self.current = None
        self.root.after(200, self._next)

    def clear(self):
        self.queue.clear()
        if self.current:
            self.current.close()


class AlertFeed:
    """Background connection to the server. Callbacks run on worker threads.

    on_state(kind, text): connecting | connected | lost | kicked | denied | failed | stopped
    """

    def __init__(self, client_id, name, on_alert, on_state):
        self.client_id, self.name = client_id, name
        self.on_alert, self.on_state = on_alert, on_state
        self.address, self.username, self.last_id = '', '', 0
        self.stop_event = threading.Event()

    def headers(self, connect=False):
        values = {'X-AdminRouter-Username': self.username, 'X-AdminRouter-Client': self.client_id,
                  'X-AdminRouter-Name': self.name}
        if connect:
            values['X-AdminRouter-Connect'] = '1'
        return values

    def start(self, username, resolver, retry=True):
        self.stop()
        self.stop_event = threading.Event()
        threading.Thread(target=self._run, args=(self.stop_event, username, resolver, retry), daemon=True).start()

    def stop(self):
        self.stop_event.set()

    def _verify(self):
        try:
            status, _, _ = http_request(f'http://{self.address}/api/alerts?after=0', self.headers(connect=True))
        except (OSError, ValueError):
            return 'error'
        if status == 200:
            return 'ok'
        return 'denied' if status in (401, 403) else 'error'

    def _poll(self, stop):
        failures = 0
        while not stop.wait(POLL_SECONDS):
            try:
                status, body, _ = http_request(f'http://{self.address}/api/alerts?after={self.last_id}', self.headers())
                if status == 403:
                    return 'kicked'
                if status == 401:
                    return 'denied'
                if status != 200:
                    raise OSError(f'HTTP {status}')
                data = json.loads(body)
                if failures:
                    self.on_state('connected', self.address)
                failures = 0
                for alert in data.get('alerts', []):
                    self.last_id = max(self.last_id, int(alert.get('id', 0)))
                    self.on_alert(alert)
            except (OSError, ValueError):
                failures += 1
                if failures == 1:
                    self.on_state('lost', 'Connection lost. Retrying...')
                if failures >= 3:
                    return 'lost'
        return 'stopped'

    def _run(self, stop, username, resolver, retry):
        self.username = username
        ever_connected = False
        while not stop.is_set():
            self.on_state('connecting', 'Looking for the server...')
            try:
                address = resolver()
            except Exception:  # noqa: BLE001
                address = None
            if address:
                self.address = address
                result = self._verify()
                if result == 'denied':
                    self.on_state('denied', 'That username was not accepted.')
                    return
                if result == 'ok':
                    ever_connected = True
                    self.last_id = 0
                    self.on_state('connected', address)
                    outcome = self._poll(stop)
                    if outcome in ('kicked', 'denied'):
                        self.on_state(outcome, 'An administrator disconnected this device.' if outcome == 'kicked'
                                      else 'That username was not accepted.')
                        return
                    if outcome == 'stopped':
                        return
            if not retry and not ever_connected:
                self.on_state('failed', 'Could not reach the server.')
                return
            stop.wait(5)