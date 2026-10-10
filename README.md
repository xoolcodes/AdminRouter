# AdminRouter

A small Python admin login service for Windows and Linux. It uses Python's
standard library and SQLite, so the basic setup does not need Flask or any
other package.

The username and password entered during setup are the **admin login** for the
web page.

## Requirements

- Python 3.8 or newer
- Windows 10/11 or Linux
- An unused port, such as `8080`

For an older or low-spec computer, answer `n` when setup asks about optional
packages. The basic service does not need them.

## Windows: Command Prompt

These commands use Command Prompt (`cmd.exe`), not PowerShell.

### Install Python and Git without winget

```cmd
mkdir "%USERPROFILE%\Downloads" 2>nul
cd /d "%USERPROFILE%\Downloads"
curl.exe -L -o python-installer.exe https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
start /wait "" python-installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
curl.exe -L -o git-installer.exe https://github.com/git-for-windows/git/releases/download/v2.49.0.windows.1/Git-2.49.0-64-bit.exe
start /wait "" git-installer.exe /VERYSILENT /NORESTART
```

Close Command Prompt and open a new one so Windows reloads `PATH`. Then run:

```cmd
git --version
py --version
cd /d "%USERPROFILE%\Downloads"
git clone https://github.com/xoolcodes/AdminRouter.git
cd AdminRouter
py setup_adminrouter.py
```

If `py` is not available but `python` is, run:

```cmd
python setup_adminrouter.py
```

When setup finishes, it prints a line like this:

```text
AdminRouter accessible at http://192.168.1.25:8080
```

Open that address in a browser. Use the username and password you entered
while configuring the installer. On the same computer, `http://127.0.0.1:8080`
will also work when the selected port is `8080`.

The service starts immediately and is added to the Windows Startup folder.

### Download without Git

If Git is not available, download the project ZIP from Command Prompt:

```cmd
cd /d "%USERPROFILE%\Downloads"
curl.exe -L -o AdminRouter.zip https://github.com/xoolcodes/AdminRouter/archive/refs/heads/main.zip
tar -xf AdminRouter.zip
cd AdminRouter-main
py setup_adminrouter.py
```

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

Setup prints the address to open, for example:

```text
AdminRouter accessible at http://192.168.1.25:8080
```

If user systemd is available, AdminRouter is enabled as a user service. If it
is not available, setup uses a user crontab entry instead. It also starts the
service immediately.

## Updating

Stop the current service before updating the files.

On Windows Command Prompt, run from the AdminRouter folder:

```cmd
wmic process where "CommandLine like '%%adminrouter_service.py%%'" call terminate
git pull --ff-only
py setup_adminrouter.py
```

On Linux, run from the AdminRouter folder:

```bash
systemctl --user disable --now adminrouter.service 2>/dev/null || true
git pull --ff-only
python3 setup_adminrouter.py
```

Use the same admin username, password, and port if you want to keep the same
login and address. Setup writes a new configuration and starts the updated
service.

## Uninstalling

Stop the service, then remove the generated files and startup entry.

Windows Command Prompt:

```cmd
wmic process where "CommandLine like '%%adminrouter_service.py%%'" call terminate
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AdminRouter.bat"
del adminrouter_config.sql adminrouter_service.py
```

Linux:

```bash
systemctl --user disable --now adminrouter.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/adminrouter.service"
crontab -l 2>/dev/null | grep -v 'adminrouter_service.py' | crontab -
rm -f adminrouter_config.sql adminrouter_service.py
```

## Limits

- This is a small starter admin service, not a production web server.
- The service listens on all network interfaces so another device on the LAN
  can open the printed address. Use a firewall if it should stay private.
- The login page is served from `public/index.html`.
- The installer supports Windows and Linux only.
