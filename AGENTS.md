# AGENTS.md

## Project Overview

PDF Watermarker is a cross-platform Windows/macOS desktop application. It uses
Tkinter for the UI, PyMuPDF for PDF rendering and editing, OpenCV/NumPy for
pixel-level watermark detection, Pillow for previews, tkinterdnd2 for drag and
drop, and pikepdf/libqpdf for authorized decryption with a supplied or empty
password.

## Current Version and Layout

- `Mark13.py` is the only current application entry point.
- `pdf_watermarker/version.py` is the version source of truth.
- `pdf_watermarker/app.py` owns UI and session orchestration.
- `pdf_watermarker/detection.py` owns watermark and ROI detection.
- `pdf_watermarker/processing.py` owns removal, unencrypted saves, and OCR.
- `pdf_watermarker/content_stream.py` owns low-level stream parsing.
- `pdf_watermarker/geometry.py`, `models.py`, and `constants.py` are shared code.
- `pdf_decryptor/` owns encryption inspection and supplied-password decryption.
- `assets/` contains source assets that are committed.
- `scripts/` contains reproducible platform build and asset scripts.
- `tests/` contains self-contained regression tests and must not depend on local
  ignored PDFs.

Do not restore historical `Mark1.py` through `Mark12.py` files. Git history is
the archive for old implementations.

## Mandatory Version Bump

Every development change that modifies application source must increment the
integer Mark version. The next source change after Mark13 must be Mark14, then
Mark15, and so on. In the same change:

1. Rename the root entry point to `Mark<version>.py` and remove the prior entry.
2. Update `APP_VERSION` in `pdf_watermarker/version.py`.
3. Update build artifact names and entry paths in both scripts.
4. Update `Readme.md` and this file wherever the current or next version appears.
5. Build and test only after the version update is complete.

Keep exactly one current versioned root entry point. Do not copy the complete
implementation into a new version file; the entry stays small and imports the
shared package.

## Development Environment

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python Mark13.py
```

macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python Mark13.py
```

Do not add a platform-only dependency unless necessary. If one is necessary,
document its environment marker and keep platform-specific outputs ignored.

## Validation

Run against the changed source immediately before committing:

```bash
.venv/bin/python -m compileall -q Mark13.py pdf_watermarker pdf_decryptor tests
.venv/bin/python -m unittest discover -v
```

For Windows, use the equivalent `.venv\Scripts\python.exe` command. Changes to
startup or packaging also require a GUI initialization smoke test and a packaged
executable launch test on the current platform. Changes to detection/removal must
verify that targeted watermark content is removed, nearby body content remains,
and the newly written PDF reopens without encryption.

Never treat a test result from an earlier commit, earlier Mark version, or a
pre-change build as validation of current source.

## Build

macOS:

```bash
./scripts/build_macos.sh
```

Windows PowerShell:

```powershell
.\scripts\build_windows.ps1
```

PyInstaller output is architecture- and OS-specific. Keep `.venv/`, `build/`,
`dist/`, `*.spec`, `.app`, `.dmg`, Python caches, generated test PDFs, temporary
previews, and IDE configuration out of Git.

## Code Rules

- Preserve the current Chinese UI wording unless a feature requires a change.
- Keep PDF password handling in `pdf_decryptor/`; never add password guessing or
  unknown-password recovery.
- Keep detection, processing, geometry, models, and UI concerns in their existing
  modules instead of growing the root entry point.
- Prefer official `import pymupdf as fitz` imports over the deprecated `fitz`
  compatibility package.
- Avoid unnecessary dependencies. OpenCV is currently required for thresholding,
  morphology, connected components, resize/blur, masks, and ROI comparisons.
- Preserve original PDFs and use verified unencrypted temporary/output files.
- Update tests whenever behavior changes.

## Git Workflow

Review the complete staged diff and confirm ignored build artifacts remain
untracked before committing. Use a concise Chinese commit summary and push the
current branch only after current-source validation passes.

After development, update affected documentation, review staged files, commit on the current branch with a concise Chinese summary, and push. Never claim an old test pass validates changed source.
