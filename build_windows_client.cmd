@echo off
setlocal
cd /d "%~dp0"
py -m pip install --upgrade pyinstaller pillow
py -c "from PIL import Image; Image.open('WINDOWSICON.jfif').convert('RGBA').save('windowsicon.ico', format='ICO', sizes=[(16,16),(32,32),(48,48),(256,256)])"
pyinstaller --noconfirm --clean --onefile --windowed --icon windowsicon.ico --name AdminRouterAlertingClient remote_alerting_client.py
if errorlevel 1 (
  echo Build failed.
  exit /b 1
)
echo Built dist\AdminRouterAlertingClient.exe
