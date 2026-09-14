# AGENTS.md

## Project Overview

PDF Watermarker is a Windows and macOS desktop application for detecting and removing
watermarks from PDF files. The main application is implemented with Python,
Tkinter, PyMuPDF, OpenCV, NumPy, Pillow, tkinterdnd2, and pikepdf/libqpdf.

## Source Files

- `Mark12.py` is the stable, cross-platform Mark12 entry point. Keep it small.
- `pdf_watermarker/app.py` owns the Tkinter UI and file/preview session lifecycle.
- `pdf_watermarker/detection.py` owns automatic and ROI watermark detection.
- `pdf_watermarker/processing.py` owns removal, unencrypted saves, and OCR output.
- `pdf_watermarker/content_stream.py` owns low-level PDF stream parsing.
- `pdf_watermarker/geometry.py`, `models.py`, and `constants.py` contain shared helpers.
- `tests/` contains self-contained regression tests generated in temporary directories.
- `Mark10.py` is the previous improved implementation.
- `Mark9.py` is the previous improved implementation.
- `Mark8.py` is the earlier improved implementation.
- `pdf_decryptor/` is the independent, password-supplied PDF decryption module
  used before Mark12 opens a document with PyMuPDF.
- `Mark6.py` is the previous improved implementation.
- `Mark5.py` is the previous stable implementation.
- `Mark1.py` through `Mark4.py` are historical versions and should be
  preserved unless the user explicitly asks for cleanup.
- `pdf_tool_icon.ico` is the application icon.
- `requirements.txt` contains the Python dependencies.

## Development Environment

Use the project virtual environment whenever possible.

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies with:

```powershell
python -m pip install -r requirements.txt
```

macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Run the current application with:

```powershell
python Mark12.py
```

## Validation

Before handing off Python changes, run the platform-appropriate equivalent of:

```bash
.venv/bin/python -m py_compile Mark12.py pdf_watermarker/*.py pdf_decryptor/*.py tests/*.py
.venv/bin/python -m unittest discover -v
```

When changing watermark detection or removal, validate against a representative
PDF and confirm that the watermark is removed without damaging nearby content.
When changing startup or packaging, instantiate the Tk root/application and smoke-test
the packaged executable. Compare the method set after any mixin refactor so public and
internal behavior is not accidentally dropped.

## Build

Build the current Mark12 Windows executable with:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --noconsole --onefile --clean --icon="pdf_tool_icon.ico" --name Mark12Final Mark12.py
```

For the optimized Windows build, use the local `Mark12Final.spec` (it filters out
the unused `opencv_videoio_ffmpeg` video DLL) together with UPX on PATH:

```powershell
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --upx-dir "C:\path\to\upx\win64" Mark12Final.spec
```

Build the current Apple Silicon macOS app with:

```bash
.venv/bin/python -m PyInstaller --noconfirm --clean --windowed --onedir \
  --name Mark12Mac --icon pdf_tool_icon.ico \
  --osx-bundle-identifier com.toutais.pdfwatermarker Mark12.py
```

This produces `dist/Mark12Mac.app`. PyInstaller builds are architecture-specific;
use an Intel Python environment for an Intel build. Local/ad-hoc signing is enough
for smoke testing, but public distribution requires Developer ID signing and notarization.

The generated `build/`, `dist/`, and `*.spec` files are local build artifacts.
They must remain ignored by Git and must not be committed.

## Git and Repository Rules

- Commit core source code, documentation, icons, and `requirements.txt`.
- Every conversation that ends with a new version of the Python code must be followed by compiling it and pushing the core code to git.
- Do not commit `.venv/`, `build/`, `dist/`, `*.spec`, Python caches, test
  PDFs, `.app`, `.dmg`, temporary preview files, or IDE configuration.
- Keep changes focused and preserve existing historical versions.
- Do not rewrite history or use destructive Git commands unless explicitly
  requested.
- Use clear commit messages and push only after the working tree has been
  reviewed.
- Every functional update or new Mark version must update both `Readme.md` and
  `AGENTS.md` in the same change.

## Code Style

- Follow the existing Python structure and naming conventions.
- Prefer small, focused changes over broad refactors.
- Keep user-facing messages clear and consistent with the existing Chinese UI.
- Avoid adding dependencies unless they are necessary and documented in
  `requirements.txt`.
- Do not add comments that merely restate obvious code.

## Version Notes

- Mark6 added support for Hujiang-style pale diagonal text/path watermarks.
- Mark7 adds support for Test-2-style repeated bottom QR-code watermarks,
  bottom QR instruction text, and repeated Koolearn/New Oriental right-side
  background image watermarks.
- Mark7 supports selecting or dragging multiple PDFs and recursively importing
  a folder. Files are analyzed sequentially and their detection results can be
  reviewed from the batch-file selector.
- Mark8 fixes Test-3-style slanted pale vector watermarks (e.g. 沪江德语):
  removal now deletes the pale/translucent vector paths directly from the page
  content stream instead of white-filling the whole bounding rectangle, so body
  text is no longer wiped out. A precise pale-pixel cell redaction acts as a
  safe fallback when the path parser matches nothing.
- Mark8 keyword-based text-watermark detection only triggers near page edges or
  on rotated text, so body lines that merely contain a brand word (e.g.
  "Hujiang") are no longer misclassified as watermarks.
- Mark8 rendered-diagonal detection now auto-fits the dominant diagonal
  orientation and clusters parallel bands instead of relying on hardcoded
  slopes/intercepts, and it is skipped on pages already matched by the
  soft-vector detector.
- Mark9 checks PDF encryption before PyMuPDF analysis and uses the independent
  `pdf_decryptor` package (pikepdf/libqpdf) to create and verify an unencrypted
  session temp file. Empty user passwords work automatically; known non-empty
  user or owner passwords are requested through a masked dialog. The original
  PDF is never overwritten, cached decrypted files are reused during the
  session, and all session decryption files are removed on exit.
- Mark10 detects explicit PDF `/Subtype /Watermark` artifacts and producer-tagged
  `/Private /Watermark` image/Form XObjects before pixel analysis. This supports
  Test4's optional-content, even-page image watermark and removes Test5's tiled
  Form watermark directly from content streams.
- Mark10 avoids expanding duplicate/dead image resources, filters 1-pixel drawing
  helpers, indexes removals by page, and uses a faster safe garbage-collection
  level when saving.
- Every Mark10 preview and final result is explicitly saved without encryption and
  verified to contain no password or PDF permission protection before success is
  reported.
- Mark11 uses deep resource cleanup (`garbage=4`, `clean=1`, object streams, and
  deflate) when saving so Test4-style PDFs can shrink without rasterizing text.
  It diagnoses missing `ToUnicode` maps in FzBookMaker/custom-encoded fonts and
  explains that displayed-but-uncopyable text requires external OCR (Tesseract
  with Chinese language data); the original glyph encoding cannot be losslessly
  reconstructed from the PDF alone.
- Mark11's bundled EXE was slimmed from ~98 MiB to ~63 MiB by switching to
  `opencv-python-headless` and dropping the unused `opencv_videoio_ffmpeg` video DLL.
- Mark12 now treats large background images as direct XObject removals and skips rendered-diagonal pixel scanning on those pages, which keeps Test6-style previews much faster without changing the other watermark paths.
- Mark12 is now split into the `pdf_watermarker` package while preserving the
  original 92 application methods through mixins. The same entry point and dependency
  set are used on Windows and macOS. Apple Silicon source, PDF workflow, GUI, packaged
  launch, bundled drag-and-drop, and bundled libqpdf checks have passed.
