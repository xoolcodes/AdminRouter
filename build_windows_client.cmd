@echo off
setlocal
cd /d "%~dp0"
py -m pip install --upgrade pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed --name AdminRouterAlertingClient remote_alerting_client.py
if errorlevel 1 (
  echo Build failed.
  exit /b 1
)
echo Built dist\AdminRouterAlertingClient.exe
