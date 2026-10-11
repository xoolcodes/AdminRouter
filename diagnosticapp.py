#!/usr/bin/env python3
"""AdminRouter diagnostics: checks whether the server is running and why not."""
import json
import socket
import threading
import time
import tkinter as tk

from sharedUItoolkit import (FONT, Card, Field, RoundButton, ScrollFrame, Spinner, apply_window_icon, center_window,
                                config_dir, find_server, get_local_ip, http_request, load_json, make_header,
                                palette, probe_host, save_json, split_address, style_check)

WIDTH, HEIGHT = 680, 830

CHECKS = [
    ('network', 'This PC is on a network'),
    ('beacon', 'Discovery beacon answers'),
    ('port', 'Web port accepts connections'),
    ('health', 'Server responds'),
    ('login', 'Login page is served'),
    ('auth', 'Sign-in is enforced'),
    ('user', 'Username is accepted'),
    ('alerts', 'Remote Alerting is switched on'),
]
HINTS = [
    'Check the AdminRouter service is running on the host PC (log: %LOCALAPPDATA%\\AdminRouter\\adminrouter.log).',
    'Check the address and port, for example 192.168.1.20:8080.',
    'Allow the web port and port 39872 through the host PC firewall.',
    'Make sure this PC is on the same network as the host.',
]


def fmt_uptime(seconds):
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    return (f'{days}d ' if days else '') + (f'{hours}h ' if hours or days else '') + f'{minutes}m'


class CheckRow(tk.Frame):
    def __init__(self, parent, theme, title):
        super().__init__(parent, bg=parent.cget('bg'))
        self.theme = theme
        self.dot = tk.Canvas(self, width=16, height=16, bg=self.cget('bg'), highlightthickness=0, bd=0)
        self.dot_id = self.dot.create_oval(2, 2, 14, 14, fill=theme['border'], outline='')
        self.dot.grid(row=0, column=0, rowspan=2, padx=(0, 12), sticky='n', pady=(3, 0))
        tk.Label(self, text=title, bg=self.cget('bg'), fg=theme['text'], font=(FONT, 10, 'bold'), anchor='w').grid(
            row=0, column=1, sticky='w')
        self.ms = tk.Label(self, text='', bg=self.cget('bg'), fg=theme['muted'], font=(FONT, 9))
        self.ms.grid(row=0, column=2, sticky='e')
        self.detail = tk.Label(self, text='Waiting', bg=self.cget('bg'), fg=theme['muted'], font=(FONT, 9),
                               anchor='w', justify='left', wraplength=500)
        self.detail.grid(row=1, column=1, columnspan=2, sticky='w')
        self.columnconfigure(1, weight=1)

    def set(self, level, detail='', ms=None):
        t = self.theme
        color = {'good': t['good'], 'bad': t['bad'], 'warn': t['warn'], 'run': t['accent']}.get(level, t['border'])
        self.dot.itemconfigure(self.dot_id, fill=color)
        self.detail.configure(text=detail, fg=t['bad'] if level == 'bad' else t['muted'])
        self.ms.configure(text=f'{ms} ms' if ms is not None else '')


class DiagnosticApp:
    def __init__(self, root):
        self.root = root
        self.theme = t = palette('diag')
        root.title('AdminRouter Diagnostics')
        root.configure(bg=t['bg'])
        root.resizable(False, False)
        apply_window_icon(root)
        center_window(root, WIDTH, HEIGHT)
        self.cfg_path = config_dir('AdminRouterDiagnostic') / 'config.json'
        self.cfg = load_json(self.cfg_path, {})
        self.running = False
        self.closing = False
        self.report = []
        self.recheck_job = None
        self.recheck = tk.BooleanVar(value=False)
        self.build_ui()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(400, self.run)

    def ui(self, fn, *args):
        try:
            self.root.after(0, lambda: fn(*args))
        except (tk.TclError, RuntimeError):
            pass

    # ---------- UI ----------
    def build_ui(self):
        t = self.theme
        scroll = ScrollFrame(self.root, t)
        scroll.pack(fill='both', expand=True)
        pad = tk.Frame(scroll.inner, bg=t['bg'])
        pad.pack(fill='both', expand=True, padx=28, pady=24)
        make_header(pad, t, 'Diagnostics', 'Is the AdminRouter server running?', badge='TOOL').pack(fill='x')

        # summary banner
        banner = Card(pad, t, pad=18)
        banner.pack(fill='x', pady=(18, 10))
        b = banner.body
        self.big_dot = tk.Canvas(b, width=26, height=26, bg=t['card'], highlightthickness=0, bd=0)
        self.big_dot_id = self.big_dot.create_oval(2, 2, 24, 24, fill=t['border'], outline='')
        self.big_dot.pack(side='left')
        text = tk.Frame(b, bg=t['card'])
        text.pack(side='left', padx=(14, 0), fill='x', expand=True)
        self.title = tk.Label(text, text='Ready', bg=t['card'], fg=t['text'], font=(FONT, 15, 'bold'), anchor='w')
        self.title.pack(anchor='w')
        self.subtitle = tk.Label(text, text='', bg=t['card'], fg=t['muted'], font=(FONT, 10), anchor='w',
                                 justify='left', wraplength=440)
        self.subtitle.pack(anchor='w')
        self.spinner = Spinner(b, t, size=28, thickness=3)
        self.spinner.pack(side='right')

        # inputs
        inputs = tk.Frame(pad, bg=t['bg'])
        inputs.pack(fill='x')
        self.address = Field(inputs, t, 'SERVER ADDRESS (LEAVE BLANK TO SEARCH)')
        self.address.pack(side='left', fill='x', expand=True)
        self.user = Field(inputs, t, 'USERNAME (OPTIONAL)')
        self.user.pack(side='left', fill='x', expand=True, padx=(14, 0))
        self.address.set(self.cfg.get('address', ''))
        self.user.set(self.cfg.get('username', ''))
        for field in (self.address, self.user):
            field.entry.bind('<Return>', lambda e: self.run())

        actions = tk.Frame(pad, bg=t['bg'])
        actions.pack(fill='x', pady=(14, 14))
        self.run_button = RoundButton(actions, 'Run diagnostics', self.run, t, width=170, height=40)
        self.run_button.pack(side='left')
        self.copy_button = RoundButton(actions, 'Copy report', self.copy_report, t, style='ghost', width=130, height=40)
        self.copy_button.pack(side='left', padx=(10, 0))
        auto = style_check(tk.Checkbutton(actions, text='Re-check every 10 seconds', variable=self.recheck,
                                          command=self.schedule_recheck), {**t, 'card': t['bg']})
        auto.pack(side='right')

        # check rows
        results = Card(pad, t, pad=16)
        results.pack(fill='x')
        self.rows = {}
        for index, (key, title) in enumerate(CHECKS):
            row = CheckRow(results.body, t, title)
            row.pack(fill='x', pady=(0 if index == 0 else 6, 0))
            self.rows[key] = row

        self.hint = tk.Label(pad, text='', bg=t['bg'], fg=t['muted'], font=(FONT, 9), justify='left', anchor='w',
                             wraplength=620)
        self.hint.pack(anchor='w', pady=(12, 0), fill='x')

    def banner(self, level, title, subtitle=''):
        color = {'good': self.theme['good'], 'bad': self.theme['bad'], 'warn': self.theme['warn'],
                 'run': self.theme['accent']}.get(level, self.theme['border'])
        self.big_dot.itemconfigure(self.big_dot_id, fill=color)
        self.title.configure(text=title)
        self.subtitle.configure(text=subtitle)

    # ---------- running ----------
    def run(self):
        if self.running:
            return
        self.running = True
        address, username = self.address.get(), self.user.get()
        self.cfg.update(address=address, username=username)
        save_json(self.cfg_path, self.cfg)
        self.report = []
        for row in self.rows.values():
            row.set('wait', 'Waiting')
        self.hint.configure(text='')
        self.banner('run', 'Checking...', 'Running the checks, this takes a few seconds.')
        self.spinner.start()
        self.run_button.set_enabled(False)
        threading.Thread(target=self.worker, args=(address, username), daemon=True).start()

    def set_row(self, key, level, detail='', ms=None):
        self.report.append((dict(CHECKS)[key], level, detail, ms))
        self.ui(self.rows[key].set, level, detail, ms)

    def worker(self, address, username):
        results = {}

        def fail_rest(keys, reason):
            for key in keys:
                self.set_row(key, 'wait', reason)

        # 1. local network
        ip = get_local_ip()
        if ip.startswith('127.'):
            self.set_row('network', 'warn', 'No network address found (only loopback).')
        else:
            self.set_row('network', 'good', f'This PC is {ip}')

        # 2. find or probe the server
        host, port, beacon = '', None, None
        if address:
            host, port = split_address(address)
            self.ui(self.rows['beacon'].set, 'run', 'Asking the server...')
            beacon = probe_host(host, timeout=2)
        else:
            self.ui(self.rows['beacon'].set, 'run', 'Searching your network...')
            beacon = find_server()
            if beacon:
                host, port = beacon['host'], beacon['port']
        real_port = beacon['port'] if beacon else None
        if beacon and address and port and port != real_port:
            self.set_row('beacon', 'warn', f"The server says it runs on port {real_port}, but you entered {port}. Using {real_port}.")
            port = real_port
        elif beacon:
            port = port or real_port
            self.set_row('beacon', 'good', f"Found AdminRouter at {host}:{beacon['port']}")
        elif address:
            self.set_row('beacon', 'warn', 'No answer on port 39872 (older server version or firewall). Continuing.')
        else:
            self.set_row('beacon', 'bad', 'No AdminRouter server found on this network.')
            fail_rest(['port', 'health', 'login', 'auth', 'user', 'alerts'], 'Skipped')
            self.finish('down', 'Server not found', 'Nothing answered on your local network.')
            return
        if not port:
            self.set_row('port', 'bad', 'Add the port to the address, for example 192.168.1.20:8080')
            fail_rest(['health', 'login', 'auth', 'user', 'alerts'], 'Skipped')
            self.finish('down', 'Missing port', 'Enter the address as host:port.')
            return
        base = f'http://{host}:{port}'

        # 3. TCP port
        started = time.monotonic()
        try:
            with socket.create_connection((host, port), timeout=3):
                pass
            self.set_row('port', 'good', f'{host}:{port} is open', int((time.monotonic() - started) * 1000))
        except OSError as exc:
            self.set_row('port', 'bad', f'Could not connect to {host}:{port} ({exc.__class__.__name__}).')
            fail_rest(['health', 'login', 'auth', 'user', 'alerts'], 'Skipped')
            self.finish('down', 'Server is not reachable', f'Nothing is listening on {host}:{port}.')
            return

        # 4. health
        health = {}
        try:
            status, body, ms = http_request(f'{base}/api/health', {'X-AdminRouter-Username': username}, timeout=5)
            if status == 200:
                health = json.loads(body)
                self.set_row('health', 'good', f"Running, up for {fmt_uptime(health.get('uptime', 0))}", ms)
            elif status == 404:
                self.set_row('health', 'good', 'Running (older version without a health endpoint).', ms)
            else:
                self.set_row('health', 'warn', f'Answered with HTTP {status}.', ms)
        except (OSError, ValueError) as exc:
            self.set_row('health', 'bad', f'Port is open but the server did not answer ({exc}).')

        # 5. login page
        try:
            status, body, ms = http_request(f'{base}/', timeout=5)
            if status == 200 and 'AdminRouter' in body:
                self.set_row('login', 'good', 'Login page loads.', ms)
            else:
                self.set_row('login', 'warn', f'Unexpected response (HTTP {status}). Is another app using this port?', ms)
        except OSError as exc:
            self.set_row('login', 'bad', f'Could not load the login page ({exc}).')

        # 6. auth enforced
        try:
            status, _, ms = http_request(f'{base}/api/status', timeout=5)
            if status == 401:
                self.set_row('auth', 'good', 'Protected pages require a sign-in.', ms)
            else:
                self.set_row('auth', 'warn', f'Expected HTTP 401 but got {status}.', ms)
        except OSError as exc:
            self.set_row('auth', 'bad', f'No answer ({exc}).')

        # 7. username
        if not username:
            self.set_row('user', 'wait', 'Skipped. Enter a username above to test it.')
        elif 'authorized' in health:
            if health['authorized']:
                self.set_row('user', 'good', f'"{username}" is accepted.')
            else:
                self.set_row('user', 'bad', f'"{username}" was not accepted by the server.')
        else:
            self.set_row('user', 'wait', 'Skipped. This server version cannot test usernames.')

        # 8. remote alerting
        enabled = health.get('remote_alerting', beacon['remote_alerting'] if beacon else None)
        if enabled is True:
            self.set_row('alerts', 'good', 'Alerts can be sent to clients.')
        elif enabled is False:
            self.set_row('alerts', 'warn', 'Switched off. Turn it on in the dashboard under Settings.')
        else:
            self.set_row('alerts', 'wait', 'Unknown.')

        levels = [level for _, level, _, _ in self.report]
        if 'bad' in levels:
            self.finish('warn', 'Server is running, with problems', 'See the red items below.')
        elif 'warn' in levels:
            self.finish('good', 'Server is running', 'A few things are worth a look (yellow items).')
        else:
            self.finish('good', 'Server is running', f'Everything checks out at {host}:{port}.')

    def finish(self, level, title, subtitle):
        self.ui(self.done, level, title, subtitle)

    def done(self, level, title, subtitle):
        self.running = False
        self.spinner.stop()
        self.run_button.set_enabled(True)
        self.banner('bad' if level == 'down' else level, title, subtitle)
        if level in ('down', 'warn'):
            self.hint.configure(text='What to try:\n- ' + '\n- '.join(HINTS))
        self.report.append(('SUMMARY', level, f'{title}. {subtitle}', None))
        self.schedule_recheck()

    def schedule_recheck(self):
        if self.recheck_job:
            self.root.after_cancel(self.recheck_job)
            self.recheck_job = None
        if self.recheck.get() and not self.closing:
            self.recheck_job = self.root.after(10000, self.run)

    def copy_report(self):
        lines = [f"AdminRouter diagnostics - {time.strftime('%Y-%m-%d %H:%M:%S')}"]
        for name, level, detail, ms in self.report:
            lines.append(f"[{level.upper():5}] {name}: {detail}" + (f' ({ms} ms)' if ms is not None else ''))
        self.root.clipboard_clear()
        self.root.clipboard_append('\n'.join(lines))
        self.copy_button.set_text('Copied!')
        self.root.after(1500, lambda: self.copy_button.set_text('Copy report'))

    def close(self):
        self.closing = True
        self.root.destroy()

if __name__ == '__main__':
    window = tk.Tk()
    DiagnosticApp(window)
    window.mainloop()