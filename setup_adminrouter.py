#!/usr/bin/env python3
import getpass
import hashlib
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

PACKAGE_SET = ["flask", "bcrypt", "zeroconf"]
PBKDF2_ITERATIONS = 390000

# Whole screen is blue; only the text itself gets a white background with black text.
BODY = "\033[0;97;44m"    # bright white on blue (screen + anything the user types)
LABEL = "\033[0;30;47m"   # black on white (text blocks)
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


def label(text: str) -> str:
    """Text on a white background in black, then back to the blue screen."""
    return f"{LABEL} {text} {BODY}"


def say(text: str = "") -> None:
    print(label(text) if text else "")


def ask(prompt: str) -> str:
    return input(label(prompt) + " ").strip()


def ask_secret(prompt: str) -> str:
    print(label(prompt) + " ", end="", flush=True)
    return getpass.getpass("")


def paint_screen() -> None:
    # Setting the background and then clearing fills the whole terminal with blue.
    print(BODY + "\033[2J\033[H", end="", flush=True)


def is_supported_platform() -> bool:
    return platform.system() in {"Windows", "Linux"}


def prompt_yes_no(prompt: str) -> bool:
    while True:
        answer = ask(f"{prompt} [y/n]:").lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        say("Please answer with y or n.")


def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS
    ).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


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


def install_service_script(source: Path, destination: Path) -> None:
    shutil.copyfile(source, destination)
    destination.chmod(0o755)


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
    paint_screen()
    say("AdminRouter setup")
    say("Configure the admin login and web port")
    say()


def main() -> int:
    if not is_supported_platform():
        print("This installer only supports Windows or Linux terminals.")
        return 1
    enable_terminal_colors()
    print_setup_header()
    install_packages_if_requested()
    say()
    username = ask("Username:")
    while not username:
        username = ask("Username cannot be empty. Username:")
    password = ask_secret("Password:")
    while not password:
        password = ask_secret("Password cannot be empty. Password:")
    port_text = ask("Port to run on:")
    while True:
        try:
            port = parse_port(port_text)
            break
        except ValueError as exc:
            say(str(exc))
            port_text = ask("Port to run on:")
    say()
    root = Path(__file__).resolve().parent
    service_source = root / "adminrouter_service.py"
    if not service_source.exists():
        say("adminrouter_service.py must sit next to this installer.")
        return 1
    runtime_dir = get_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(root / "public", runtime_dir / "public", dirs_exist_ok=True)
    sql_path = runtime_dir / "adminrouter_config.sql"
    service_script = runtime_dir / "adminrouter_service.py"
    log_path = runtime_dir / "adminrouter.log"
    generate_sql_file(sql_path, username, hash_password(password), port)
    install_service_script(service_source, service_script)
    if platform.system() == "Windows":
        configure_windows_startup(service_script, sql_path)
    else:
        configure_linux_startup(service_script, sql_path)
    say(f"Files stored in {runtime_dir}")
    running = run_in_background_now(service_script, sql_path, port, log_path)
    say("AdminRouter has been configured to run in background on startup.")
    if running:
        say(f"AdminRouter is running at http://{get_local_ip()}:{port}")
    else:
        say(f"AdminRouter did not respond on port {port}. Check {log_path}.")
    say()
    ask("Press Enter to close:")
    print(RESET, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())