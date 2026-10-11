#!/usr/bin/env python3
"""AdminRouter dashboard: finds the server and opens the web dashboard in its own window."""
import threading
import tkinter as tk
import webbrowser

from sharedUItoolkit import (FONT, Card, Field, RoundButton, Spinner, StatusLine, apply_window_icon,
                                center_window, config_dir, find_server, load_json, make_header, palette,
                                resource_path, save_json, style_check)

WIDTH, HEIGHT = 500, 500


class DashboardApp:
    def __init__(self, root):
        self.root = root
        self.theme = t = palette('dash')
        root.title('AdminRouter Dashboard')
        root.configure(bg=t['bg'])
        root.resizable(False, False)
        apply_window_icon(root)
        center_window(root, WIDTH, HEIGHT)
        self.cfg_path = config_dir('AdminRouterDashboard') / 'config.json'
        self.cfg = load_json(self.cfg_path, {})
        self.closing = threading.Event()
        self.opened = False
        self.auto_open = tk.BooleanVar(value=bool(self.cfg.get('auto_open', True)))
        self.build_ui()
        root.protocol('WM_DELETE_WINDOW', self.close)
        if self.cfg.get('address'):
            self.address.set(self.cfg['address'])
        self.rescan()

    def ui(self, fn, *args):
        try:
            self.root.after(0, lambda: fn(*args))
        except (tk.TclError, RuntimeError):
            pass

    def build_ui(self):
        t = self.theme
        pad = tk.Frame(self.root, bg=t['bg'])
        pad.pack(fill='both', expand=True, padx=28, pady=26)
        make_header(pad, t, 'AdminRouter', 'Network dashboard').pack(fill='x')

        row = tk.Frame(pad, bg=t['bg'])
        row.pack(fill='x', pady=(26, 14))
        self.status = StatusLine(row, t)
        self.status.pack(side='left')
        self.spinner = Spinner(row, t, size=22, thickness=3)
        self.spinner.pack(side='right')

        card = Card(pad, t, pad=20)
        card.pack(fill='x')
        self.address = Field(card.body, t, 'SERVER ADDRESS')
        self.address.pack(fill='x')
        self.address.entry.bind('<Return>', lambda e: self.open_dashboard())
        buttons = tk.Frame(card.body, bg=t['card'])
        buttons.pack(fill='x', pady=(18, 12))
        self.open_button = RoundButton(buttons, 'Open dashboard', self.open_dashboard, t, width=226, height=44, size=11)
        self.open_button.pack(side='left')
        self.scan_button = RoundButton(buttons, 'Search again', self.rescan, t, style='ghost', width=150, height=44, size=10)
        self.scan_button.pack(side='right')
        auto = style_check(tk.Checkbutton(card.body, text='Open automatically when the server is found',
                                          variable=self.auto_open, command=self.save_prefs), t)
        auto.pack(anchor='w')

        tk.Label(pad, text='The dashboard opens in its own window. If that is not available on this PC it opens in your browser.',
                 bg=t['bg'], fg=t['muted'], font=(FONT, 9), wraplength=430, justify='left').pack(anchor='w', pady=(18, 0))

    def save_prefs(self):
        self.cfg.update(address=self.address.get(), auto_open=bool(self.auto_open.get()))
        save_json(self.cfg_path, self.cfg)

    # ---------- discovery ----------
    def rescan(self):
        self.scan_button.set_enabled(False)
        self.status.set('Looking for AdminRouter on your network...', 'accent')
        self.spinner.start()
        threading.Thread(target=self.discover, daemon=True).start()

    def discover(self):
        found = find_server()
        self.ui(self.found, found)

    def found(self, info):
        if self.opened or self.closing.is_set():
            return
        self.spinner.stop()
        self.scan_button.set_enabled(True)
        if not info:
            self.status.set('Not found automatically. Type the address above.', 'warn')
            return
        self.address.set(f"{info['host']}:{info['port']}")
        self.status.set('Server found.', 'good')
        if self.auto_open.get():
            self.root.after(700, self.open_dashboard)

    # ---------- opening ----------
    def open_dashboard(self):
        address = self.address.get().replace('http://', '').rstrip('/')
        if not address or self.opened:
            if not address:
                self.status.set('Enter the server address first.', 'bad')
            return
        self.opened = True
        self.save_prefs()
        url = f'http://{address}/'
        try:
            import webview
        except Exception:  # noqa: BLE001 - pywebview or its runtime is missing
            webview = None
        if webview is None:
            webbrowser.open(url)
            self.opened = False
            self.status.set('Opened in your browser.', 'good')
            return
        self.closing.set()
        self.root.destroy()
        try:
            webview.create_window('AdminRouter Dashboard', url, width=1280, height=820, min_size=(900, 600))
            try:
                webview.start(icon=resource_path('app.ico'))
            except TypeError:
                webview.start()
        except Exception:  # noqa: BLE001 - embedded window failed (for example WebView2 missing)
            webbrowser.open(url)

    def close(self):
        self.closing.set()
        self.root.destroy()


if __name__ == '__main__':
    window = tk.Tk()
    DashboardApp(window)
    window.mainloop()