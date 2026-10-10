# AdminRouter

A small Python admin login service for Windows and Linux. It uses Python's
standard library and SQLite, so the basic setup does not need Flask or any
other package.

The username and password entered during setup are the **admin login** for the
web page.

After login, AdminRouter opens a dashboard at `/dashboard.html`. The Devices
tab scans the local IPv4 subnet and provides a hidden ping check for each
responding address. It shows best-effort OS and device-type guesses. The
Automation tab is present but marked **Coming soon**.

Settings includes a Remote Alerting switch. When enabled, the Remote Alerting
tab appears. The server endpoint is ready for a companion client, but native
Windows `.exe` and Android apps are separate clients. A Windows client source
and build script are included below; an Android client is not included yet.

## Requirements

- Python 3.8 or newer
- Windows 10/11 or Linux
- An unused port, such as `8080`

For an older or low-spec computer, answer `n` when setup asks about optional
packages. The basic service does not need them.

## Windows: Command Prompt

These commands use Command Prompt (`cmd.exe`), not PowerShell.

### Install and run in one Command Prompt window

```cmd
mkdir "%USERPROFILE%\Downloads" 2>nul
cd /d "%USERPROFILE%\Downloads"
curl.exe -L -o python-installer.exe https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe
start /wait "" python-installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
set "PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
curl.exe -L -o AdminRouter.zip https://github.com/xoolcodes/AdminRouter/archive/refs/heads/main.zip
tar -xf AdminRouter.zip
"%PYTHON%" AdminRouter-main\setup_adminrouter.py
```

This uses one Command Prompt window and does not depend on `PATH` being
refreshed after Python is installed. If Python is already installed, run:

```cmd
cd /d "%USERPROFILE%\Downloads\AdminRouter-main"
py setup_adminrouter.py
```

When setup finishes, it prints a line like this:

```text
AdminRouter is running at http://192.168.1.25:8080
```

Open that address in a browser. Use the username and password you entered
while configuring the installer. On the same computer, `http://127.0.0.1:8080`
will also work when the selected port is `8080`.

The setup prompts use a blue console style. The service starts immediately and
is added to the Windows Startup folder. Generated files are stored in
`%LOCALAPPDATA%\AdminRouter`, not in Downloads or on the Desktop. If it cannot
start, check `adminrouter.log` in that folder.

Git is not required for the Windows setup above. If `curl.exe` or `tar` is
unavailable, install Git and Python from their official installers, then clone
the repository and run `py setup_adminrouter.py`.

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
AdminRouter is running at http://192.168.1.25:8080
```

If user systemd is available, AdminRouter is enabled as a user service. If it
is not available, setup uses a user crontab entry instead. It also starts the
service immediately. Generated files are stored in
`~/.local/share/AdminRouter`; startup and port errors are written to
`adminrouter.log` there.

## Updating

Stop the current service before updating the files.

On Windows Command Prompt, run from the AdminRouter folder:

```cmd
wmic process where "CommandLine like '%%adminrouter_service.py%%'" call terminate
git pull --ff-only
"%LocalAppData%\Programs\Python\Python312\python.exe" setup_adminrouter.py
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

## Windows alerting client

The repository includes `remote_alerting_client.py`, a small desktop client
that polls the server and displays Windows notifications. Build it on a
Windows machine from Command Prompt:

```cmd
cd /d path\to\AdminRouter
build_windows_client.cmd
```

The executable is created at `dist\AdminRouterAlertingClient.exe`. Start it
and enter only the admin username. The client scans local IP addresses on the
fixed discovery port `39872`, calls `/info/creditngtgtu`, and then switches to
the selected dashboard port. It does not ask for or store the admin password.
Enable Remote Alerting in the dashboard Settings tab; dashboard test alerts
will appear in the client.

GitHub Actions also builds the Windows executable automatically. Download the
`AdminRouterAlertingClient-windows` artifact from the workflow run on GitHub.

The Android client still needs a separate Android project and notification
service; the server API is ready for one to poll `/api/alerts?after=ID` with
the `X-AdminRouter-Username` header. The supplied `WINDOWSICON.jfif` is
converted into the Windows executable icon during the build.

## Uninstalling

Stop the service, then remove the generated files and startup entry.

Windows Command Prompt:

```cmd
wmic process where "CommandLine like '%%adminrouter_service.py%%'" call terminate
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\AdminRouter.bat"
rmdir /s /q "%LOCALAPPDATA%\AdminRouter"
```

Linux:

```bash
systemctl --user disable --now adminrouter.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/adminrouter.service"
crontab -l 2>/dev/null | grep -v 'adminrouter_service.py' | crontab -
rm -rf "$HOME/.local/share/AdminRouter"
```

## Limits

- This is a small starter admin service, not a production web server.
- The service listens on all network interfaces so another device on the LAN
  can open the printed address. Use a firewall if it should stay private.
- The login page is served from `public/index.html`.
- The dashboard is served from `public/dashboard.html` after admin login.
- Device discovery scans the local IPv4 route. Larger or segmented networks
  may still need a proper network scanner and will not all be covered.
- OS and device type are estimates based on ping TTL and reverse-DNS names;
  they cannot reliably distinguish every iPhone, Android, laptop, or server.
- Ping runs invisibly in the service process. On minimal Linux installations,
  install the package that provides `ping` if the button reports no response.
- The installer supports Windows and Linux only.
