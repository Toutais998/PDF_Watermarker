#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")" && pwd)"
python_bin="$project_dir/.venv/bin/python"

if [ ! -x "$python_bin" ]; then
  osascript -e 'display alert "PDF Watermarker" message "未找到 .venv。请先在项目目录运行：python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt" as critical'
  exit 1
fi

cd "$project_dir"
exec "$python_bin" Mark18.py
