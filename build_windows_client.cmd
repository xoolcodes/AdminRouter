name: Build Windows client
on:
  workflow_dispatch: {}
  push:
    paths: ['alerting_client.py', '.github/workflows/build-windows-client.yml']
jobs:
  build:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install pyinstaller pillow
      - name: Build exe
        shell: pwsh
        run: |
          $opts = @('--onefile', '--noconsole', '--name', 'AlertingClient')
          if (Test-Path 'WINDOWSICON.jfif') {
            python -c "from PIL import Image; Image.open('WINDOWSICON.jfif').convert('RGBA').save('icon.ico', sizes=[(16,16),(32,32),(48,48),(256,256)])"
            $opts += @('--icon', 'icon.ico')
          }
          pyinstaller @args alerting_client.py
      - uses: actions/upload-artifact@v4
        with:
          name: AlertingClient
          path: dist/AlertingClient.exe