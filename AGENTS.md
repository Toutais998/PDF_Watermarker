# AGENTS.md

## Project Overview

PDF Watermarker is a Windows desktop application for detecting and removing
watermarks from PDF files. The main application is implemented with Python,
Tkinter, PyMuPDF, OpenCV, NumPy, Pillow, and tkinterdnd2.

## Source Files

- `Mark5.py` is the previous stable implementation.
- `Mark6.py` is the current improved implementation.
- `Mark1.py` through `Mark4.py` are historical versions and should be
  preserved unless the user explicitly asks for cleanup.
- `pdf_tool_icon.ico` is the application icon.
- `requirements.txt` contains the Python dependencies.

## Development Environment

Use the project virtual environment whenever possible:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies with:

```powershell
python -m pip install -r requirements.txt
```

Run the current application with:

```powershell
python Mark6.py
```

## Validation

Before handing off Python changes, run:

```powershell
.\.venv\Scripts\python.exe -m py_compile Mark6.py
```

When changing watermark detection or removal, validate against a representative
PDF and confirm that the watermark is removed without damaging nearby content.

## Build

Build the Windows executable with the virtual environment:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --noconsole --onefile --clean --icon="pdf_tool_icon.ico" --name Mark6Final Mark6.py
```

The generated `build/`, `dist/`, and `*.spec` files are local build artifacts.
They must remain ignored by Git and must not be committed.

## Git and Repository Rules

- Commit core source code, documentation, icons, and `requirements.txt`.
- Do not commit `.venv/`, `build/`, `dist/`, `*.spec`, Python caches, test
  PDFs, temporary preview files, or IDE configuration.
- Keep changes focused and preserve existing historical versions.
- Do not rewrite history or use destructive Git commands unless explicitly
  requested.
- Use clear commit messages and push only after the working tree has been
  reviewed.

## Code Style

- Follow the existing Python structure and naming conventions.
- Prefer small, focused changes over broad refactors.
- Keep user-facing messages clear and consistent with the existing Chinese UI.
- Avoid adding dependencies unless they are necessary and documented in
  `requirements.txt`.
- Do not add comments that merely restate obvious code.
