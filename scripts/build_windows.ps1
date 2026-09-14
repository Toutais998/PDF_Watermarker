$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir

& .\.venv\Scripts\python.exe -m PyInstaller `
    --noconfirm `
    --clean `
    --noconsole `
    --onefile `
    --name Mark14Final `
    --icon assets\pdf_tool_icon.ico `
    Mark14.py
