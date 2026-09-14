"""Cross-platform application launcher."""

import sys
from tkinter import messagebox

from .app import PDFWatermarkRemover, create_root


def main() -> int:
    root = None
    try:
        root = create_root()
        PDFWatermarkRemover(root)
        root.mainloop()
        return 0
    except ImportError as exc:
        detail = f"错误：缺少依赖模块：{exc}\n请运行：python -m pip install -r requirements.txt"
    except Exception as exc:
        detail = f"发生错误：{exc}"

    if root is not None:
        try:
            messagebox.showerror("PDF 水印去除工具", detail, parent=root)
        except Exception:
            pass
    print(detail, file=sys.stderr)
    return 1
