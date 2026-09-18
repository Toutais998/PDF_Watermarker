$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir

& .\.venv\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --clean `
    --noconsole `
    --onefile `
    --name Mark18Final `
    --icon assets\pdf_tool_icon.ico `
    Mark18.py
