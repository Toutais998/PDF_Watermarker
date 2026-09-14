#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"

.venv/bin/python -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --onedir \
  --name Mark13Mac \
  --icon assets/pdf_tool_icon.ico \
  --osx-bundle-identifier com.toutais.pdfwatermarker \
  Mark13.py
