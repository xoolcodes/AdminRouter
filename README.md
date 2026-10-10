# AdminRouter

AdminRouter is a small Python HTTP service installer for Windows and Linux.
The core service uses only Python's standard library and SQLite. Optional
packages are not required for the generated service.

## Requirements

- Windows 10/11 or Linux with Python 3.8 or newer.
- A user account that can create files in the project directory.
- One unused TCP port, for example `8080`.

For a low-spec computer, answer `n` when the installer asks to install
optional packages. The core service does not require Flask, bcrypt, or
zeroconf. Keep the port local or protected by your firewall when possible.

## Windows Command Prompt

These commands are for **Command Prompt (`cmd.exe`)**, not PowerShell. Run
each command on its own line.

### Install Python and Git without winget

```cmd
cd /d "%USERPROFILE%\Downloads"
curl.exe -L -o python-installer.exe https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
start /wait "" python-installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
curl.exe -L -o git-installer.exe https://github.com/git-for-windows/git/releases/download/v2.49.0.windows.1/Git-2.49.0-64-bit.exe
start /wait "" git-installer.exe /VERYSILENT /NORESTART
```

Close Command Prompt and open a new one so Windows reloads `PATH`. Confirm
the installations:

```cmd
git --version
py --version
```

If `Downloads` does not exist, create it first:

```cmd
mkdir "%USERPROFILE%\Downloads"
```

### Download and run

```cmd
cd /d "%USERPROFILE%\Downloads"
git clone https://github.com/xoolcodes/AdminRouter.git
cd AdminRouter
py setup_adminrouter.py
```

If `py` is unavailable but `python` works, use:

```cmd
python setup_adminrouter.py
```

During setup, answer `n` for optional packages on low-spec systems, then
provide a username, password, and port. The installer creates
`adminrouter_config.sql` and `adminrouter_service.py`, starts the service,
and adds `AdminRouter.bat` to the Windows Startup folder.

### Windows without Git

Git is optional for downloading a release snapshot. From Command Prompt:

```cmd
cd /d "%USERPROFILE%\Downloads"
curl.exe -L -o AdminRouter.zip https://github.com/xoolcodes/AdminRouter/archive/refs/heads/main.zip
tar -xf AdminRouter.zip
cd AdminRouter-main
py setup_adminrouter.py
```

If `curl.exe` or `tar` is unavailable, install Python and Git from their
official installers, then use the Git commands above.

## Linux

### Debian or Ubuntu

```bash
sudo apt update
sudo apt install -y python3 python3-pip git
cd "$HOME"
git clone https://github.com/xoolcodes/AdminRouter.git
cd AdminRouter
python3 setup_adminrouter.py
```

### Fedora

```bash
sudo dnf install -y python3 python3-pip git
cd "$HOME"
git clone https://github.com/xoolcodes/AdminRouter.git
cd AdminRouter
python3 setup_adminrouter.py
```

For a low-spec Linux computer, answer `n` for optional packages. If
`systemctl --user` is available, the installer enables a user systemd service.
Otherwise it adds a user crontab entry. The service is also started
immediately after setup.

## What the installer does

1. Optionally offers to install `flask`, `bcrypt`, and `zeroconf`.
2. Prompts for a username, password, and port.
3. Stores a salted PBKDF2-HMAC-SHA256 password hash in the generated SQL file.
4. Generates `adminrouter_service.py` and `adminrouter_config.sql`.
5. Starts the service and configures startup execution for Windows or Linux.

## Limitations

- This project supports Windows and Linux only.
- It is a small starter HTTP service, not a complete production web server.
- The service listens on `0.0.0.0`; use a firewall or a local-only setup if it
	should not be reachable from other machines.
- The installer does not provide an automatic uninstall command.

## Uninstall

Stop the running Python service first, then remove the generated files and
startup entry.

On Windows Command Prompt:

```cmd
wmic process where "CommandLine like '%%adminrouter_service.py%%'" call terminate
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AdminRouter.bat"
del adminrouter_config.sql adminrouter_service.py
```

On Linux:

```bash
systemctl --user disable --now adminrouter.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/adminrouter.service"
crontab -l 2>/dev/null | grep -v 'adminrouter_service.py' | crontab -
rm -f adminrouter_config.sql adminrouter_service.py
```
