#!/usr/bin/env python3
import concurrent.futures
import getpass
import hashlib
import hmac
import ipaddress
import json
import os
import platform
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

PACKAGE_SET = ["flask", "bcrypt", "zeroconf"]
PBKDF2_ITERATIONS = 390000
WHITE_TEXT = "\033[97m"
RESET = "\033[0m"


def enable_terminal_colors() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            ctypes.windll.kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except (AttributeError, OSError):
        return


def blue(text: str) -> str:
    return f"{WHITE_TEXT}{text}{RESET}"


def setup_prompt(text: str) -> str:
    return blue(text)


def is_supported_platform() -> bool:
    return platform.system() in {"Windows", "Linux"}


def prompt_yes_no(prompt: str) -> bool:
    while True:
        answer = input(setup_prompt(f"{prompt} [y/n]: ")).strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer with y or n.")


def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt, expected = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations_text)
        ).hex()
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(digest, expected)


def sql_escape(value: str) -> str:
    return value.replace("'", "''")


def generate_sql_file(path: Path, username: str, password_hash: str, port: int) -> None:
    sql = (
        "CREATE TABLE IF NOT EXISTS app_config (\n"
        "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
        "    username TEXT NOT NULL,\n"
        "    password_hash TEXT NOT NULL,\n"
        "    port INTEGER NOT NULL\n"
        ");\n"
        "INSERT INTO app_config (username, password_hash, port) VALUES (\n"
        f"    '{sql_escape(username)}',\n"
        f"    '{sql_escape(password_hash)}',\n"
        f"    {port}\n"
        ");\n"
    )
    path.write_text(sql, encoding="utf-8")


def create_service_script(path: Path) -> None:
    script = '''#!/usr/bin/env python3
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
import time
import threading
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SETTINGS_PATH = Path(__file__).resolve().parent / 'settings.json'
ALERTS_PATH = Path(__file__).resolve().parent / 'alerts.json'
DISCOVERY_PORT = 39872


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


def load_settings():
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    return {'remote_alerting_enabled': bool(data.get('remote_alerting_enabled', False))}


def save_settings(settings):
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2) + '\\n', encoding='utf-8')


def load_alerts():
    try:
        data = json.loads(ALERTS_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = []
    return data if isinstance(data, list) else []


def save_alerts(alerts):
    ALERTS_PATH.write_text(json.dumps(alerts[-100:], indent=2) + '\\n', encoding='utf-8')


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
    ip_pattern = re.compile(r'\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b')
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
        return 'iPhone/iPad', 'iOS (hostname guess)'
    if any(value in label for value in ('android', 'pixel', 'galaxy')):
        return 'Android phone/tablet', 'Android (hostname guess)'
    if ttl <= 64:
        return 'Computer/server', 'Linux/macOS/Android (TTL guess)'
    if ttl <= 128:
        return 'Computer/laptop', 'Windows (TTL guess)'
    return 'Network device', 'Unknown OS'


def run_ping(address):
    try:
        target = str(ipaddress.ip_address(address))
    except ValueError:
        return None
    if platform.system() == 'Windows':
        command = ['ping', '-n', '1', '-w', '500', target]
        creationflags = subprocess.CREATE_NO_WINDOW
    else:
        command = ['ping', '-c', '1', '-W', '1', target]
        creationflags = 0
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=3, check=False,
            creationflags=creationflags,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    ttl_match = re.search(r'ttl[=|:](\\d+)', result.stdout, re.IGNORECASE)
    ttl = int(ttl_match.group(1)) if ttl_match else 0
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
    return sorted(found, key=lambda device: ipaddress.ip_address(device['ip']))


def ping_device(address):
    result = run_ping(address)
    if result is None:
        return {'ok': False, 'ip': address, 'error': 'No response'}
    result['ok'] = True
    return result


class Handler(BaseHTTPRequestHandler):
    username = ''
    password_hash = ''
    port = 0
    settings = load_settings()
    public_index = Path(__file__).resolve().parent / 'public' / 'index.html'
    sessions = set()

    def do_GET(self):
        if self.path == '/info/creditngtgtu':
            self.send_json({'service': 'AdminRouter', 'port': self.port, 'username': self.username, 'remote_alerting': self.settings['remote_alerting_enabled']})
            return
        if self.path in {'/', '/index.html'}:
            self.serve_file('index.html')
            return
        if self.path == '/dashboard.html':
            if not self.is_authenticated():
                self.redirect('/')
                return
            self.serve_file('dashboard.html')
            return
        if self.path == '/remote-alerting.html':
            if not self.is_authenticated() or not self.settings['remote_alerting_enabled']:
                self.redirect('/dashboard.html')
                return
            self.serve_file('remote-alerting.html')
            return
        if self.path == '/api/settings':
            if not self.is_authenticated():
                self.send_json({'error': 'Authentication required'}, 401)
                return
            self.send_json(self.settings)
            return
        if self.path.startswith('/api/alerts'):
            if not self.is_authenticated() and not self.client_authorized():
                self.send_json({'error': 'Authentication required'}, 401)
                return
            query = parse_qs(urlsplit(self.path).query)
            after = int(query.get('after', ['0'])[0]) if query.get('after') else 0
            self.send_json({'alerts': [alert for alert in load_alerts() if alert['id'] > after]})
            return
        if self.path == '/api/devices':
            if not self.is_authenticated():
                self.send_json({'error': 'Authentication required'}, 401)
                return
            self.send_json({'devices': discover_devices()})
            return
        if self.path == '/api/status':
            if not self.is_authenticated():
                self.send_json({'authenticated': False}, 401)
                return
            self.send_json({'authenticated': True, 'user': self.username})
            return
        self.send_error(404)

    def do_POST(self):
        if not self.is_authenticated() and self.path != '/api/login':
            self.send_json({'error': 'Authentication required'}, 401)
            return
        if self.path == '/api/settings':
            try:
                length = min(int(self.headers.get('Content-Length', '0')), 1024)
                data = json.loads(self.rfile.read(length))
                self.settings['remote_alerting_enabled'] = bool(data.get('remote_alerting_enabled', False))
                save_settings(self.settings)
            except (ValueError, TypeError, json.JSONDecodeError, OSError):
                self.send_json({'ok': False, 'error': 'Invalid settings'}, 400)
                return
            self.send_json({'ok': True, **self.settings})
            return
        if self.path == '/api/alerts':
            if not self.settings['remote_alerting_enabled']:
                self.send_json({'ok': False, 'error': 'Remote Alerting is disabled'}, 403)
                return
            try:
                length = min(int(self.headers.get('Content-Length', '0')), 2048)
                data = json.loads(self.rfile.read(length))
                message = str(data.get('message', '')).strip()
                if not message:
                    raise ValueError('Message is empty')
                alerts = load_alerts()
                alert = {'id': (alerts[-1]['id'] + 1 if alerts else 1), 'message': message, 'created': int(time.time())}
                alerts.append(alert)
                save_alerts(alerts)
            except (ValueError, TypeError, json.JSONDecodeError, OSError):
                self.send_json({'ok': False, 'error': 'Invalid alert'}, 400)
                return
            self.send_json({'ok': True, 'queued': True, 'id': alert['id']})
            return
        if self.path == '/api/ping':
            try:
                length = min(int(self.headers.get('Content-Length', '0')), 1024)
                data = json.loads(self.rfile.read(length))
                result = ping_device(str(data.get('ip', '')))
            except (ValueError, TypeError, json.JSONDecodeError):
                self.send_json({'ok': False, 'error': 'Invalid request'}, 400)
                return
            self.send_json(result, 200 if result['ok'] else 400)
            return
        if self.path != '/api/login':
            self.send_error(404)
            return
        try:
            length = min(int(self.headers.get('Content-Length', '0')), 4096)
            data = json.loads(self.rfile.read(length))
            username = str(data.get('username', ''))
            password = str(data.get('password', ''))
        except (ValueError, TypeError, json.JSONDecodeError):
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
        for name, value in (headers or {}).items():
            self.send_header(name, value)
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
    discovery_server = ThreadingHTTPServer(('0.0.0.0', DISCOVERY_PORT), Handler)
    threading.Thread(target=discovery_server.serve_forever, daemon=True).start()
    server = ThreadingHTTPServer(('0.0.0.0', int(port)), Handler)
    server.serve_forever()
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def configure_windows_startup(service_script: Path, sql_path: Path) -> None:
    startup_dir = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    startup_dir.mkdir(parents=True, exist_ok=True)
    startup_file = startup_dir / "AdminRouter.bat"
    startup_file.write_text(f'start "" /B "{sys.executable}" "{service_script}" "{sql_path}"\n', encoding="utf-8")


def configure_linux_startup(service_script: Path, sql_path: Path) -> None:
    systemd = shutil.which("systemctl")
    if systemd:
        unit_dir = Path.home() / ".config/systemd/user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        unit_file = unit_dir / "adminrouter.service"
        unit_file.write_text(
            "[Unit]\nDescription=AdminRouter background service\n\n[Service]\nType=simple\n"
            f"ExecStart={sys.executable} {service_script} {sql_path}\nRestart=always\n\n"
            "[Install]\nWantedBy=default.target\n",
            encoding="utf-8",
        )
        subprocess.run([systemd, "--user", "daemon-reload"], check=False)
        subprocess.run([systemd, "--user", "enable", "adminrouter.service"], check=False)
        return
    command = f'@reboot {sys.executable} {service_script} {sql_path}'
    current = subprocess.run(["crontab", "-l"], check=False, capture_output=True, text=True)
    lines = [] if current.returncode else current.stdout.splitlines()
    if command not in lines:
        lines.append(command)
        subprocess.run(["crontab", "-"], input="\n".join(lines) + "\n", text=True, check=False)


def run_in_background_now(service_script: Path, sql_path: Path, port: int, log_path: Path) -> bool:
    with open(log_path, "ab") as log_file:
        if platform.system() == "Windows":
            subprocess.Popen(
                [sys.executable, str(service_script), str(sql_path)], stdout=log_file, stderr=log_file,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            subprocess.Popen(
                [sys.executable, str(service_script), str(sql_path)], stdout=log_file, stderr=log_file,
                start_new_session=True,
            )
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def install_packages_if_requested() -> None:
    if prompt_yes_no("Install optional packages (flask, bcrypt, zeroconf)?"):
        subprocess.run([sys.executable, "-m", "pip", "install", *PACKAGE_SET], check=False)


def parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError("Port must be a number") from exc
    if port <= 0 or port > 65535:
        raise ValueError("Port must be between 1 and 65535")
    return port


def get_local_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def get_runtime_dir() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path.home() / ".local/share"
    return base / "AdminRouter"


def print_setup_header() -> None:
    width = 72
    print("\033[2J\033[H\033[44m\033[97m", end="")
    print(" AdminRouter setup ".center(width))
    print("Configure the admin login and web port".center(width))
    print(" " * width)


def main() -> int:
    if not is_supported_platform():
        print("This installer only supports Windows or Linux terminals.")
        return 1
    enable_terminal_colors()
    print_setup_header()
    install_packages_if_requested()
    username = input(setup_prompt("Username: ")).strip()
    while not username:
        username = input(setup_prompt("Username cannot be empty. Username: ")).strip()
    password = getpass.getpass(setup_prompt("Password: "))
    while not password:
        password = getpass.getpass(setup_prompt("Password cannot be empty. Password: "))
    port_text = input(setup_prompt("Port to run on: ")).strip()
    while True:
        try:
            port = parse_port(port_text)
            break
        except ValueError as exc:
            print(str(exc))
            port_text = input(setup_prompt("Port to run on: ")).strip()
    root = Path(__file__).resolve().parent
    runtime_dir = get_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root / "public", runtime_dir / "public", dirs_exist_ok=True)
    sql_path = runtime_dir / "adminrouter_config.sql"
    service_script = runtime_dir / "adminrouter_service.py"
    log_path = runtime_dir / "adminrouter.log"
    generate_sql_file(sql_path, username, hash_password(password), port)
    create_service_script(service_script)
    if platform.system() == "Windows":
        configure_windows_startup(service_script, sql_path)
    else:
        configure_linux_startup(service_script, sql_path)
    print(f"Files stored in {runtime_dir}")
    running = run_in_background_now(service_script, sql_path, port, log_path)
    print("AdminRouter has been configured to run in background on startup.")
    if running:
        print(blue(f"AdminRouter is running at http://{get_local_ip()}:{port}"))
    else:
        print(f"AdminRouter did not respond on port {port}. Check {log_path}.")
    print(RESET, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
