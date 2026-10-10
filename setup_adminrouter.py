#!/usr/bin/env python3
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
import sqlite3
import socket
import subprocess
import sys
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
    return f"{WHITE_TEXT}{text}{RESET}"


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
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PBKDF2_ITERATIONS,
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt, expected = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            int(iterations_text),
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
import hashlib
import hmac
import ipaddress
import json
import platform
import re
import secrets
import sqlite3
import sys
import socket
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def load_config(sql_path: Path):
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
            'sha256',
            password.encode('utf-8'),
            bytes.fromhex(salt),
            int(iterations_text),
        ).hex()
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(digest, expected)


def discover_devices():
    commands = [['arp', '-a']]
    if platform.system() != 'Windows':
        commands.insert(0, ['ip', 'neigh'])
    devices = {}
    ip_pattern = re.compile(r'\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b')
    mac_pattern = re.compile(r'(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}')
    for command in commands:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in result.stdout.splitlines():
            ip_match = ip_pattern.search(line)
            if not ip_match:
                continue
            address = ip_match.group(0)
            try:
                ipaddress.ip_address(address)
            except ValueError:
                continue
            mac_match = mac_pattern.search(line)
            devices[address] = {
                'ip': address,
                'mac': mac_match.group(0) if mac_match else 'unknown',
                'name': 'Network device',
            }
    local_ip = '127.0.0.1'
    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        pass
    devices.setdefault(local_ip, {'ip': local_ip, 'mac': 'local', 'name': 'This computer'})
    return sorted(devices.values(), key=lambda device: device['ip'])


def ping_device(address):
    try:
        target = str(ipaddress.ip_address(address))
    except ValueError:
        return {'ok': False, 'error': 'Invalid IP address'}
    if platform.system() == 'Windows':
        command = ['ping', '-n', '1', '-w', '1000', target]
    else:
        command = ['ping', '-c', '1', '-W', '1', target]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=4, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return {'ok': False, 'error': 'Ping could not be run'}
    return {'ok': result.returncode == 0, 'ip': target}


class Handler(BaseHTTPRequestHandler):
    username = ''
    password_hash = ''
    public_index = Path(__file__).resolve().parent / 'public' / 'index.html'
    sessions = set()

    def do_GET(self):
        if self.path in {'/', '/index.html'}:
            self.serve_file('index.html')
            return
        if self.path == '/dashboard.html':
            if not self.is_authenticated():
                self.send_response(302)
                self.send_header('Location', '/')
                self.end_headers()
                return
            self.serve_file('dashboard.html')
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
        if self.path == '/api/ping':
            if not self.is_authenticated():
                self.send_json({'error': 'Authentication required'}, 401)
                return
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
        self.send_json(
            {'ok': True, 'user': self.username},
            headers={'Set-Cookie': f'admin_session={token}; HttpOnly; SameSite=Strict; Path=/'},
        )

    def is_authenticated(self):
        cookie = self.headers.get('Cookie', '')
        cookies = {
            item.split('=', 1)[0].strip(): item.split('=', 1)[1]
            for item in cookie.split(';')
            if '=' in item
        }
        return cookies.get('admin_session') in self.sessions

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
    server = ThreadingHTTPServer(('0.0.0.0', int(port)), Handler)
    server.serve_forever()
'''
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def configure_windows_startup(service_script: Path, sql_path: Path) -> None:
    startup_dir = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    startup_dir.mkdir(parents=True, exist_ok=True)
    startup_file = startup_dir / "AdminRouter.bat"
    command = f'start "" /B "{sys.executable}" "{service_script}" "{sql_path}"\n'
    startup_file.write_text(command, encoding="utf-8")


def configure_linux_startup(service_script: Path, sql_path: Path) -> None:
    systemd = shutil.which("systemctl")
    if systemd:
        unit_dir = Path.home() / ".config/systemd/user"
        unit_dir.mkdir(parents=True, exist_ok=True)
        unit_file = unit_dir / "adminrouter.service"
        unit_file.write_text(
            "[Unit]\n"
            "Description=AdminRouter background service\n\n"
            "[Service]\n"
            "Type=simple\n"
            f"ExecStart={sys.executable} {service_script} {sql_path}\n"
            "Restart=always\n\n"
            "[Install]\n"
            "WantedBy=default.target\n",
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
                [sys.executable, str(service_script), str(sql_path)],
                stdout=log_file,
                stderr=log_file,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        else:
            subprocess.Popen(
                [sys.executable, str(service_script), str(sql_path)],
                stdout=log_file,
                stderr=log_file,
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
    if not prompt_yes_no(
        "Install optional packages (flask, bcrypt, zeroconf)?"
    ):
        return
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
    print(' AdminRouter setup '.center(width))
    print('Configure the admin login and web port'.center(width))
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
