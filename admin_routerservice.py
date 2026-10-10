#!/usr/bin/env python3
"""AdminRouter background service (web UI, device discovery, automations)."""
import concurrent.futures
import hashlib
import hmac
import ipaddress
import json
import platform
import re
import secrets
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

BASE = Path(__file__).resolve().parent
SETTINGS_PATH = BASE / 'settings.json'
ALERTS_PATH = BASE / 'alerts.json'
AUTOMATIONS_PATH = BASE / 'automations.json'
KNOWN_PATH = BASE / 'known_devices.json'
DISCOVERY_PORT = 39872
THEMES = ('original', 'classic', 'futuristic')
LOCK = threading.RLock()


# --------------------------------------------------------------------------
# Config / auth
# --------------------------------------------------------------------------
def load_config(sql_path):
    conn = sqlite3.connect(':memory:')
    conn.executescript(sql_path.read_text(encoding='utf-8'))
    row = conn.execute(
        'SELECT username, password_hash, port FROM app_config ORDER BY id DESC LIMIT 1'
    ).fetchone()
    conn.close()
    if not row:
        raise RuntimeError('No configuration row found in SQL file')
    return row


def verify_password(password, stored_hash):
    try:
        algorithm, iterations_text, salt, expected = stored_hash.split('$', 3)
        if algorithm != 'pbkdf2_sha256':
            return False
        digest = hashlib.pbkdf2_hmac(
            'sha256', password.encode('utf-8'), bytes.fromhex(salt), int(iterations_text)
        ).hex()
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(digest, expected)


# --------------------------------------------------------------------------
# Settings / alerts / known devices
# --------------------------------------------------------------------------
def load_settings():
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    theme = data.get('ui_theme', 'original')
    if theme == 'modern':  # old name for the original look
        theme = 'original'
    return {
        'remote_alerting_enabled': bool(data.get('remote_alerting_enabled', False)),
        'ui_theme': theme if theme in THEMES else 'original',
    }


def save_settings(settings):
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + '\n', encoding='utf-8')


SETTINGS = load_settings()


def load_alerts():
    try:
        data = json.loads(ALERTS_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = []
    return data if isinstance(data, list) else []


def save_alerts(alerts):
    ALERTS_PATH.write_text(json.dumps(alerts[-100:], indent=2) + '\n', encoding='utf-8')


def queue_alert(message):
    """Queue an alert for companion apps. Raises if it cannot be queued."""
    if not SETTINGS['remote_alerting_enabled']:
        raise RuntimeError('Remote Alerting is disabled')
    message = str(message).strip()[:500]
    if not message:
        raise ValueError('Message is empty')
    with LOCK:
        alerts = load_alerts()
        alert = {'id': (alerts[-1]['id'] + 1 if alerts else 1), 'message': message, 'created': int(time.time())}
        alerts.append(alert)
        save_alerts(alerts)
    return alert


def load_known():
    try:
        data = json.loads(KNOWN_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    return data if isinstance(data, dict) else {}


KNOWN = load_known()


def remember_devices(devices):
    with LOCK:
        for device in devices:
            KNOWN[device['ip']] = {'hostname': device.get('hostname', 'Unknown device')}
        try:
            KNOWN_PATH.write_text(json.dumps(KNOWN, indent=2) + '\n', encoding='utf-8')
        except OSError:
            pass


def known_ips():
    with LOCK:
        return sorted(KNOWN, key=lambda ip: ipaddress.ip_address(ip))


def known_list():
    with LOCK:
        return [{'ip': ip, 'hostname': KNOWN[ip].get('hostname', 'Unknown device')} for ip in known_ips()]


# --------------------------------------------------------------------------
# Networking
# --------------------------------------------------------------------------
def local_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(('8.8.8.8', 80))
        address = sock.getsockname()[0]
        sock.close()
        return address
    except OSError:
        return '127.0.0.1'


def hidden_flags():
    return subprocess.CREATE_NO_WINDOW if platform.system() == 'Windows' else 0


def read_neighbour_table():
    commands = [['arp', '-a']]
    if platform.system() != 'Windows':
        commands.insert(0, ['ip', 'neigh'])
    entries = {}
    ip_pattern = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
    mac_pattern = re.compile(r'(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}')
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False, creationflags=hidden_flags())
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in result.stdout.splitlines():
            ip_match = ip_pattern.search(line)
            if ip_match:
                mac_match = mac_pattern.search(line)
                entries[ip_match.group(0)] = mac_match.group(0) if mac_match else 'unknown'
    return entries


def classify_device(ttl, hostname):
    label = (hostname or '').lower()
    if any(value in label for value in ('iphone', 'ipad', 'ios')):
        return 'iPhone/iPad', 'iOS'
    if any(value in label for value in ('android', 'pixel', 'galaxy')):
        return 'Android phone/tablet', 'Android'
    if ttl <= 0:
        return 'Network device', 'Unknown'
    if ttl <= 64:
        return 'Computer/server', 'Linux/macOS/Android'
    if ttl <= 128:
        return 'Computer/laptop', 'Windows'
    return 'Network device', 'Unknown'


def ping_once(target):
    """Ping a validated IP string once. Returns (ok, ttl)."""
    if platform.system() == 'Windows':
        command = ['ping', '-n', '1', '-w', '500', target]
    else:
        command = ['ping', '-c', '1', '-W', '1', target]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False, creationflags=hidden_flags())
    except (OSError, subprocess.TimeoutExpired):
        return False, 0
    if result.returncode != 0:
        return False, 0
    ttl_match = re.search(r'ttl[=|:](\d+)', result.stdout, re.IGNORECASE)
    return True, int(ttl_match.group(1)) if ttl_match else 0


def run_ping(address):
    try:
        target = str(ipaddress.ip_address(address))
    except ValueError:
        return None
    ok, ttl = ping_once(target)
    if not ok:
        return None
    try:
        hostname = socket.gethostbyaddr(target)[0]
    except OSError:
        hostname = 'Unknown device'
    device_type, operating_system = classify_device(ttl, hostname)
    return {'ip': target, 'hostname': hostname, 'os': operating_system, 'type': device_type}


def local_network(address):
    if platform.system() != 'Windows':
        try:
            route = subprocess.run(['ip', '-4', 'route', 'show', 'scope', 'link'], capture_output=True, text=True, timeout=3, check=False, creationflags=hidden_flags())
            for line in route.stdout.splitlines():
                network = line.split()[0]
                if '/' in network and ipaddress.ip_address(address) in ipaddress.ip_network(network, strict=False):
                    route_network = ipaddress.ip_network(network, strict=False)
                    if route_network.num_addresses <= 1024:
                        return route_network
                    return ipaddress.ip_network(f'{address}/24', strict=False)
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            pass
    return ipaddress.ip_network(f'{address}/24', strict=False)


def discover_devices():
    address = local_ip()
    try:
        network = local_network(address)
    except ValueError:
        network = ipaddress.ip_network('127.0.0.0/8')
    mac_addresses = read_neighbour_table()
    candidates = [str(host) for host in network.hosts()]
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        found = [device for device in pool.map(run_ping, candidates) if device]
    for device in found:
        device['mac'] = mac_addresses.get(device['ip'], 'unknown')
    if address not in {device['ip'] for device in found}:
        found.append({'ip': address, 'mac': 'local', 'hostname': socket.gethostname(), 'os': platform.system(), 'type': 'This computer'})
    found.sort(key=lambda device: ipaddress.ip_address(device['ip']))
    remember_devices(found)
    return found


def ping_device(address):
    result = run_ping(address)
    if result is None:
        return {'ok': False, 'ip': address, 'error': 'No response'}
    result['ok'] = True
    return result


# --------------------------------------------------------------------------
# Automation actions
# --------------------------------------------------------------------------
def fail_check(failed, total, action):
    """True when the ping action should count as FAILED."""
    mode = action.get('fail_mode', 'any')
    if mode == 'all':
        return total > 0 and failed == total
    if mode == 'count':
        return failed >= int(action.get('fail_count', 1))
    return failed >= 1


def ping_many(ips):
    with concurrent.futures.ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(lambda ip: ping_once(ip)[0], ips))
    return dict(zip(ips, results))


def run_action(action):
    """Run one action. Returns (success, log text)."""
    kind = action['type']
    if kind == 'alert':
        try:
            queue_alert(action['message'])
            return True, f"Alert sent: {action['message']}"
        except Exception as exc:  # noqa: BLE001
            return False, f'Alert failed: {exc}'
    if kind == 'ping_all':
        ips = known_ips()
        if not ips:
            discover_devices()
            ips = known_ips()
        label = 'known devices'
    else:
        ips = list(action.get('ips', []))
        label = 'selected devices'
    if not ips:
        return False, 'No devices to ping'
    results = ping_many(ips)
    failed = [ip for ip, ok in results.items() if not ok]
    broken = fail_check(len(failed), len(ips), action)
    text = f'Pinged {len(ips)} {label}, {len(failed)} did not respond'
    if failed:
        text += ' (' + ', '.join(failed) + ')'
    return (not broken), text


def describe_action(action):
    return {'ping_all': 'Ping all', 'ping': 'Ping selected', 'alert': 'Send alert'}.get(action['type'], action['type'])


def execute_rule(rule):
    """Run the action chain. AND = always run; OR = run only if everything so far failed."""
    lines = []
    outcome = None
    for index, action in enumerate(rule['actions']):
        name = describe_action(action)
        if index == 0 or action.get('op', 'AND') == 'AND':
            ok, text = run_action(action)
            outcome = ok if index == 0 else (outcome and ok)
        else:
            if outcome:
                lines.append(f'{name}: skipped (OR, previous step succeeded)')
                continue
            ok, text = run_action(action)
            outcome = ok
        lines.append(f"{name}: {'OK' if ok else 'FAILED'} - {text}")
    return bool(outcome), lines


# --------------------------------------------------------------------------
# Automation validation
# --------------------------------------------------------------------------
def clean_rule(data, rule_id, other_ids):
    name = str(data.get('name', '')).strip()[:60] or 'Untitled automation'
    triggers = []
    for index, raw in enumerate((data.get('triggers') or [])[:10]):
        kind = raw.get('type')
        item = {'id': secrets.token_hex(4), 'type': kind, 'op': 'OR' if str(raw.get('op', 'AND')).upper() == 'OR' else 'AND'}
        if index == 0:
            item['op'] = 'AND'
        if kind == 'interval':
            minutes = int(raw.get('minutes'))
            if not 1 <= minutes <= 10080:
                raise ValueError('Minutes must be between 1 and 10080')
            item['minutes'] = minutes
        elif kind == 'daily':
            time_text = str(raw.get('time', ''))
            if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', time_text):
                raise ValueError('Daily time must look like 09:30')
            item['time'] = time_text
        elif kind == 'result':
            ref = str(raw.get('automation_id', ''))
            if ref == rule_id or ref not in other_ids:
                raise ValueError('Pick another automation for the "when" trigger')
            on = 'success' if raw.get('on') == 'success' else 'fail'
            item.update({'automation_id': ref, 'on': on})
        else:
            raise ValueError('Unknown trigger type')
        triggers.append(item)
    if not triggers:
        raise ValueError('Add at least one trigger')

    actions = []
    for index, raw in enumerate((data.get('actions') or [])[:10]):
        kind = raw.get('type')
        item = {'type': kind, 'op': 'OR' if str(raw.get('op', 'AND')).upper() == 'OR' else 'AND'}
        if index == 0:
            item['op'] = 'AND'
        if kind in ('ping', 'ping_all'):
            mode = raw.get('fail_mode', 'any')
            item['fail_mode'] = mode if mode in ('any', 'all', 'count') else 'any'
            count = int(raw.get('fail_count', 1) or 1)
            item['fail_count'] = max(1, min(count, 254))
            if kind == 'ping':
                ips = []
                for ip in raw.get('ips') or []:
                    ips.append(str(ipaddress.ip_address(str(ip))))
                if not ips:
                    raise ValueError('Select at least one device to ping')
                item['ips'] = ips
        elif kind == 'alert':
            message = str(raw.get('message', '')).strip()[:500]
            if not message:
                raise ValueError('Alert message cannot be empty')
            item['message'] = message
        else:
            raise ValueError('Unknown action type')
        actions.append(item)
    if not actions:
        raise ValueError('Add at least one action')
    return {'id': rule_id, 'name': name, 'enabled': True, 'triggers': triggers, 'actions': actions}


# --------------------------------------------------------------------------
# Automation engine
# --------------------------------------------------------------------------
class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.rules = self._load()
        self.rt = {}          # per-rule runtime state
        self.results = {}     # rule id -> last finished run
        self.counter = 0
        self.running = set()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)

    def _load(self):
        try:
            data = json.loads(AUTOMATIONS_PATH.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            data = []
        return data if isinstance(data, list) else []

    def _persist(self):
        AUTOMATIONS_PATH.write_text(json.dumps(self.rules, indent=2) + '\n', encoding='utf-8')

    def start(self):
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            time.sleep(1)
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                pass

    # ---- trigger state -------------------------------------------------
    def _state(self, rule):
        st = self.rt.setdefault(rule['id'], {'next_due': {}, 'daily_done': {}, 'consumed': {}, 'last_start': 0, 'last_run': None})
        now = time.time()
        dt = datetime.now(timezone.utc)
        for t in rule['triggers']:
            tid = t['id']
            if t['type'] == 'interval' and tid not in st['next_due']:
                st['next_due'][tid] = now + t['minutes'] * 60
            elif t['type'] == 'daily' and tid not in st['daily_done']:
                hour, minute = map(int, t['time'].split(':'))
                st['daily_done'][tid] = dt.strftime('%Y-%m-%d') if (dt.hour, dt.minute) >= (hour, minute) else ''
            elif t['type'] == 'result' and tid not in st['consumed']:
                st['consumed'][tid] = self.results.get(t['automation_id'], {}).get('run_id', 0)
        return st

    def _active(self, t, st, now, dt):
        if t['type'] == 'interval':
            return now >= st['next_due'][t['id']]
        if t['type'] == 'daily':
            hour, minute = map(int, t['time'].split(':'))
            return (dt.hour, dt.minute) >= (hour, minute) and st['daily_done'][t['id']] != dt.strftime('%Y-%m-%d')
        result = self.results.get(t['automation_id'])
        return bool(result) and result['run_id'] > st['consumed'][t['id']] and result['ok'] == (t['on'] == 'success')

    def _satisfied_group(self, rule, st, now, dt):
        """Triggers are OR-ed groups of AND-ed triggers (AND binds tighter)."""
        groups = [[]]
        for index, t in enumerate(rule['triggers']):
            if index > 0 and t.get('op') == 'OR':
                groups.append([])
            groups[-1].append(t)
        for group in groups:
            if group and all(self._active(t, st, now, dt) for t in group):
                return group
        return None

    def _consume(self, group, st, now, dt):
        for t in group:
            if t['type'] == 'interval':
                st['next_due'][t['id']] = now + t['minutes'] * 60
            elif t['type'] == 'daily':
                st['daily_done'][t['id']] = dt.strftime('%Y-%m-%d')
            else:
                st['consumed'][t['id']] = self.results[t['automation_id']]['run_id']

    # ---- running -------------------------------------------------------
    def _tick(self):
        now = time.time()
        dt = datetime.now(timezone.utc)
        with self.lock:
            for rule in self.rules:
                if not rule.get('enabled') or rule['id'] in self.running:
                    continue
                st = self._state(rule)
                if now - st['last_start'] < 5:
                    continue
                group = self._satisfied_group(rule, st, now, dt)
                if group:
                    self._consume(group, st, now, dt)
                    self._launch(rule, st, 'trigger')

    def _launch(self, rule, st, reason):
        self.running.add(rule['id'])
        st['last_start'] = time.time()
        self.pool.submit(self._run, json.loads(json.dumps(rule)), reason)

    def _run(self, rule, reason):
        started = time.time()
        try:
            ok, lines = execute_rule(rule)
        except Exception as exc:  # noqa: BLE001
            ok, lines = False, [f'Error: {exc}']
        with self.lock:
            self.counter += 1
            record = {'run_id': self.counter, 'ok': ok, 'started': started, 'finished': time.time(), 'reason': reason, 'lines': lines}
            self.results[rule['id']] = record
            if rule['id'] in self.rt:
                self.rt[rule['id']]['last_run'] = record
            self.running.discard(rule['id'])

    # ---- API helpers ---------------------------------------------------
    def snapshot(self):
        with self.lock:
            out = []
            for rule in self.rules:
                st = self.rt.get(rule['id'], {})
                out.append({**rule, 'running': rule['id'] in self.running, 'last_run': st.get('last_run')})
            return out

    def save(self, data):
        with self.lock:
            ids = {r['id'] for r in self.rules}
            rule_id = str(data.get('id') or '')
            if rule_id and rule_id not in ids:
                raise ValueError('Unknown automation')
            rule_id = rule_id or secrets.token_hex(4)
            rule = clean_rule(data, rule_id, ids - {rule_id})
            rule['enabled'] = bool(data.get('enabled', True))
            for index, existing in enumerate(self.rules):
                if existing['id'] == rule_id:
                    self.rules[index] = rule
                    break
            else:
                self.rules.append(rule)
            self.rt.pop(rule_id, None)
            self._state(rule)
            self._persist()

    def delete(self, rule_id):
        with self.lock:
            self.rules = [r for r in self.rules if r['id'] != rule_id]
            self.rt.pop(rule_id, None)
            self._persist()

    def toggle(self, rule_id, enabled):
        with self.lock:
            for rule in self.rules:
                if rule['id'] == rule_id:
                    rule['enabled'] = bool(enabled)
                    self.rt.pop(rule_id, None)
                    self._state(rule)
                    self._persist()
                    return
            raise ValueError('Unknown automation')

    def run_now(self, rule_id):
        with self.lock:
            for rule in self.rules:
                if rule['id'] == rule_id:
                    if rule_id in self.running:
                        raise ValueError('Already running')
                    self._launch(rule, self._state(rule), 'manual')
                    return
            raise ValueError('Unknown automation')


ENGINE = Engine()


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    username = ''
    password_hash = ''
    port = 0
    public_index = BASE / 'public' / 'index.html'
    sessions = set()

    def read_json(self, limit):
        length = min(int(self.headers.get('Content-Length', '0')), limit)
        data = json.loads(self.rfile.read(length))
        if not isinstance(data, dict):
            raise ValueError('Expected an object')
        return data

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/info/creditngtgtu':
            self.send_json({'service': 'AdminRouter', 'port': self.port, 'username': self.username, 'remote_alerting': SETTINGS['remote_alerting_enabled']})
            return
        if path in {'/', '/index.html'}:
            self.serve_file('index.html')
            return
        if path == '/dashboard.html':
            if not self.is_authenticated():
                self.redirect('/')
                return
            self.serve_file('dashboard.html')
            return
        if path == '/remote-alerting.html':
            if not self.is_authenticated() or not SETTINGS['remote_alerting_enabled']:
                self.redirect('/dashboard.html')
                return
            self.serve_file('remote-alerting.html')
            return
        if path == '/api/settings':
            if not self.is_authenticated():
                self.send_json({'error': 'Authentication required'}, 401)
                return
            self.send_json(SETTINGS)
            return
        if path == '/api/alerts':
            if not self.is_authenticated() and not self.client_authorized():
                self.send_json({'error': 'Authentication required'}, 401)
                return
            query = parse_qs(urlsplit(self.path).query)
            try:
                after = int(query.get('after', ['0'])[0])
            except ValueError:
                after = 0
            self.send_json({'alerts': [alert for alert in load_alerts() if alert['id'] > after]})
            return
        if path == '/api/devices':
            if not self.is_authenticated():
                self.send_json({'error': 'Authentication required'}, 401)
                return
            self.send_json({'devices': discover_devices()})
            return
        if path == '/api/automations':
            if not self.is_authenticated():
                self.send_json({'error': 'Authentication required'}, 401)
                return
            self.send_json({'automations': ENGINE.snapshot(), 'known_devices': known_list()})
            return
        if path == '/api/status':
            if not self.is_authenticated():
                self.send_json({'authenticated': False}, 401)
                return
            self.send_json({'authenticated': True, 'user': self.username})
            return
        self.send_error(404)

    def do_POST(self):
        path = urlsplit(self.path).path
        if not self.is_authenticated() and path != '/api/login':
            self.send_json({'error': 'Authentication required'}, 401)
            return
        if path == '/api/settings':
            try:
                data = self.read_json(1024)
                if 'remote_alerting_enabled' in data:
                    SETTINGS['remote_alerting_enabled'] = bool(data['remote_alerting_enabled'])
                if data.get('ui_theme') in THEMES:
                    SETTINGS['ui_theme'] = data['ui_theme']
                save_settings(SETTINGS)
            except (ValueError, TypeError, OSError):
                self.send_json({'ok': False, 'error': 'Invalid settings'}, 400)
                return
            self.send_json({'ok': True, **SETTINGS})
            return
        if path == '/api/alerts':
            if not SETTINGS['remote_alerting_enabled']:
                self.send_json({'ok': False, 'error': 'Remote Alerting is disabled'}, 403)
                return
            try:
                data = self.read_json(4096)
                alert = queue_alert(data.get('message', ''))
            except (ValueError, TypeError, OSError):
                self.send_json({'ok': False, 'error': 'Invalid alert (message cannot be empty)'}, 400)
                return
            self.send_json({'ok': True, 'queued': True, 'id': alert['id']})
            return
        if path == '/api/ping':
            try:
                data = self.read_json(1024)
                result = ping_device(str(data.get('ip', '')))
            except (ValueError, TypeError):
                self.send_json({'ok': False, 'error': 'Invalid request'}, 400)
                return
            self.send_json(result)
            return
        if path.startswith('/api/automations'):
            try:
                data = self.read_json(32768)
                if path == '/api/automations':
                    ENGINE.save(data)
                elif path == '/api/automations/delete':
                    ENGINE.delete(str(data.get('id', '')))
                elif path == '/api/automations/toggle':
                    ENGINE.toggle(str(data.get('id', '')), data.get('enabled'))
                elif path == '/api/automations/run':
                    ENGINE.run_now(str(data.get('id', '')))
                else:
                    self.send_error(404)
                    return
            except (ValueError, TypeError, AttributeError, OSError) as exc:
                self.send_json({'ok': False, 'error': str(exc) or 'Invalid automation'}, 400)
                return
            self.send_json({'ok': True, 'automations': ENGINE.snapshot(), 'known_devices': known_list()})
            return
        if path != '/api/login':
            self.send_error(404)
            return
        try:
            data = self.read_json(4096)
            username = str(data.get('username', ''))
            password = str(data.get('password', ''))
        except (ValueError, TypeError):
            self.send_json({'ok': False, 'error': 'Invalid request'}, 400)
            return
        if not hmac.compare_digest(username, self.username) or not verify_password(password, self.password_hash):
            self.send_json({'ok': False, 'error': 'Invalid username or password'}, 401)
            return
        token = secrets.token_urlsafe(32)
        self.sessions.add(token)
        self.send_json({'ok': True, 'user': self.username}, headers={'Set-Cookie': f'admin_session={token}; HttpOnly; SameSite=Strict; Path=/'})

    def is_authenticated(self):
        cookie = self.headers.get('Cookie', '')
        cookies = {
            item.split('=', 1)[0].strip(): item.split('=', 1)[1]
            for item in cookie.split(';') if '=' in item
        }
        return cookies.get('admin_session') in self.sessions

    def client_authorized(self):
        return hmac.compare_digest(self.headers.get('X-AdminRouter-Username', ''), self.username)

    def serve_file(self, filename):
        try:
            payload = (self.public_index.parent / filename).read_bytes()
        except OSError:
            self.send_error(500, f'public/{filename} is missing')
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def redirect(self, location):
        self.send_response(302)
        self.send_header('Location', location)
        self.end_headers()

    def send_json(self, value, status=200, headers=None):
        payload = json.dumps(value).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        for name, header_value in (headers or {}).items():
            self.send_header(name, header_value)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: adminrouter_service.py <config.sql>')
    username, password_hash, port = load_config(Path(sys.argv[1]))
    Handler.username = username
    Handler.password_hash = password_hash
    Handler.port = int(port)
    ENGINE.start()
    discovery_server = ThreadingHTTPServer(('0.0.0.0', DISCOVERY_PORT), Handler)
    threading.Thread(target=discovery_server.serve_forever, daemon=True).start()
    server = ThreadingHTTPServer(('0.0.0.0', int(port)), Handler)
    server.serve_forever()