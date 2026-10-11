#!/usr/bin/env python3
"""AdminRouter Pro: licensed alert monitor with PIN-protected settings and background mode."""
import hashlib
import hmac
import os
import re
import secrets
import socket
import sys
import threading
import time
import tkinter as tk

from sharedUItoolkit import (FONT, AlertFeed, AlertPresenter, Card, Field, RoundButton, ScrollFrame, Spinner,
                                StatusLine, apply_window_icon, center_window, config_dir, device_name, draw_logo,
                                find_server, load_client_id, load_json, make_header, palette, resource_path,
                                save_json, split_address, style_check)

APP_NAME = 'AdminRouterPro'
INSTANCE_PORT = 39873
KEY_SALT = 'adminrouter-pro-activation-v1'
KEY_HASH = '620876d7f8f602d534274b1dc27a6747dcaf7437f2f12f36ed868b33cd4cb90d'
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
WIDTH, HEIGHT = 560, 720
DEFAULTS = {
    'key_hash': '', 'pin_salt': '', 'pin_hash': '',
    'mode': 'auto', 'address': '', 'username': '',
    'start_with_windows': False, 'close_to_tray': True, 'require_pin_quit': True, 'sound': True,
}


# --------------------------------------------------------------------------
# Config, licence key, PIN
# --------------------------------------------------------------------------
class Config:
    def __init__(self):
        self.path = config_dir(APP_NAME) / 'config.json'
        self.data = {**DEFAULTS, **load_json(self.path, {})}

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value

    def save(self):
        save_json(self.path, self.data)

    @property
    def activated(self):
        return hmac.compare_digest(str(self.data.get('key_hash', '')), KEY_HASH)

    @property
    def has_pin(self):
        return bool(self.data.get('pin_hash'))


def key_matches(text):
    normalized = re.sub(r'[^A-Z0-9]', '', text.upper())
    return hmac.compare_digest(hashlib.sha256((KEY_SALT + normalized).encode()).hexdigest(), KEY_HASH)


def hash_pin(pin, salt_hex=None):
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    return salt.hex(), hashlib.pbkdf2_hmac('sha256', pin.encode(), salt, 200_000).hex()


def pin_matches(pin, salt_hex, digest_hex):
    try:
        return hmac.compare_digest(hash_pin(pin, salt_hex)[1], digest_hex)
    except ValueError:
        return False


def pin_problem(pin, confirm):
    if not (pin.isdigit() and 4 <= len(pin) <= 8):
        return 'Use 4 to 8 digits.'
    if pin != confirm:
        return 'The two PINs do not match.'
    return ''


def set_startup(enabled):
    """Add or remove the Windows login entry that starts the app in the background."""
    if os.name != 'nt':
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                if getattr(sys, 'frozen', False):
                    command = f'"{sys.executable}" --background'
                else:
                    command = f'"{sys.executable}" "{os.path.abspath(__file__)}" --background'
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


class InstanceLock:
    """Only one copy runs. A second launch just asks the first to show its window."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    def acquire(self):
        try:
            self.sock.bind(('127.0.0.1', INSTANCE_PORT))
            self.sock.listen(2)
            return True
        except OSError:
            return False

    def listen(self, on_show):
        def loop():
            while True:
                try:
                    conn, _ = self.sock.accept()
                    data = conn.recv(16)
                    conn.close()
                    if data == b'show':
                        on_show()
                except OSError:
                    return
        threading.Thread(target=loop, daemon=True).start()

    @staticmethod
    def signal():
        try:
            with socket.create_connection(('127.0.0.1', INSTANCE_PORT), timeout=2) as conn:
                conn.sendall(b'show')
        except OSError:
            pass


# --------------------------------------------------------------------------
# Splash with loading animation
# --------------------------------------------------------------------------
class Splash(tk.Toplevel):
    STEPS = ['Starting up...', 'Loading secure storage...', 'Checking licence...', 'Preparing the alert engine...', 'Ready']

    def __init__(self, root, theme, on_done, duration=2.8):
        super().__init__(root)
        self.theme, self.on_done, self.duration = theme, on_done, duration
        self.overrideredirect(True)
        self.configure(bg=theme['accent'])
        try:
            self.attributes('-topmost', True)
        except tk.TclError:
            pass
        center_window(self, 480, 320)
        inner = tk.Frame(self, bg=theme['bg'])
        inner.pack(fill='both', expand=True, padx=1, pady=1)
        draw_logo(inner, theme, size=76).pack(pady=(34, 12))
        tk.Label(inner, text='AdminRouter Pro', bg=theme['bg'], fg=theme['text'], font=(FONT, 21, 'bold')).pack()
        tk.Label(inner, text='Alert monitor', bg=theme['bg'], fg=theme['muted'], font=(FONT, 10)).pack()
        self.spinner = Spinner(inner, theme, size=34, thickness=4)
        self.spinner.pack(pady=(20, 8))
        self.spinner.start()
        self.status = tk.Label(inner, text=self.STEPS[0], bg=theme['bg'], fg=theme['muted'], font=(FONT, 9))
        self.status.pack()
        self.bar = tk.Canvas(inner, width=320, height=10, bg=theme['bg'], highlightthickness=0, bd=0)
        self.bar.create_line(5, 5, 315, 5, width=6, capstyle='round', fill=theme['border'])
        self.fill = self.bar.create_line(5, 5, 5, 5, width=6, capstyle='round', fill=theme['accent'])
        self.bar.pack(pady=(14, 0))
        self.started = time.monotonic()
        self.alpha(0.0)
        self.tick()

    def alpha(self, value):
        try:
            self.attributes('-alpha', max(0.0, min(1.0, value)))
        except tk.TclError:
            pass

    def tick(self):
        elapsed = time.monotonic() - self.started
        p = min(1.0, elapsed / self.duration)
        eased = p * p * (3 - 2 * p)
        self.bar.coords(self.fill, 5, 5, 5 + 310 * eased, 5)
        self.status.configure(text=self.STEPS[min(len(self.STEPS) - 1, int(p * len(self.STEPS)))])
        if elapsed < 0.25:
            self.alpha(elapsed / 0.25)
        if p >= 1.0:
            self.after(250, self.fade_out)
        else:
            self.after(16, self.tick)

    def fade_out(self, value=1.0):
        if value <= 0.05:
            self.spinner.stop()
            self.destroy()
            self.on_done()
            return
        self.alpha(value)
        self.after(18, lambda: self.fade_out(value - 0.12))


# --------------------------------------------------------------------------
# Dialogs
# --------------------------------------------------------------------------
class Dialog(tk.Toplevel):
    def __init__(self, app, title, width, height, topmost=False):
        super().__init__(app.root)
        self.app, self.theme = app, app.theme
        self.title(title)
        self.configure(bg=self.theme['bg'])
        self.resizable(False, False)
        center_window(self, width, height)
        if app.root.state() != 'withdrawn':
            self.transient(app.root)
        if topmost:
            self.attributes('-topmost', True)
        self.protocol('WM_DELETE_WINDOW', self.cancel)
        self.bind('<Escape>', lambda e: self.cancel())
        self.after(60, self.take_focus)

    def take_focus(self):
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
            self.grab_set()
        except tk.TclError:
            pass

    def cancel(self):
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


def digits_only(widget, field):
    vcmd = (widget.register(lambda value: value == '' or (value.isdigit() and len(value) <= 8)), '%P')
    field.entry.configure(validate='key', validatecommand=vcmd)


class PinDialog(Dialog):
    def __init__(self, app, prompt, on_ok, topmost=False):
        super().__init__(app, 'AdminRouter Pro', 400, 330, topmost)
        t, self.on_ok = self.theme, on_ok
        pad = tk.Frame(self, bg=t['bg'])
        pad.pack(fill='both', expand=True, padx=28, pady=26)
        tk.Label(pad, text='Enter your PIN', bg=t['bg'], fg=t['text'], font=(FONT, 16, 'bold')).pack(anchor='w')
        tk.Label(pad, text=prompt, bg=t['bg'], fg=t['muted'], font=(FONT, 10), wraplength=340, justify='left').pack(
            anchor='w', pady=(4, 16))
        self.pin = Field(pad, t, 'PIN', show='\u2022')
        self.pin.pack(fill='x')
        digits_only(self, self.pin)
        self.error = tk.Label(pad, text='', bg=t['bg'], fg=t['bad'], font=(FONT, 9))
        self.error.pack(anchor='w', pady=(8, 0))
        row = tk.Frame(pad, bg=t['bg'])
        row.pack(side='bottom', fill='x')
        RoundButton(row, 'Unlock', self.submit, t, width=170, height=42).pack(side='right')
        RoundButton(row, 'Cancel', self.cancel, t, style='ghost', width=110, height=42).pack(side='right', padx=(0, 10))
        self.pin.entry.bind('<Return>', lambda e: self.submit())
        self.after(120, self.pin.entry.focus_set)

    def submit(self):
        ok, message = self.app.check_pin(self.pin.get())
        if ok:
            self.cancel()
            self.on_ok()
        else:
            self.error.configure(text=message)
            self.pin.set('')


class ChangePinDialog(Dialog):
    def __init__(self, app):
        super().__init__(app, 'Change PIN', 400, 400)
        t = self.theme
        pad = tk.Frame(self, bg=t['bg'])
        pad.pack(fill='both', expand=True, padx=28, pady=26)
        tk.Label(pad, text='Change PIN', bg=t['bg'], fg=t['text'], font=(FONT, 16, 'bold')).pack(anchor='w')
        tk.Label(pad, text='4 to 8 digits.', bg=t['bg'], fg=t['muted'], font=(FONT, 10)).pack(anchor='w', pady=(4, 14))
        self.new = Field(pad, t, 'NEW PIN', show='\u2022')
        self.new.pack(fill='x')
        self.confirm = Field(pad, t, 'CONFIRM NEW PIN', show='\u2022')
        self.confirm.pack(fill='x', pady=(14, 0))
        digits_only(self, self.new)
        digits_only(self, self.confirm)
        self.error = tk.Label(pad, text='', bg=t['bg'], fg=t['bad'], font=(FONT, 9))
        self.error.pack(anchor='w', pady=(8, 0))
        row = tk.Frame(pad, bg=t['bg'])
        row.pack(side='bottom', fill='x')
        RoundButton(row, 'Save PIN', self.save, t, width=170, height=42).pack(side='right')
        RoundButton(row, 'Cancel', self.cancel, t, style='ghost', width=110, height=42).pack(side='right', padx=(0, 10))
        self.after(120, self.new.entry.focus_set)

    def save(self):
        problem = pin_problem(self.new.get(), self.confirm.get())
        if problem:
            self.error.configure(text=problem)
            return
        self.app.cfg['pin_salt'], self.app.cfg['pin_hash'] = hash_pin(self.new.get())
        self.app.cfg.save()
        self.cancel()


class SettingsDialog(Dialog):
    def __init__(self, app, first_run=False):
        super().__init__(app, 'Finish setup' if first_run else 'Settings', 560, 760)
        t, cfg = self.theme, app.cfg
        self.first_run = first_run
        scroll = ScrollFrame(self, t)
        scroll.pack(fill='both', expand=True)
        pad = tk.Frame(scroll.inner, bg=t['bg'])
        pad.pack(fill='both', expand=True, padx=28, pady=24)
        tk.Label(pad, text='Finish setup' if first_run else 'Settings', bg=t['bg'], fg=t['text'],
                 font=(FONT, 17, 'bold')).pack(anchor='w')
        tk.Label(pad, text='Choose how this PC connects. Only people with the PIN can change these.',
                 bg=t['bg'], fg=t['muted'], font=(FONT, 10), wraplength=480, justify='left').pack(anchor='w', pady=(2, 16))

        # connection
        card = Card(pad, t)
        card.pack(fill='x')
        self.section(card.body, 'CONNECTION')
        self.mode = tk.StringVar(value=cfg['mode'])
        for value, text in (('auto', 'Automatic: find the server on my network'), ('manual', 'Manual: use a fixed address')):
            style_check(tk.Radiobutton(card.body, text=text, value=value, variable=self.mode,
                                       command=self.sync_mode), t).pack(anchor='w', pady=1)
        self.address = Field(card.body, t, 'SERVER ADDRESS (HOST:PORT)')
        self.address.pack(fill='x', pady=(12, 0))
        self.address.set(cfg['address'])
        self.user = Field(card.body, t, 'USERNAME')
        self.user.pack(fill='x', pady=(12, 0))
        self.user.set(cfg['username'])
        self.sync_mode()

        # startup
        card = Card(pad, t)
        card.pack(fill='x', pady=(12, 0))
        self.section(card.body, 'STARTUP AND BACKGROUND')
        self.startup = tk.BooleanVar(value=bool(cfg['start_with_windows']))
        self.tray = tk.BooleanVar(value=bool(cfg['close_to_tray']))
        self.quit_pin = tk.BooleanVar(value=bool(cfg['require_pin_quit']))
        self.sound = tk.BooleanVar(value=bool(cfg['sound']))
        for var, text in ((self.startup, 'Start with Windows and run quietly in the background'),
                          (self.tray, 'Keep running in the tray when the window is closed'),
                          (self.quit_pin, 'Ask for the PIN before quitting or disconnecting')):
            style_check(tk.Checkbutton(card.body, text=text, variable=var), t).pack(anchor='w', pady=2)

        # alerts + security
        card = Card(pad, t)
        card.pack(fill='x', pady=(12, 0))
        self.section(card.body, 'ALERTS AND SECURITY')
        style_check(tk.Checkbutton(card.body, text='Play a sound when an alert arrives', variable=self.sound), t).pack(
            anchor='w', pady=2)
        RoundButton(card.body, 'Change PIN', lambda: ChangePinDialog(app), t, style='ghost', width=140, height=38).pack(
            anchor='w', pady=(12, 0))

        self.error = tk.Label(pad, text='', bg=t['bg'], fg=t['bad'], font=(FONT, 10), wraplength=480, justify='left')
        self.error.pack(anchor='w', pady=(12, 0))
        row = tk.Frame(pad, bg=t['bg'])
        row.pack(fill='x', pady=(8, 0))
        RoundButton(row, 'Save settings', self.save, t, width=180, height=44, size=11).pack(side='right')
        if not first_run:
            RoundButton(row, 'Cancel', self.cancel, t, style='ghost', width=110, height=44).pack(side='right', padx=(0, 10))

    def section(self, parent, text):
        tk.Label(parent, text=text, bg=parent.cget('bg'), fg=self.theme['accent'], font=(FONT, 9, 'bold')).pack(
            anchor='w', pady=(0, 8))

    def sync_mode(self):
        self.address.set_enabled(self.mode.get() == 'manual')

    def save(self):
        username, address, mode = self.user.get(), self.address.get(), self.mode.get()
        if not username:
            self.error.configure(text='Enter the username the server expects.')
            return
        if mode == 'manual':
            host, port = split_address(address)
            if not host or not port:
                self.error.configure(text='Manual mode needs an address like 192.168.1.20:8080.')
                return
            address = f'{host}:{port}'
        cfg = self.app.cfg
        changed = (cfg['mode'], cfg['address'], cfg['username']) != (mode, address, username)
        cfg['mode'], cfg['address'], cfg['username'] = mode, address, username
        cfg['start_with_windows'], cfg['close_to_tray'] = self.startup.get(), self.tray.get()
        cfg['require_pin_quit'], cfg['sound'] = self.quit_pin.get(), self.sound.get()
        cfg.save()
        set_startup(bool(cfg['start_with_windows']))
        self.cancel()
        self.app.settings_saved(changed or self.first_run)


# --------------------------------------------------------------------------
# Main application
# --------------------------------------------------------------------------
class ProApp:
    def __init__(self, root, background=False):
        self.root = root
        self.theme = t = palette('pro')
        self.cfg = Config()
        self.background = background
        root.title('AdminRouter Pro')
        root.configure(bg=t['bg'])
        root.resizable(False, False)
        apply_window_icon(root)
        center_window(root, WIDTH, HEIGHT)
        root.withdraw()

        self.presenter = AlertPresenter(root, sound=lambda: bool(self.cfg['sound']))
        self.feed = AlertFeed(load_client_id(APP_NAME), device_name(),
                              on_alert=lambda alert: self.ui(self.handle_alert, alert),
                              on_state=lambda kind, text: self.ui(self.on_state, kind, text))
        self.history, self.page = [], None
        self.want_connected, self.splash_active = False, False
        self.state_text, self.state_level = 'Not connected', 'muted'
        self.pin_fail, self.lock_until = 0, 0.0
        self.tray = None
        root.protocol('WM_DELETE_WINDOW', self.on_close_clicked)
        self.start_tray()

        if background and self.cfg.activated and self.cfg.has_pin:
            self.start_connection()                       # no window, no splash: just monitor
        else:
            self.splash_active = True
            Splash(root, t, self.after_splash)

    def ui(self, fn, *args):
        try:
            self.root.after(0, lambda: fn(*args))
        except (tk.TclError, RuntimeError):
            pass

    # ---------- window / tray ----------
    def after_splash(self):
        self.splash_active = False
        self.root.deiconify()
        self.root.lift()
        self.route()

    def show_window(self):
        if self.splash_active:
            return
        if self.page is None:
            self.route()
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def start_tray(self):
        try:
            import pystray
            from PIL import Image, ImageDraw
        except Exception:  # noqa: BLE001 - tray libraries not installed
            return
        try:
            ico = resource_path('app.ico')
            if os.path.exists(ico):
                image = Image.open(ico).convert('RGBA')
            else:
                image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
                ImageDraw.Draw(image).ellipse((4, 4, 60, 60), fill=self.theme['accent'])
            menu = pystray.Menu(
                pystray.MenuItem('Open AdminRouter Pro', lambda icon, item: self.ui(self.show_window), default=True),
                pystray.MenuItem('Quit', lambda icon, item: self.ui(self.quit_requested)))
            self.tray = pystray.Icon(APP_NAME, image, 'AdminRouter Pro', menu)
            self.tray.run_detached()
        except Exception:  # noqa: BLE001 - no system tray available
            self.tray = None

    def update_tray_title(self):
        if self.tray:
            try:
                self.tray.title = f'AdminRouter Pro: {self.state_text}'[:127]
            except Exception:  # noqa: BLE001
                pass

    def on_close_clicked(self):
        if self.cfg['close_to_tray']:
            self.root.withdraw()
        else:
            self.quit_requested()

    def quit_requested(self):
        if self.cfg['require_pin_quit'] and self.cfg.has_pin:
            PinDialog(self, 'Enter your PIN to quit AdminRouter Pro. Alerts will stop until it is started again.',
                      self.quit_now, topmost=True)
        else:
            self.quit_now()

    def quit_now(self):
        self.feed.stop()
        self.presenter.clear()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:  # noqa: BLE001
                pass
        self.root.destroy()

    # ---------- PIN ----------
    def check_pin(self, pin):
        now = time.time()
        if now < self.lock_until:
            return False, f'Too many attempts. Try again in {int(self.lock_until - now) + 1}s.'
        if pin_matches(pin, self.cfg['pin_salt'], self.cfg['pin_hash']):
            self.pin_fail = 0
            return True, ''
        self.pin_fail += 1
        if self.pin_fail >= 3:
            self.pin_fail, self.lock_until = 0, now + 30
            return False, 'Too many attempts. Locked for 30 seconds.'
        return False, 'Incorrect PIN.'

    # ---------- pages ----------
    def clear_page(self):
        if self.page is not None:
            self.page.destroy()
        self.page = tk.Frame(self.root, bg=self.theme['bg'])
        self.page.pack(fill='both', expand=True)
        return self.page

    def route(self):
        if not self.cfg.activated:
            self.show_activation()
        elif not self.cfg.has_pin:
            self.show_pin_setup()
        else:
            self.show_main()
            if not self.want_connected:
                self.start_connection()

    def hero(self, page, title, subtitle):
        t = self.theme
        frame = tk.Frame(page, bg=t['bg'])
        frame.pack(fill='x', padx=40, pady=(70, 0))
        draw_logo(frame, t, size=72).pack()
        tk.Label(frame, text=title, bg=t['bg'], fg=t['text'], font=(FONT, 20, 'bold')).pack(pady=(18, 4))
        tk.Label(frame, text=subtitle, bg=t['bg'], fg=t['muted'], font=(FONT, 10), wraplength=420, justify='center').pack()
        return frame

    def show_activation(self):
        t = self.theme
        page = self.clear_page()
        self.hero(page, 'Activate AdminRouter Pro', 'Enter your product key to continue. You only need to do this once.')
        card = Card(page, t, pad=20)
        card.pack(fill='x', padx=40, pady=(26, 0))
        field = Field(card.body, t, 'PRODUCT KEY')
        field.pack(fill='x')
        error = tk.Label(card.body, text='', bg=t['card'], fg=t['bad'], font=(FONT, 9))
        error.pack(anchor='w', pady=(8, 10))
        state = {'fails': 0, 'until': 0.0}

        def activate():
            if time.time() < state['until']:
                error.configure(text='Please wait a few seconds before trying again.')
                return
            if key_matches(field.get()):
                self.cfg['key_hash'] = KEY_HASH
                self.cfg.save()
                self.route()
                return
            state['fails'] += 1
            if state['fails'] >= 5:
                state['fails'], state['until'] = 0, time.time() + 15
            error.configure(text='That key is not valid. Check it and try again.')

        RoundButton(card.body, 'Activate', activate, t, width=434, height=46, size=11).pack()
        field.entry.bind('<Return>', lambda e: activate())
        field.entry.focus_set()

    def show_pin_setup(self):
        t = self.theme
        page = self.clear_page()
        self.hero(page, 'Create a settings PIN',
                  'Only people who know this PIN can change the server address, startup options or quit the app.')
        card = Card(page, t, pad=20)
        card.pack(fill='x', padx=40, pady=(26, 0))
        pin = Field(card.body, t, 'NEW PIN (4 TO 8 DIGITS)', show='\u2022')
        pin.pack(fill='x')
        confirm = Field(card.body, t, 'CONFIRM PIN', show='\u2022')
        confirm.pack(fill='x', pady=(14, 0))
        digits_only(self.root, pin)
        digits_only(self.root, confirm)
        error = tk.Label(card.body, text='', bg=t['card'], fg=t['bad'], font=(FONT, 9))
        error.pack(anchor='w', pady=(8, 10))

        def save():
            problem = pin_problem(pin.get(), confirm.get())
            if problem:
                error.configure(text=problem)
                return
            self.cfg['pin_salt'], self.cfg['pin_hash'] = hash_pin(pin.get())
            self.cfg.save()
            self.show_main()
            SettingsDialog(self, first_run=True)

        RoundButton(card.body, 'Save PIN and continue', save, t, width=434, height=46, size=11).pack()
        confirm.entry.bind('<Return>', lambda e: save())
        pin.entry.focus_set()

    def show_main(self):
        t = self.theme
        page = self.clear_page()
        pad = tk.Frame(page, bg=t['bg'])
        pad.pack(fill='both', expand=True, padx=28, pady=24)
        header = make_header(pad, t, 'AdminRouter', 'Alert monitor', badge='PRO')
        header.pack(fill='x')
        RoundButton(header.right, 'Settings', self.open_settings, t, style='ghost', width=104, height=38).pack()

        card = Card(pad, t, pad=18)
        card.pack(fill='x', pady=(22, 12))
        top = tk.Frame(card.body, bg=t['card'])
        top.pack(fill='x')
        self.status = StatusLine(top, t, size=11)
        self.status.pack(side='left')
        self.spinner = Spinner(top, t, size=22, thickness=3)
        self.spinner.pack(side='right')
        grid = tk.Frame(card.body, bg=t['card'])
        grid.pack(fill='x', pady=(14, 0))
        self.details = {}
        for row, name in enumerate(('Server', 'User', 'This device')):
            tk.Label(grid, text=name, bg=t['card'], fg=t['muted'], font=(FONT, 9), width=11, anchor='w').grid(row=row, column=0, sticky='w', pady=2)
            value = tk.Label(grid, text='', bg=t['card'], fg=t['text'], font=(FONT, 10), anchor='w')
            value.grid(row=row, column=1, sticky='w')
            self.details[name] = value

        self.connect_button = RoundButton(pad, 'Connect', self.toggle_connection, t, width=504, height=46, size=11)
        self.connect_button.pack(pady=(0, 18))

        head = tk.Frame(pad, bg=t['bg'])
        head.pack(fill='x')
        tk.Label(head, text='Recent alerts', bg=t['bg'], fg=t['text'], font=(FONT, 12, 'bold')).pack(side='left')
        RoundButton(head, 'Clear', self.clear_history, t, style='ghost', width=76, height=30, size=9).pack(side='right')
        box = Card(pad, t, pad=12)
        box.pack(fill='both', expand=True, pady=(8, 0))
        self.history_text = tk.Text(box.body, height=8, bg=t['card'], fg=t['text'], relief='flat', wrap='word',
                                    font=(FONT, 10), state='disabled', highlightthickness=0, bd=0, cursor='arrow',
                                    spacing3=6, insertwidth=0)
        self.history_text.pack(fill='both', expand=True)
        self.history_text.tag_configure('time', foreground=t['muted'], font=(FONT, 9))
        self.history_text.tag_configure('empty', foreground=t['muted'])
        self.footer = tk.Label(pad, text='', bg=t['bg'], fg=t['muted'], font=(FONT, 9), wraplength=500, justify='left')
        self.footer.pack(anchor='w', pady=(12, 0))
        self.render_history()
        self.refresh_main()

    def refresh_main(self):
        if self.page is None or not hasattr(self, 'status') or not self.status.winfo_exists():
            return
        cfg = self.cfg
        self.status.set(self.state_text, self.state_level)
        self.connect_button.set_text('Disconnect' if self.want_connected else 'Connect')
        self.connect_button.style = 'ghost' if self.want_connected else 'primary'
        self.connect_button.set_enabled(bool(cfg['username']))
        server = (self.feed.address if self.state_level == 'good' and self.feed.address else
                  ('Automatic search' if cfg['mode'] == 'auto' else cfg['address'] or 'Not set'))
        self.details['Server'].configure(text=server)
        self.details['User'].configure(text=cfg['username'] or 'Not set')
        self.details['This device'].configure(text=device_name())
        parts = ['Starts with Windows' if cfg['start_with_windows'] else 'Does not start with Windows',
                 'keeps running in the tray when closed' if cfg['close_to_tray'] else 'quits when closed']
        self.footer.configure(text=', '.join(parts).capitalize() + '.')
        self._refresh_connected_visuals()

    def _refresh_connected_visuals(self):
        if self.state_level in ('accent', 'warn'):
            self.spinner.start()
        else:
            self.spinner.stop()

    # ---------- connection ----------
    def start_connection(self):
        cfg = self.cfg
        if not cfg['username']:
            self.set_state('Open Settings to finish setup.', 'warn')
            return
        if cfg['mode'] == 'manual' and cfg['address']:
            address = cfg['address']
            resolver = lambda: address  # noqa: E731
        else:
            def resolver():
                found = find_server()
                return f"{found['host']}:{found['port']}" if found else None
        self.want_connected = True
        self.feed.start(cfg['username'], resolver, retry=True)
        self.set_state('Looking for the server...', 'accent')

    def toggle_connection(self):
        if not self.want_connected:
            self.start_connection()
        elif self.cfg['require_pin_quit'] and self.cfg.has_pin:
            PinDialog(self, 'Disconnecting stops alerts on this PC.', self.disconnect)
        else:
            self.disconnect()

    def disconnect(self):
        self.want_connected = False
        self.feed.stop()
        self.set_state('Disconnected.', 'muted')

    def set_state(self, text, level):
        self.state_text, self.state_level = text, level
        self.update_tray_title()
        self.refresh_main()

    def on_state(self, kind, text):
        if kind == 'connecting':
            self.set_state('Looking for the server...', 'accent')
        elif kind == 'connected':
            self.set_state(f'Connected to {text}', 'good')
        elif kind == 'lost':
            self.set_state(text, 'warn')
        else:                                    # kicked / denied
            self.want_connected = False
            self.presenter.clear()
            self.set_state(text, 'bad')

    # ---------- alerts ----------
    def handle_alert(self, alert):
        self.history.insert(0, (time.strftime('%H:%M:%S'), str(alert.get('message', ''))))
        del self.history[50:]
        self.render_history()
        self.presenter.enqueue(alert)

    def render_history(self):
        if self.page is None or not hasattr(self, 'history_text') or not self.history_text.winfo_exists():
            return
        box = self.history_text
        box.configure(state='normal')
        box.delete('1.0', 'end')
        if not self.history:
            box.insert('end', 'No alerts yet. New ones will appear here.', 'empty')
        for stamp, message in self.history:
            box.insert('end', stamp + '   ', 'time')
            box.insert('end', message + '\n')
        box.configure(state='disabled')

    def clear_history(self):
        self.history.clear()
        self.render_history()

    # ---------- settings ----------
    def open_settings(self):
        PinDialog(self, 'Settings are protected. Enter your PIN to continue.', lambda: SettingsDialog(self))

    def settings_saved(self, reconnect):
        self.refresh_main()
        if reconnect:
            self.feed.stop()
            self.start_connection()


def main():
    background = '--background' in sys.argv
    lock = InstanceLock()
    if not lock.acquire():
        if not background:
            InstanceLock.signal()
        return
    root = tk.Tk()
    app = ProApp(root, background)
    lock.listen(lambda: app.ui(app.show_window))
    root.mainloop()


if __name__ == '__main__':
    main()