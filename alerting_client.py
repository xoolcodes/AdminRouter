#!/usr/bin/env python3
"""AdminRouter desktop companion with local service discovery and styled alerts."""
import concurrent.futures
import ipaddress
import json
import os
import re
import socket
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

BLUE = '#123b72'
BLUE_DARK = '#0b2850'
WHITE = '#ffffff'
INK = '#182534'
MUTED = '#d7e3f2'
GREEN = '#72d39a'
RED = '#ffb3b3'
DISCOVERY_PORT = 39872
POLL_SECONDS = 2
MAX_LOCK_SECONDS = 600
COLOR_RE = re.compile(r'^#[0-9a-fA-F]{6}$')


def config_file():
    base = os.environ.get('APPDATA') or str(Path.home() / '.config')
    return Path(base) / 'AdminRouterClient' / 'client.json'


def load_client_id():
    """A stable id so the server recognises this PC between runs."""
    path = config_file()
    try:
        value = json.loads(path.read_text(encoding='utf-8')).get('client_id', '')
        if re.fullmatch(r'[A-Za-z0-9-]{8,64}', value):
            return value
    except (OSError, ValueError, AttributeError):
        pass
    value = uuid.uuid4().hex
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'client_id': value}), encoding='utf-8')
    except OSError:
        pass
    return value


def color(value, fallback):
    return value if isinstance(value, str) and COLOR_RE.match(value) else fallback


class AlertWindow:
    """One alert. Unexitable alerts hide the mouse, swallow keys and always use a timer."""

    def __init__(self, app, alert):
        self.app = app
        self.closed = False
        self.jobs = []
        root = app.root
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
                        font=('Segoe UI', size, 'bold'), wraplength=wrap, justify='center')
        body.pack(expand=True, fill='both', padx=40, pady=(40, 10))
        self.footer = tk.Label(win, text='', bg=self.bg, fg=self.fg, font=('Segoe UI', 11))
        self.footer.pack(pady=(0, 24))

        if self.button_mode:
            tk.Button(win, text='Hide', command=self.close, bg=self.fg, fg=self.bg, activebackground=self.fg,
                      activeforeground=self.bg, relief='flat', font=('Segoe UI', 12, 'bold'),
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
                self.app.root.unbind_all(sequence)
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
        self.app.alert_closed()


class AlertingClient:
    def __init__(self, root):
        self.root = root
        root.title('AdminRouter Alerting')
        root.geometry('520x320')
        root.minsize(520, 320)
        root.configure(bg=BLUE)
        self.server = ''
        self.username = ''
        self.last_id = 0
        self.client_id = load_client_id()
        self.client_name = socket.gethostname().encode('ascii', 'replace').decode('ascii')
        self.stop_event = threading.Event()      # app is closing
        self.session_stop = threading.Event()    # current connection ended
        self.alert_queue = []
        self.overlay = None
        self.scanning = True
        self.build_ui()
        root.protocol('WM_DELETE_WINDOW', self.close)
        threading.Thread(target=self.discover, daemon=True).start()

    # ---------- UI ----------
    def label(self, parent, text, size=10, bold=False, fg=WHITE, **kw):
        return tk.Label(parent, text=text, bg=parent.cget('bg'), fg=fg,
                        font=('Segoe UI', size, 'bold' if bold else 'normal'), **kw)

    def build_ui(self):
        header = tk.Frame(self.root, bg=BLUE_DARK)
        header.pack(fill='x')
        header.pack_propagate(False)
        header.configure(height=84)
        self.label(header, 'AdminRouter', 18, True).pack(anchor='w', padx=24, pady=(14, 0))
        self.label(header, 'Remote Alerting companion', 10, fg=MUTED).pack(anchor='w', padx=24)
        body = tk.Frame(self.root, bg=BLUE)
        body.pack(fill='both', expand=True, padx=24, pady=16)
        self.label(body, 'Network address', 9, True, fg=MUTED).pack(anchor='w')
        self.address = tk.Entry(body, relief='flat', fg=INK, font=('Segoe UI', 11))
        self.address.pack(fill='x', ipady=5, pady=(2, 10))
        self.label(body, 'Username', 9, True, fg=MUTED).pack(anchor='w')
        self.username_entry = tk.Entry(body, relief='flat', fg=INK, font=('Segoe UI', 11))
        self.username_entry.pack(fill='x', ipady=5, pady=(2, 12))
        self.connect_button = tk.Button(body, text='Connect', command=self.connect, relief='flat', bg=WHITE, fg=BLUE_DARK,
                                        font=('Segoe UI', 10, 'bold'), cursor='hand2', state='disabled')
        self.connect_button.pack(anchor='e', ipadx=14, ipady=3)
        self.status = self.label(body, 'Looking for AdminRouter...', 9, fg=MUTED)
        self.status.pack(anchor='w', pady=(8, 0))

    def set_status(self, text, fg=MUTED):
        self.status.configure(text=text, fg=fg)

    # ---------- discovery ----------
    def local_candidates(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(('8.8.8.8', 80))
            address = sock.getsockname()[0]
            sock.close()
            return [str(h) for h in ipaddress.ip_network(f'{address}/24', strict=False).hosts()]
        except OSError:
            return ['127.0.0.1']

    def probe(self, host):
        try:
            with urllib.request.urlopen(f'http://{host}:{DISCOVERY_PORT}/info/creditngtgtu', timeout=0.6) as response:
                info = json.loads(response.read())
            if info.get('service') == 'AdminRouter':
                return host, int(info.get('port')), str(info.get('username', ''))
        except (OSError, ValueError, TypeError, urllib.error.URLError):
            pass
        return None

    def find_server(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=48) as pool:
            for found in pool.map(self.probe, self.local_candidates()):
                if found:
                    return found
        return None

    def discover(self):
        while not self.stop_event.is_set() and self.scanning:
            found = self.find_server()
            if found:
                self.root.after(0, lambda f=found: self.discovery_found(*f))
                return
            self.root.after(0, lambda: self.set_status('Still looking for AdminRouter...'))
            self.stop_event.wait(3)

    def discovery_found(self, host, port, username):
        self.scanning = False
        self.address.delete(0, 'end')
        self.address.insert(0, f'{host}:{port}')
        self.set_status('Server found. Enter your username to connect.')
        self.connect_button.configure(state='normal')
        if not self.username_entry.get():
            self.username_entry.insert(0, username)
        self.username_entry.focus_set()

    # ---------- connection ----------
    def headers(self, connect=False):
        values = {'X-AdminRouter-Username': self.username, 'X-AdminRouter-Client': self.client_id,
                  'X-AdminRouter-Name': self.client_name}
        if connect:
            values['X-AdminRouter-Connect'] = '1'
        return values

    def connect(self):
        username = self.username_entry.get().strip()
        address = self.address.get().strip().replace('http://', '').rstrip('/')
        if not username or not address:
            messagebox.showwarning('AdminRouter', 'Enter the address and your username first.')
            return
        self.connect_button.configure(state='disabled')
        self.set_status('Connecting...')
        threading.Thread(target=self.verify_username, args=(address, username), daemon=True).start()

    def verify_username(self, address, username):
        self.server, self.username = address, username
        try:
            request = urllib.request.Request(f'http://{address}/api/alerts?after=0', headers=self.headers(connect=True))
            with urllib.request.urlopen(request, timeout=5) as response:
                json.loads(response.read())
        except (OSError, ValueError, urllib.error.URLError) as exc:
            self.server = ''
            self.root.after(0, lambda: self.connection_failed(str(exc)))
            return
        self.last_id = 0
        self.session_stop = threading.Event()
        self.root.after(0, lambda: self.set_status('Connected. Waiting for alerts.', GREEN))
        threading.Thread(target=self.poll, args=(self.session_stop,), daemon=True).start()

    def connection_failed(self, detail):
        self.connect_button.configure(state='normal')
        self.set_status('Username was not accepted.', RED)
        messagebox.showerror('AdminRouter', 'Could not connect with that username.\n' + detail)

    def poll(self, session):
        failures = 0
        while not session.wait(POLL_SECONDS) and not self.stop_event.is_set():
            try:
                request = urllib.request.Request(f'http://{self.server}/api/alerts?after={self.last_id}', headers=self.headers())
                with urllib.request.urlopen(request, timeout=5) as response:
                    data = json.loads(response.read())
                failures = 0
                for alert in data.get('alerts', []):
                    self.last_id = max(self.last_id, int(alert.get('id', 0)))
                    self.root.after(0, lambda a=alert: self.enqueue_alert(a))
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    text = 'An administrator disconnected this device.' if exc.code == 403 else 'Username was not accepted.'
                    self.root.after(0, lambda t=text: self.disconnected(t))
                    return
                failures += 1
            except (OSError, ValueError, urllib.error.URLError):
                failures += 1
                if failures == 1:
                    self.root.after(0, lambda: self.set_status('Connection lost; looking again...', RED))
                if failures % 4 == 3:
                    found = self.find_server()
                    if found:
                        self.server = f'{found[0]}:{found[1]}'
            else:
                if failures == 0:
                    self.root.after(0, lambda: self.set_status('Connected. Waiting for alerts.', GREEN))

    def disconnected(self, text):
        self.session_stop.set()
        self.server = ''
        self.alert_queue.clear()
        if self.overlay:
            self.overlay.close()
        self.connect_button.configure(state='normal')
        self.set_status(text, RED)

    # ---------- alerts ----------
    def enqueue_alert(self, alert):
        self.alert_queue.append(alert)
        if self.overlay is None:
            self.show_next()

    def show_next(self):
        if self.overlay is None and self.alert_queue:
            self.overlay = AlertWindow(self, self.alert_queue.pop(0))

    def alert_closed(self):
        self.overlay = None
        self.root.after(200, self.show_next)

    def close(self):
        self.stop_event.set()
        self.session_stop.set()
        self.root.destroy()


if __name__ == '__main__':
    window = tk.Tk()
    AlertingClient(window)
    window.mainloop()