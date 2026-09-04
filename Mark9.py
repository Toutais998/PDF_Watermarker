import os
import sys
import threading
import tempfile
import hashlib
import math
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Iterable

import fitz  # PyMuPDF
import cv2
import numpy as np
from PIL import Image, ImageTk, ImageDraw

from tkinter import (
    Tk,
    filedialog,
    messagebox,
    Frame,
    Button,
    Label,
    Listbox,
    Entry,
    Canvas,
    simpledialog,
)
from tkinter import ttk, StringVar, TOP, BOTH, X, Y, LEFT, RIGHT, END

from pdf_decryptor import (
    IncorrectPasswordError,
    PasswordRequiredError,
    PdfDecryptionError,
    decrypt_pdf,
    is_pdf_encrypted,
)

# 可选的拖拽支持
try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_TKDND = True
except Exception:
    TkinterDnD = None
    DND_FILES = None
    HAS_TKDND = False


MAIN_BG = "#f0f0f0"
BUTTON_BG = "#007bff"
BUTTON_FG = "white"
FRAME_BG = "white"
ROI_OUTLINE = "#ff3b30"
ROI_FILL = "#ff3b3020"

WATERMARK_KEYWORDS = [
    "中国知网", "cnki", "www.cnki.net",
    "沪江德语", "hujiang", "hujiang.com", "www.hujiang.com",
    "新东方在线", "koolearn", "koolearn.com", "www.koolearn.com",
    "网络课堂", "电子教材", "xdfdeyu", "扫一扫", "微信公众",
    "学位论文", "版权所有", "学术期刊",
    "请勿复制", "请勿传播", "版权保护",
    "confidential", "watermark", "draft",
    "机密", "草稿", "内部文件", "内部资料",
    "copyright", "all rights reserved",
    "初稿", "评审稿", "proof", "first proof only",
]


# ---------------- 内容流解析（用于精确定位并删除浅色矢量水印路径） ----------------

def _tokenize_content_stream(text: str):
    """返回 (kind, value, start, end) 元组；num 的 value 为浮点值。"""
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \r\n\t":
            i += 1
            continue
        if ch == "<":
            if text.startswith("<<", i):
                j = text.find(">>", i)
                if j < 0:
                    j = i
                tokens.append(("other", text[i:j + 2], i, j + 2))
                i = j + 2
                continue
            j = text.find(">", i)
            if j < 0:
                j = i
            tokens.append(("other", text[i:j + 1], i, j + 1))
            i = j + 1
            continue
        if ch == "[":
            depth = 0
            j = i
            while j < n:
                if text[j] == "[":
                    depth += 1
                elif text[j] == "]":
                    depth -= 1
                    if depth <= 0:
                        j += 1
                        break
                j += 1
            tokens.append(("other", text[i:j], i, j))
            i = j
            continue
        if ch == "(":
            # 处理转义，找到真正的字符串结束括号
            j = i + 1
            depth = 1
            while j < n and depth > 0:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                j += 1
            tokens.append(("other", text[i:j], i, j))
            i = j
            continue
        if ch == "/":
            j = i + 1
            while j < n and text[j] not in " \r\n\t<>[]()":
                j += 1
            tokens.append(("name", text[i:j], i, j))
            i = j
            continue
        num_match = re.match(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text[i:])
        if num_match:
            raw = num_match.group()
            tokens.append(("num", float(raw), i, i + num_match.end()))
            i += num_match.end()
            continue
        op_match = re.match(r"[A-Za-z*]+", text[i:])
        if op_match:
            tokens.append(("op", op_match.group(), i, i + op_match.end()))
            i += op_match.end()
            continue
        tokens.append(("other", text[i], i, i + 1))
        i += 1
    return tokens


def _matrix_mul(A, B):
    a1, b1, c1, d1, e1, f1 = A
    a2, b2, c2, d2, e2, f2 = B
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _matrix_apply(pt, m):
    x, y = pt
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def _path_bbox(path_ops, ctm):
    xs = []
    ys = []
    for op, coords in path_ops:
        for idx in range(0, len(coords), 2):
            x, y = _matrix_apply((coords[idx], coords[idx + 1]), ctm)
            xs.append(x)
            ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _resolve_gs_alpha(doc, page, gs_name: str) -> float:
    try:
        res = doc.xref_get_key(page.xref, "Resources")
        res_text = res[1] if res[0] == "dict" else ""
        ext_match = re.search(r"/ExtGState\s*<<(.*?)>>", res_text, re.DOTALL)
        if not ext_match:
            return 1.0
        ref_match = re.search(r"%s\s+(\d+)\s+0\s+R" % re.escape(gs_name), ext_match.group(1))
        if not ref_match:
            return 1.0
        gs_obj = doc.xref_object(int(ref_match.group(1)))
        for key in ("/ca", "/CA"):
            m = re.search(key + r"\s+([\d.]+)", gs_obj)
            if m:
                return float(m.group(1))
    except Exception:
        pass
    return 1.0


def _filter_watermark_paths(doc, page, content_xref: int, target_rects) -> int:
    """从页面内容流中删除落在目标矩形内、且为浅色或半透明的填充路径。"""
    try:
        raw = doc.xref_stream(content_xref)
    except Exception:
        return 0
    text = raw.decode("latin1", "ignore")
    tokens = _tokenize_content_stream(text)
    page_height = page.rect.height
    state = {"ctm": (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), "fill": None, "alpha": 1.0}
    state_stack = []
    path_ops = []
    pending = []
    out = []
    drop_ranges = []
    removed = 0
    last_name = None

    def pop_nums(count):
        if len(pending) < count:
            return None
        nums = pending[-count:]
        del pending[-count:]
        return nums

    for tok in tokens:
        kind = tok[0]
        if kind == "num":
            pending.append(tok[1])
            out.append(tok)
            continue
        if kind == "name":
            last_name = tok[1]
            out.append(tok)
            continue
        if kind != "op":
            out.append(tok)
            continue
        op = tok[1]

        if op == "q":
            state_stack.append(dict(state))
            out.append(tok)
        elif op == "Q":
            if state_stack:
                state = state_stack.pop()
            out.append(tok)
        elif op == "cm":
            nums = pop_nums(6)
            if nums:
                state["ctm"] = _matrix_mul(state["ctm"], (nums[0], nums[1], nums[2], nums[3], nums[4], nums[5]))
            out.append(tok)
        elif op == "g":
            nums = pop_nums(1)
            if nums:
                state["fill"] = ("gray", nums[0])
            out.append(tok)
        elif op == "rg":
            nums = pop_nums(3)
            if nums:
                state["fill"] = ("rgb", (nums[0], nums[1], nums[2]))
            out.append(tok)
        elif op == "k":
            nums = pop_nums(4)
            if nums:
                state["fill"] = ("cmyk", (nums[0], nums[1], nums[2], nums[3]))
            out.append(tok)
        elif op in ("scn", "SCN"):
            # 数值着色（RGB/CMYK）记录颜色；带 Pattern 名称时按未知处理
            if len(pending) >= 3:
                nums = pending[-3:]
                del pending[-3:]
                state["fill"] = ("rgb", (nums[0], nums[1], nums[2]))
            elif len(pending) >= 4:
                nums = pending[-4:]
                del pending[-4:]
                state["fill"] = ("cmyk", (nums[0], nums[1], nums[2], nums[3]))
            out.append(tok)
        elif op == "gs":
            if last_name:
                state["alpha"] = _resolve_gs_alpha(doc, page, last_name)
            last_name = None
            out.append(tok)
        elif op in ("m", "l"):
            nums = pop_nums(2)
            if nums:
                path_ops.append((op, nums))
            out.append(tok)
        elif op == "re":
            nums = pop_nums(4)
            if nums:
                path_ops.append((op, nums))
            out.append(tok)
        elif op in ("c", "v", "y"):
            nums = pop_nums(6)
            if nums:
                path_ops.append((op, nums))
            out.append(tok)
        elif op == "h":
            path_ops.append((op, []))
            out.append(tok)
        elif op in ("f", "f*", "B", "B*", "b", "b*", "s", "S", "n"):
            bb = _path_bbox(path_ops, state["ctm"])
            lum = None
            if state["fill"]:
                ft, fv = state["fill"]
                if ft == "gray":
                    lum = fv
                elif ft == "rgb":
                    lum = 0.2126 * fv[0] + 0.7152 * fv[1] + 0.0722 * fv[2]
                elif ft == "cmyk":
                    lum = 1.0 - min(1.0, fv[0] + fv[1] + fv[2] + fv[3])
            pale = lum is not None and lum >= 0.5
            translucent = state["alpha"] < 0.9
            drop = False
            if bb and op in ("f", "f*", "B", "B*", "b", "b*") and (pale or translucent):
                bb_top = (bb[0], page_height - bb[3], bb[2], page_height - bb[1])
                for tr in target_rects:
                    if not (bb_top[2] < tr.x0 or bb_top[0] > tr.x1 or bb_top[3] < tr.y0 or bb_top[1] > tr.y1):
                        drop = True
                        break
            if drop:
                remove_count = 0
                for po, pnums in path_ops:
                    remove_count += 1 + len(pnums)
                dropped = out[-remove_count:]
                del out[-remove_count:]
                if dropped:
                    drop_ranges.append((dropped[0][2], dropped[-1][3]))
                removed += 1
            else:
                out.append(tok)
            path_ops = []
        else:
            out.append(tok)

    if removed and drop_ranges:
        drop_ranges.sort()
        merged = []
        for start, end in drop_ranges:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        rebuilt = []
        cursor = 0
        for start, end in merged:
            rebuilt.append(text[cursor:start])
            cursor = end
        rebuilt.append(text[cursor:])
        doc.update_stream(content_xref, "".join(rebuilt).encode("latin1", "ignore"))
    return removed


@dataclass
class WatermarkInfo:
    type_name: str
    page_index: int
    content: str = ""
    rect: Optional[fitz.Rect] = None
    polygon: Optional[List[Tuple[float, float]]] = None
    is_text: bool = True
    selected: bool = True
    xref: Optional[int] = None
    container_xref: Optional[int] = None
    fingerprint: Optional[str] = None
    occurrences: int = 1
    confidence: float = 1.0
    source: str = "auto"
    redaction_rects: Optional[List[fitz.Rect]] = None
    ui_key: str = field(default_factory=lambda: hashlib.sha1(os.urandom(16)).hexdigest())

    def display_text(self) -> str:
        prefix = f"页面 {self.page_index + 1}: {self.type_name}"
        if self.occurrences > 1:
            prefix += f" [重复 {self.occurrences} 页]"
        if self.confidence < 0.999:
            prefix += f" [相似度 {self.confidence:.2f}]"

        body = self.content.strip().replace("\n", " ") if self.content else ""
        if len(body) > 40:
            body = body[:40] + "..."
        return f"{prefix} - {body or '无内容描述'}"


@dataclass
class ImageCandidate:
    page_index: int
    xref: int
    rect: fitz.Rect
    fingerprint: str
    width: int
    height: int
    container_xref: Optional[int] = None


@dataclass
class VectorCandidate:
    page_index: int
    rect: fitz.Rect
    fingerprint: str
    opacity: float
    color_hint: str


class PDFWatermarkRemover:
    def __init__(self, master):
        self.master = master
        self.master.title("PDF 水印去除工具 Mark9")
        self.master.geometry("1280x820")
        self.master.configure(bg=MAIN_BG)

        self.pdf_path = None
        self.original_pdf_path = None
        self.preview_pdf_path = None
        self.preview_temp_files: List[str] = []
        self.preview_generation = 0
        self.is_preview_session = False
        self.decrypted_pdf_cache: Dict[str, Tuple[str, object]] = {}
        self.pdf_passwords: Dict[str, str] = {}
        self.current_source_was_encrypted = False
        self.doc = None
        self.total_pages = 0
        self.current_page = 0
        self.original_img = None
        self.photo_image = None

        self.watermarks: List[WatermarkInfo] = []
        self.text_items: List[WatermarkInfo] = []
        self.graphic_items: List[WatermarkInfo] = []
        self.image_candidates: List[ImageCandidate] = []
        self.vector_candidates: List[VectorCandidate] = []
        self.batch_paths: List[str] = []
        self.batch_results: Dict[str, Dict[str, object]] = {}
        self.batch_index = 0
        self.batch_running = False

        self.scale_factor_x = 1.0
        self.scale_factor_y = 1.0
        self.image_offset_x = 0
        self.image_offset_y = 0

        # ROI 状态
        self.roi_mode = StringVar(value="axis")  # axis / rotated
        self.roi_points_canvas: List[Tuple[float, float]] = []
        self.roi_points_pdf: List[Tuple[float, float]] = []
        self.roi_bbox_pdf: Optional[fitz.Rect] = None
        self.roi_item = None
        self.roi_preview_item = None
        self.rot_start = None
        self.rot_end = None
        self.rot_half_width = 0.0
        self.rot_stage = None  # None / axis / rotated_line / rotated_width

        self._build_ui()
        self._bind_drop_targets()
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        top_frame = Frame(self.master, bg=MAIN_BG)
        top_frame.pack(side=TOP, fill=X, padx=10, pady=10)

        Button(top_frame, text="选择PDF", bg=BUTTON_BG, fg=BUTTON_FG,
               command=self.open_pdf, width=12, height=2).pack(side=LEFT, padx=5)
        Button(top_frame, text="选择文件夹", bg=BUTTON_BG, fg=BUTTON_FG,
               command=self.open_pdf_folder, width=12, height=2).pack(side=LEFT, padx=5)
        Button(top_frame, text="分析水印", bg=BUTTON_BG, fg=BUTTON_FG,
               command=self.analyze_watermarks, width=12, height=2).pack(side=LEFT, padx=5)
        Button(top_frame, text="预览去除效果", bg=BUTTON_BG, fg=BUTTON_FG,
               command=self.preview_selected_watermarks, width=14, height=2).pack(side=LEFT, padx=5)
        Button(top_frame, text="保存当前结果", bg="#28a745", fg=BUTTON_FG,
               command=self.save_current_result, width=14, height=2).pack(side=LEFT, padx=5)
        Button(top_frame, text="返回原文件", bg="#6c757d", fg=BUTTON_FG,
               command=self.restore_original_pdf, width=12, height=2).pack(side=LEFT, padx=5)

        self.mode_var = StringVar(value="当前模式：未打开文件")
        Label(top_frame, textvariable=self.mode_var, bg=MAIN_BG, fg="#555555").pack(side=LEFT, padx=12)

        self.status_var = StringVar(value="欢迎使用 PDF 水印去除工具")
        Label(top_frame, textvariable=self.status_var, bg=MAIN_BG).pack(side=RIGHT, padx=10)

        content_frame = Frame(self.master, bg=MAIN_BG)
        content_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)

        preview_frame = Frame(content_frame, bg=FRAME_BG)
        preview_frame.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 5))

        self.drop_hint_var = StringVar(
            value="支持拖入 PDF 文件" if HAS_TKDND else "拖拽支持需要安装 tkinterdnd2：pip install tkinterdnd2"
        )
        self.drop_hint = Label(preview_frame, textvariable=self.drop_hint_var, bg="#eef5ff", anchor="w")
        self.drop_hint.pack(fill=X, padx=10, pady=(10, 0))

        batch_frame = Frame(preview_frame, bg=FRAME_BG)
        batch_frame.pack(fill=X, padx=10, pady=(6, 0))
        Label(batch_frame, text="批量文件:", bg=FRAME_BG).pack(side=LEFT)
        self.batch_file_var = StringVar()
        self.batch_file_combo = ttk.Combobox(batch_frame, textvariable=self.batch_file_var, state="readonly")
        self.batch_file_combo.pack(side=LEFT, fill=X, expand=True, padx=(6, 0))
        self.batch_file_combo.bind("<<ComboboxSelected>>", self.on_batch_file_selected)

        self.pdf_canvas = Canvas(preview_frame, bg="white", highlightthickness=0)
        self.pdf_canvas.pack(fill=BOTH, expand=True, padx=10, pady=10)
        self.pdf_canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.pdf_canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.pdf_canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.pdf_canvas.bind("<Motion>", self.on_mouse_move)
        self.pdf_canvas.bind("<Button-3>", lambda e: self.clear_roi())

        roi_frame = Frame(preview_frame, bg=FRAME_BG)
        roi_frame.pack(fill=X, padx=10, pady=(0, 5))
        Label(roi_frame, text="选区模式:", bg=FRAME_BG).pack(side=LEFT)
        ttk.Radiobutton(roi_frame, text="矩形", variable=self.roi_mode, value="axis").pack(side=LEFT, padx=(5, 0))
        ttk.Radiobutton(roi_frame, text="斜框", variable=self.roi_mode, value="rotated").pack(side=LEFT, padx=(5, 10))
        Button(roi_frame, text="分析所选区域", command=self.analyze_roi, width=14).pack(side=LEFT, padx=5)
        Button(roi_frame, text="清除选区", command=self.clear_roi, width=10).pack(side=LEFT, padx=5)
        Label(
            roi_frame,
            text="斜框模式：先拖出方向线，再移动鼠标确定宽度并单击确认；右键清空",
            bg=FRAME_BG,
            fg="#666666",
        ).pack(side=LEFT, padx=10)

        nav_frame = Frame(preview_frame, bg=FRAME_BG)
        nav_frame.pack(fill=X, padx=10, pady=(0, 10))
        Button(nav_frame, text="上一页", command=self.prev_page, width=10).pack(side=LEFT, padx=5)
        self.page_var = StringVar(value="0/0")
        Label(nav_frame, textvariable=self.page_var, bg=FRAME_BG).pack(side=LEFT, padx=10)
        Button(nav_frame, text="下一页", command=self.next_page, width=10).pack(side=LEFT, padx=5)

        side_frame = Frame(content_frame, bg=FRAME_BG, width=430)
        side_frame.pack(side=RIGHT, fill=BOTH, padx=(5, 0))
        side_frame.pack_propagate(False)

        search_frame = Frame(side_frame, bg=FRAME_BG)
        search_frame.pack(fill=X, padx=10, pady=10)
        Label(search_frame, text="搜索水印:", bg=FRAME_BG).pack(side=LEFT)
        self.search_var = StringVar()
        Entry(search_frame, textvariable=self.search_var, width=28).pack(side=LEFT, fill=X, expand=True, padx=5)
        Button(search_frame, text="搜索", command=self.search_watermarks).pack(side=LEFT)

        self.notebook = ttk.Notebook(side_frame)
        self.notebook.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))

        self.text_listbox = self._create_watermark_tab("文本水印")
        self.graphic_listbox = self._create_watermark_tab("图形水印")

        self.text_listbox.bind("<<ListboxSelect>>", lambda e: self.on_watermark_select(True))
        self.graphic_listbox.bind("<<ListboxSelect>>", lambda e: self.on_watermark_select(False))

        custom_frame = Frame(side_frame, bg=FRAME_BG)
        custom_frame.pack(fill=X, padx=10, pady=(0, 10))
        Label(custom_frame, text="添加自定义水印关键词:", bg=FRAME_BG).pack(anchor="w")
        self.custom_keyword = StringVar()
        Entry(custom_frame, textvariable=self.custom_keyword).pack(fill=X, pady=5)
        Button(custom_frame, text="添加", command=self.add_custom_keyword).pack(anchor="e")

    def _create_watermark_tab(self, title: str) -> Listbox:
        frame = Frame(self.notebook, bg=FRAME_BG)
        self.notebook.add(frame, text=title)

        list_frame = Frame(frame, bg=FRAME_BG)
        list_frame.pack(fill=BOTH, expand=True, padx=5, pady=5)

        listbox = Listbox(list_frame, bg=FRAME_BG, selectmode="multiple")
        listbox.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        scrollbar.pack(side=RIGHT, fill=Y)
        listbox.config(yscrollcommand=scrollbar.set)

        btn_frame = Frame(frame, bg=FRAME_BG)
        btn_frame.pack(fill=X, padx=5, pady=5)
        Button(btn_frame, text="全选", command=lambda lb=listbox: self.select_all(lb), width=10).pack(side=LEFT, padx=5)
        Button(btn_frame, text="取消全选", command=lambda lb=listbox: self.clear_all(lb), width=10).pack(side=LEFT, padx=5)
        return listbox

    def _bind_drop_targets(self):
        if not HAS_TKDND:
            return
        for widget in (self.master, self.pdf_canvas, self.drop_hint):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self.on_drop_file)
            except Exception:
                pass

    def _update_mode_hint(self):
        if not self.pdf_path:
            self.mode_var.set("当前模式：未打开文件")
            return
        name = os.path.basename(self.pdf_path)
        if self.is_preview_session:
            self.mode_var.set(f"当前模式：预览结果（{name}，可继续框选/分析后再保存）")
        else:
            source_name = os.path.basename(self.original_pdf_path or self.pdf_path)
            suffix = "，已授权解密" if self.current_source_was_encrypted else ""
            self.mode_var.set(f"当前模式：原始文件（{source_name}{suffix}）")

    def _cleanup_temp_previews(self):
        remaining = []
        for temp_path in self.preview_temp_files:
            try:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                remaining.append(temp_path)
        self.preview_temp_files = remaining

    def _cleanup_decrypted_pdfs(self):
        remaining = {}
        active_path = os.path.normcase(os.path.abspath(self.pdf_path)) if self.pdf_path else None
        for source_key, (temp_path, result) in self.decrypted_pdf_cache.items():
            try:
                if temp_path and os.path.exists(temp_path):
                    if active_path and os.path.normcase(os.path.abspath(temp_path)) == active_path:
                        remaining[source_key] = (temp_path, result)
                    else:
                        os.remove(temp_path)
            except Exception:
                remaining[source_key] = (temp_path, result)
        self.decrypted_pdf_cache = remaining

    def _ask_pdf_password(self, file_path: str) -> Optional[str]:
        return simpledialog.askstring(
            "PDF 需要密码",
            f"文件已加密，请输入合法持有的用户密码或所有者密码：\n\n{os.path.basename(file_path)}",
            show="*",
            parent=self.master,
        )

    def _update_decryption_progress(self, file_path: str, percent: int):
        self.status_var.set(
            f"正在授权解密：{os.path.basename(file_path)} {max(0, min(100, percent))}%"
        )
        self.master.update_idletasks()

    def _prepare_pdf_for_analysis(self, file_path: str):
        source_path = os.path.abspath(file_path)
        source_key = os.path.normcase(source_path)
        cached = self.decrypted_pdf_cache.get(source_key)
        if cached and os.path.isfile(cached[0]):
            return cached

        self.status_var.set(f"正在检查 PDF 加密状态：{os.path.basename(source_path)}")
        self.master.update_idletasks()
        if not is_pdf_encrypted(source_path):
            return source_path, None

        fd, temp_path = tempfile.mkstemp(prefix="pdfwm_decrypted_", suffix=".pdf")
        os.close(fd)
        password = self.pdf_passwords.get(source_key)
        try:
            while True:
                try:
                    result = decrypt_pdf(
                        source_path,
                        temp_path,
                        password=password,
                        require_encrypted=True,
                        progress=lambda percent: self._update_decryption_progress(
                            source_path, percent
                        ),
                    )
                    break
                except PasswordRequiredError:
                    password = self._ask_pdf_password(source_path)
                    if password is None:
                        raise InterruptedError("用户取消输入 PDF 密码")
                except IncorrectPasswordError:
                    self.pdf_passwords.pop(source_key, None)
                    messagebox.showerror("密码错误", "提供的 PDF 用户密码或所有者密码不正确，请重试")
                    password = self._ask_pdf_password(source_path)
                    if password is None:
                        raise InterruptedError("用户取消输入 PDF 密码")

            if password is not None:
                self.pdf_passwords[source_key] = password
            self.decrypted_pdf_cache[source_key] = (temp_path, result)
            details = result.encryption
            algorithm = details.stream_method if details else "unknown"
            bits = details.key_bits if details else 0
            self.status_var.set(
                f"已授权解密：{os.path.basename(source_path)}（{algorithm}, {bits}-bit）"
            )
            if result.had_digital_signatures:
                messagebox.showwarning(
                    "数字签名提示",
                    "输入 PDF 含数字签名。解密和后续去水印会重写文件，原签名通常不再有效。",
                )
            return temp_path, result
        except Exception:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
            raise

    def on_close(self):
        try:
            if self.doc:
                self.doc.close()
        except Exception:
            pass
        self.doc = None
        self._cleanup_temp_previews()
        self.pdf_path = None
        self._cleanup_decrypted_pdfs()
        self.pdf_passwords.clear()
        try:
            if self.preview_pdf_path and os.path.exists(self.preview_pdf_path):
                os.remove(self.preview_pdf_path)
        except Exception:
            pass
        self.master.destroy()

    # ---------------- DND ----------------
    def on_drop_file(self, event):
        files = self._parse_drop_files(event.data)
        if not files:
            return
        pdf_files = [path for path in files if path.lower().endswith(".pdf") and os.path.isfile(path)]
        if not pdf_files:
            messagebox.showerror("文件类型错误", "拖入的内容中没有 PDF 文件")
            return
        self.import_pdf_paths(pdf_files)

    @staticmethod
    def _parse_drop_files(raw: str) -> List[str]:
        raw = raw.strip()
        if not raw:
            return []
        files = []
        token = ""
        in_brace = False
        for ch in raw:
            if ch == "{":
                in_brace = True
                if token.strip():
                    files.append(token.strip())
                    token = ""
            elif ch == "}":
                in_brace = False
                if token.strip():
                    files.append(token.strip())
                    token = ""
            elif ch == " " and not in_brace:
                if token.strip():
                    files.append(token.strip())
                    token = ""
            else:
                token += ch
        if token.strip():
            files.append(token.strip())
        return files

    # ---------------- PDF loading / rendering ----------------
    def open_pdf(self):
        file_paths = filedialog.askopenfilenames(
            title="选择一个或多个 PDF 文件",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
        )
        if file_paths:
            self.import_pdf_paths(list(file_paths))

    def open_pdf_folder(self):
        folder = filedialog.askdirectory(title="选择包含 PDF 的文件夹")
        if not folder:
            return
        pdf_files = []
        for root, _, names in os.walk(folder):
            pdf_files.extend(os.path.join(root, name) for name in names if name.lower().endswith(".pdf"))
        pdf_files.sort(key=str.lower)
        if not pdf_files:
            messagebox.showinfo("未找到 PDF", "所选文件夹及其子文件夹中没有 PDF 文件")
            return
        self.import_pdf_paths(pdf_files)

    def import_pdf_paths(self, paths: List[str]):
        normalized = []
        seen = set()
        for path in paths:
            full_path = os.path.abspath(path)
            key = os.path.normcase(full_path)
            if key not in seen and os.path.isfile(full_path) and full_path.lower().endswith(".pdf"):
                seen.add(key)
                normalized.append(full_path)
        if not normalized:
            return
        if self.batch_running:
            messagebox.showinfo("批量识别进行中", "请等待当前批次识别完成后再导入新文件")
            return
        self.batch_paths = normalized
        self.batch_results = {}
        self.batch_index = 0
        self.batch_running = True
        self._refresh_batch_combo()
        self._process_next_batch_pdf()

    def _process_next_batch_pdf(self):
        if self.batch_index >= len(self.batch_paths):
            self.batch_running = False
            self._refresh_batch_combo()
            total_marks = sum(len(result.get("watermarks", [])) for result in self.batch_results.values())
            self.status_var.set(f"批量识别完成：{len(self.batch_results)}/{len(self.batch_paths)} 个文件，共 {total_marks} 个候选")
            messagebox.showinfo("批量识别完成", self.status_var.get())
            return
        path = self.batch_paths[self.batch_index]
        self._refresh_batch_combo()
        self.status_var.set(f"批量识别 {self.batch_index + 1}/{len(self.batch_paths)}：{os.path.basename(path)}")
        if self.load_pdf(path):
            self.analyze_watermarks()
        else:
            self.batch_results[path] = {"error": "无法打开文件", "watermarks": []}
            self.batch_index += 1
            self.master.after(0, self._process_next_batch_pdf)

    def _refresh_batch_combo(self):
        values = []
        for index, path in enumerate(self.batch_paths):
            if path in self.batch_results:
                result = self.batch_results[path]
                status = "失败" if result.get("error") else f"{len(result.get('watermarks', []))} 个候选"
            elif self.batch_running and index == self.batch_index:
                status = "识别中"
            else:
                status = "等待中"
            values.append(f"[{status}] {os.path.basename(path)}")
        self.batch_file_combo["values"] = values
        if values:
            self.batch_file_combo.current(min(self.batch_index, len(values) - 1))

    def on_batch_file_selected(self, _event=None):
        if self.batch_running:
            return
        index = self.batch_file_combo.current()
        if index < 0 or index >= len(self.batch_paths):
            return
        path = self.batch_paths[index]
        result = self.batch_results.get(path)
        if not result or result.get("error") or not self.load_pdf(path):
            return
        self.watermarks = list(result.get("watermarks", []))
        self.image_candidates = list(result.get("image_candidates", []))
        self.vector_candidates = list(result.get("vector_candidates", []))
        self._update_watermark_list()

    def load_pdf(self, file_path: str, as_original: bool = True, start_page: int = 0):
        if not file_path.lower().endswith(".pdf"):
            messagebox.showerror("文件类型错误", "请选择 PDF 文件")
            return False
        try:
            if self.doc:
                self.doc.close()
                self.doc = None

            if as_original:
                self._cleanup_temp_previews()
                analysis_path, decrypt_result = self._prepare_pdf_for_analysis(file_path)
                self.original_pdf_path = os.path.abspath(file_path)
                self.preview_pdf_path = None
                self.preview_generation = 0
                self.is_preview_session = False
                self.current_source_was_encrypted = decrypt_result is not None
            else:
                analysis_path = file_path
                self.preview_pdf_path = file_path
                self.is_preview_session = True

            self.pdf_path = analysis_path
            self.doc = fitz.open(analysis_path)
            self.total_pages = self.doc.page_count
            self.current_page = max(0, min(start_page, self.total_pages - 1))
            self.page_var.set(f"{self.current_page + 1}/{self.total_pages}")
            self.watermarks = []
            self.text_items = []
            self.graphic_items = []
            self.image_candidates = []
            self.vector_candidates = []
            self.text_listbox.delete(0, END)
            self.graphic_listbox.delete(0, END)
            self.clear_roi()
            self.display_page(self.current_page)
            self._update_mode_hint()
            if self.current_source_was_encrypted and as_original:
                self.status_var.set(f"已解密并打开: {os.path.basename(file_path)}")
            else:
                self.status_var.set(f"已打开: {os.path.basename(file_path)}")
            return True
        except InterruptedError:
            self.status_var.set("已取消打开加密 PDF")
            return False
        except PdfDecryptionError as e:
            messagebox.showerror("PDF 解密失败", str(e))
            self.status_var.set("PDF 解密失败")
            return False
        except Exception as e:
            messagebox.showerror("错误", f"打开 PDF 失败: {e}")
            return False

    def display_page(self, page_index: int):
        if not self.doc or page_index < 0 or page_index >= self.total_pages:
            return

        page = self.doc[page_index]
        pix = page.get_pixmap(matrix=fitz.Matrix(1.4, 1.4), alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        self.pdf_canvas.update_idletasks()
        canvas_width = max(700, self.pdf_canvas.winfo_width())
        canvas_height = max(700, self.pdf_canvas.winfo_height())

        img_ratio = img.width / img.height
        canvas_ratio = canvas_width / canvas_height
        if img_ratio > canvas_ratio:
            new_width = canvas_width
            new_height = int(canvas_width / img_ratio)
        else:
            new_height = canvas_height
            new_width = int(canvas_height * img_ratio)

        img = img.resize((new_width, new_height), Image.LANCZOS)
        self.original_img = img
        self.photo_image = ImageTk.PhotoImage(img)

        self.pdf_canvas.delete("all")
        self.image_offset_x = (canvas_width - new_width) // 2
        self.image_offset_y = (canvas_height - new_height) // 2
        self.pdf_canvas.create_image(self.image_offset_x, self.image_offset_y, image=self.photo_image, anchor="nw")

        self.scale_factor_x = page.rect.width / new_width
        self.scale_factor_y = page.rect.height / new_height

        self.current_page = page_index
        self.page_var.set(f"{page_index + 1}/{self.total_pages}")
        self._redraw_roi_if_same_page()

    def prev_page(self):
        if self.current_page > 0:
            self.display_page(self.current_page - 1)

    def next_page(self):
        if self.current_page < self.total_pages - 1:
            self.display_page(self.current_page + 1)

    # ---------------- ROI: axis / rotated ----------------
    def on_mouse_down(self, event):
        if not self.doc:
            return
        if not self._point_on_page(event.x, event.y):
            return

        if self.roi_mode.get() == "axis":
            self.rot_stage = "axis"
            self.roi_points_canvas = [(event.x, event.y), (event.x, event.y)]
            self._draw_axis_preview()
            return

        # 斜框模式
        if self.rot_stage == "rotated_width" and self.rot_start and self.rot_end:
            self._finalize_rotated_roi(event.x, event.y)
            return

        self.clear_roi(keep_status=True)
        self.rot_stage = "rotated_line"
        self.rot_start = (event.x, event.y)
        self.rot_end = (event.x, event.y)
        self._draw_line_preview()

    def on_mouse_drag(self, event):
        if not self.doc:
            return
        if self.roi_mode.get() == "axis" and self.rot_stage == "axis" and self.roi_points_canvas:
            self.roi_points_canvas[1] = (event.x, event.y)
            self._draw_axis_preview()
        elif self.roi_mode.get() == "rotated" and self.rot_stage == "rotated_line" and self.rot_start:
            self.rot_end = (event.x, event.y)
            self._draw_line_preview()

    def on_mouse_up(self, event):
        if not self.doc:
            return
        if self.roi_mode.get() == "axis" and self.rot_stage == "axis" and self.roi_points_canvas:
            self.roi_points_canvas[1] = (event.x, event.y)
            self._finalize_axis_roi()
            return

        if self.roi_mode.get() == "rotated" and self.rot_stage == "rotated_line" and self.rot_start:
            self.rot_end = (event.x, event.y)
            if self._distance(self.rot_start, self.rot_end) < 12:
                self.clear_roi(keep_status=True)
                return
            self.rot_stage = "rotated_width"
            self.status_var.set("已确定斜框方向，请移动鼠标设置宽度，然后单击确认")
            self._draw_rotated_preview(event.x, event.y)

    def on_mouse_move(self, event):
        if self.roi_mode.get() == "rotated" and self.rot_stage == "rotated_width" and self.rot_start and self.rot_end:
            self._draw_rotated_preview(event.x, event.y)

    def clear_roi(self, keep_status: bool = False):
        if self.roi_item is not None:
            self.pdf_canvas.delete(self.roi_item)
            self.roi_item = None
        if self.roi_preview_item is not None:
            self.pdf_canvas.delete(self.roi_preview_item)
            self.roi_preview_item = None
        self.roi_points_canvas = []
        self.roi_points_pdf = []
        self.roi_bbox_pdf = None
        self.rot_start = None
        self.rot_end = None
        self.rot_half_width = 0.0
        self.rot_stage = None
        if not keep_status:
            self.status_var.set("已清除选区")

    def _draw_axis_preview(self):
        if len(self.roi_points_canvas) != 2:
            return
        (x0, y0), (x1, y1) = self.roi_points_canvas
        if self.roi_preview_item is not None:
            self.pdf_canvas.delete(self.roi_preview_item)
        self.roi_preview_item = self.pdf_canvas.create_rectangle(
            x0, y0, x1, y1, outline=ROI_OUTLINE, width=2, dash=(6, 3)
        )

    def _finalize_axis_roi(self):
        (x0, y0), (x1, y1) = self.roi_points_canvas
        if abs(x1 - x0) < 10 or abs(y1 - y0) < 10:
            self.clear_roi(keep_status=True)
            return
        self.roi_points_canvas = [
            (min(x0, x1), min(y0, y1)),
            (max(x0, x1), min(y0, y1)),
            (max(x0, x1), max(y0, y1)),
            (min(x0, x1), max(y0, y1)),
        ]
        self._commit_roi_polygon(self.roi_points_canvas)
        self.status_var.set("矩形选区已创建")

    def _draw_line_preview(self):
        if self.roi_preview_item is not None:
            self.pdf_canvas.delete(self.roi_preview_item)
        if self.rot_start and self.rot_end:
            self.roi_preview_item = self.pdf_canvas.create_line(
                self.rot_start[0], self.rot_start[1], self.rot_end[0], self.rot_end[1],
                fill=ROI_OUTLINE, width=2, dash=(6, 3)
            )

    def _draw_rotated_preview(self, x: float, y: float):
        if not self.rot_start or not self.rot_end:
            return
        self.rot_half_width = self._perpendicular_distance((x, y), self.rot_start, self.rot_end)
        poly = self._build_rotated_rect(self.rot_start, self.rot_end, self.rot_half_width)
        if self.roi_preview_item is not None:
            self.pdf_canvas.delete(self.roi_preview_item)
        self.roi_preview_item = self.pdf_canvas.create_polygon(
            *self._flatten(poly), outline=ROI_OUTLINE, fill="", width=2, dash=(6, 3)
        )

    def _finalize_rotated_roi(self, x: float, y: float):
        self.rot_half_width = self._perpendicular_distance((x, y), self.rot_start, self.rot_end)
        if self.rot_half_width < 5:
            self.status_var.set("斜框宽度太小，请重新框选")
            return
        poly = self._build_rotated_rect(self.rot_start, self.rot_end, self.rot_half_width)
        self._commit_roi_polygon(poly)
        self.status_var.set("斜框选区已创建")

    def _commit_roi_polygon(self, canvas_points: List[Tuple[float, float]]):
        if self.roi_preview_item is not None:
            self.pdf_canvas.delete(self.roi_preview_item)
            self.roi_preview_item = None
        if self.roi_item is not None:
            self.pdf_canvas.delete(self.roi_item)
        self.roi_points_canvas = canvas_points
        self.roi_item = self.pdf_canvas.create_polygon(
            *self._flatten(canvas_points), outline=ROI_OUTLINE, fill="", width=2
        )
        self.roi_points_pdf = [self._canvas_to_pdf(x, y) for x, y in canvas_points]
        xs = [p[0] for p in self.roi_points_pdf]
        ys = [p[1] for p in self.roi_points_pdf]
        self.roi_bbox_pdf = fitz.Rect(min(xs), min(ys), max(xs), max(ys))
        self.rot_stage = None

    def _redraw_roi_if_same_page(self):
        if not self.roi_points_pdf:
            return
        canvas_points = [self._pdf_to_canvas(x, y) for x, y in self.roi_points_pdf]
        if self.roi_item is not None:
            self.pdf_canvas.delete(self.roi_item)
        self.roi_item = self.pdf_canvas.create_polygon(
            *self._flatten(canvas_points), outline=ROI_OUTLINE, fill="", width=2
        )

    # ---------------- analysis ----------------
    def analyze_watermarks(self):
        if not self.doc:
            messagebox.showinfo("提示", "请先打开 PDF 文件")
            return
        self.status_var.set("正在分析水印...")
        self.watermarks = []
        self.text_items = []
        self.graphic_items = []
        self.image_candidates = []
        self.vector_candidates = []
        self.text_listbox.delete(0, END)
        self.graphic_listbox.delete(0, END)
        threading.Thread(target=self._analyze_watermarks_thread, daemon=True).start()

    def _analyze_watermarks_thread(self):
        try:
            text_marks = []
            image_candidates = []
            vector_candidates = []
            for page_idx in range(self.total_pages):
                page = self.doc[page_idx]
                page_vectors = self._collect_vector_candidates(page, page_idx)
                vector_candidates.extend(page_vectors)
                text_marks.extend(self._find_text_watermarks(page, page_idx))
                # 已有矢量路径水印时不再叠加像素级斜向检测，避免产生大范围误检矩形
                if not any(v.color_hint.startswith("soft-vector") for v in page_vectors):
                    text_marks.extend(self._find_rendered_diagonal_watermarks(page, page_idx))
                image_candidates.extend(self._collect_image_candidates(page, page_idx))
                if page_idx % max(1, self.total_pages // 10) == 0:
                    pct = int(page_idx / max(1, self.total_pages) * 100)
                    self.master.after(0, lambda p=pct: self.status_var.set(f"正在扫描页面... {p}%"))

            graphic_marks = self._find_repeated_graphics(image_candidates, vector_candidates)
            text_marks = self._mark_repeated_texts(text_marks)

            self.image_candidates = image_candidates
            self.vector_candidates = vector_candidates
            self.watermarks = text_marks + graphic_marks
            self.master.after(0, self._update_watermark_list)
        except Exception as e:
            self.master.after(0, lambda: messagebox.showerror("错误", f"分析水印时出错: {e}"))
            self.master.after(0, lambda: self.status_var.set("分析水印失败"))
            if self.batch_running:
                path = self.batch_paths[self.batch_index]
                self.batch_results[path] = {"error": str(e), "watermarks": []}
                self.batch_index += 1
                self.master.after(0, self._process_next_batch_pdf)

    def _find_text_watermarks(self, page, page_idx: int) -> List[WatermarkInfo]:
        results = []
        text_dict = page.get_text("dict")
        page_width = page.rect.width
        page_height = page.rect.height
        top_margin = page_height * 0.12
        bottom_margin = page_height * 0.12
        side_margin = page_width * 0.12

        for block in text_dict.get("blocks", []):
            if "lines" not in block:
                continue
            for line in block["lines"]:
                line_text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                if not line_text:
                    continue
                rect = None
                rotated = False
                color_score = False
                for span in line.get("spans", []):
                    span_rect = fitz.Rect(span["bbox"])
                    rect = span_rect if rect is None else rect | span_rect
                    direction = line.get("dir", (1.0, 0.0))
                    if abs(direction[0]) < 0.96 or abs(direction[1]) > 0.2:
                        rotated = True
                    if span.get("color", 0) > 0x777777:
                        color_score = True

                keyword_hit = any(k.lower() in line_text.lower() for k in WATERMARK_KEYWORDS)
                in_edge = rect and (
                    rect.y0 <= top_margin or rect.y1 >= page_height - bottom_margin or
                    rect.x0 <= side_margin or rect.x1 >= page_width - side_margin
                )
                footer_qr_text = self._is_footer_qr_text(line_text, rect, page)

                # 仅当关键词命中且位于页面边缘或文字旋转时才视为文本水印，
                # 避免把正文中恰好包含品牌词（如 Hujiang）的整行误判为水印。
                keyword_text = keyword_hit and (in_edge or rotated)

                if keyword_text or rotated or footer_qr_text or (in_edge and color_score):
                    wm_type = "关键词文本" if keyword_text else "旋转文本"
                    if in_edge and not keyword_text and not rotated:
                        wm_type = "边缘文本"
                    if footer_qr_text:
                        wm_type = "底部二维码说明文字"
                    results.append(
                        WatermarkInfo(
                            type_name=wm_type,
                            page_index=page_idx,
                            content=line_text,
                            rect=rect,
                            is_text=True,
                        )
                    )
        return results

    @staticmethod
    def _is_footer_qr_text(line_text: str, rect: Optional[fitz.Rect], page) -> bool:
        if not rect:
            return False
        page_height = page.rect.height
        page_width = page.rect.width
        text = line_text.lower().replace(" ", "")
        footer_position = (
            rect.y0 >= page_height * 0.86 and
            page_width * 0.20 <= rect.x0 <= page_width * 0.35 and
            rect.x1 >= page_width * 0.60
        )
        footer_terms = ("xdfdeyu", "qq", "623687900", "扫一扫", "微信", "公众")
        return footer_position and any(term.lower() in text for term in footer_terms)

    def _find_rendered_diagonal_watermarks(self, page, page_idx: int) -> List[WatermarkInfo]:
        """Find pale diagonal watermark glyphs that PDF text extraction cannot decode."""
        if page.rect.width < 200 or page.rect.height < 200:
            return []

        scale = 1.6
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            pixels = np.frombuffer(pix.samples, dtype=np.uint8)
            image = pixels.reshape(pix.height, pix.width, pix.n)
            if pix.n >= 3:
                gray = cv2.cvtColor(image[:, :, :3], cv2.COLOR_RGB2GRAY)
            else:
                gray = image[:, :, 0]
        except Exception:
            return []

        height, width = gray.shape
        pale = cv2.inRange(gray, 145, 248)
        pale = cv2.morphologyEx(
            pale,
            cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)),
        )
        count, _, stats, _ = cv2.connectedComponentsWithStats(pale, 8)
        comps = []
        for index in range(1, count):
            x, y, box_width, box_height, area = [int(value) for value in stats[index]]
            if not (35 <= area <= 5000):
                continue
            if not (5 <= box_width <= width * 0.18 and 5 <= box_height <= height * 0.18):
                continue
            center_x = x + box_width / 2
            center_y = y + box_height / 2
            if not (width * 0.03 <= center_x <= width * 0.97):
                continue
            if not (height * 0.05 <= center_y <= height * 0.95):
                continue
            comps.append((center_x, center_y, x, y, box_width, box_height, area))

        if len(comps) < 6:
            return []

        xs = np.array([c[0] for c in comps], dtype=np.float64)
        ys = np.array([c[1] for c in comps], dtype=np.float64)
        fit_x, fit_y = xs.copy(), ys.copy()
        for _ in range(4):
            if len(fit_x) < 6:
                break
            slope, intercept = np.polyfit(fit_x, fit_y, 1)
            dist = np.abs(fit_y - (slope * fit_x + intercept)) / math.sqrt(slope * slope + 1)
            fit_x, fit_y = fit_x[dist <= np.percentile(dist, 70)], fit_y[dist <= np.percentile(dist, 70)]
        if len(fit_x) < 6:
            return []
        slope, intercept = np.polyfit(fit_x, fit_y, 1)

        norm = math.sqrt(slope * slope + 1)
        signed = (ys - (slope * xs + intercept)) / norm
        order = np.argsort(signed)
        sorted_signed = signed[order]
        clusters = []
        current = [sorted_signed[0]]
        for i in range(1, len(sorted_signed)):
            if sorted_signed[i] - current[-1] > 25:
                clusters.append(current)
                current = []
            current.append(sorted_signed[i])
        clusters.append(current)

        matched = []
        for cl in clusters:
            if len(cl) < 4:
                continue
            lo, hi = cl[0] - 15, cl[-1] + 15
            for c, sd in zip(comps, signed):
                if lo <= sd <= hi:
                    matched.append(c)

        if len(matched) < 6:
            return []

        redaction_rects = []
        for _, _, x, y, box_width, box_height, _ in matched:
            redaction_rects.append(
                fitz.Rect(
                    max(0, (x - 8) / scale),
                    max(0, (y - 8) / scale),
                    min(page.rect.width, (x + box_width + 8) / scale),
                    min(page.rect.height, (y + box_height + 8) / scale),
                )
            )

        rect = redaction_rects[0]
        for candidate_rect in redaction_rects[1:]:
            rect |= candidate_rect

        return [
            WatermarkInfo(
                type_name="斜向浅灰水印",
                page_index=page_idx,
                content="渲染像素检测：可能包含 沪江德语 www.hujiang.com",
                rect=rect,
                is_text=True,
                confidence=min(0.96, 0.72 + len(matched) / 100.0),
                source="rendered-diagonal",
                occurrences=len(matched),
                redaction_rects=redaction_rects,
            )
        ]

    def _collect_image_candidates(self, page, page_idx: int) -> List[ImageCandidate]:
        candidates = []
        for info in page.get_images(full=True):
            xref = info[0]
            try:
                img = self.doc.extract_image(xref)
                raw = img.get("image", b"")
                if not raw:
                    continue
                fingerprint = hashlib.sha1(raw).hexdigest()
                rects = page.get_image_rects(xref, transform=False)
                container_xref = info[-1] if isinstance(info[-1], int) and info[-1] > 0 else None
                for rect in rects:
                    candidates.append(
                        ImageCandidate(
                            page_index=page_idx,
                            xref=xref,
                            rect=fitz.Rect(rect),
                            fingerprint=fingerprint,
                            width=img.get("width", 0),
                            height=img.get("height", 0),
                            container_xref=container_xref,
                        )
                    )
            except Exception:
                continue
        return candidates

    def _collect_vector_candidates(self, page, page_idx: int) -> List[VectorCandidate]:
        candidates = []
        page_area = page.rect.width * page.rect.height
        for item in page.get_drawings():
            rect = fitz.Rect(item.get("rect", fitz.Rect()))
            if rect.is_empty or rect.width <= 0 or rect.height <= 0:
                continue
            area_ratio = (rect.width * rect.height) / max(1.0, page_area)
            fill_opacity = item.get("fill_opacity")
            stroke_opacity = item.get("stroke_opacity")
            opacity = min(
                1.0,
                min(v for v in [fill_opacity, stroke_opacity, 1.0] if v is not None)
            )
            fill = item.get("fill")
            color = item.get("color")
            color_hint = f"{fill or color}"

            color_value = fill or color
            luminance = self._color_luminance(color_value)
            shallow_color = luminance is not None and luminance >= 0.72
            dark_color = luminance is not None and luminance <= 0.25

            # Mark5: 兼容两类图形水印：
            # 1) Mark4 已支持的大面积、浅色/半透明矢量；
            # 2) 顶部/边缘重复出现的黑色不透明细线或细框，常见于下载水印/裁切框。
            near_edge = (
                rect.y0 <= page.rect.height * 0.08 or
                rect.y1 >= page.rect.height * 0.92 or
                rect.x0 <= page.rect.width * 0.08 or
                rect.x1 >= page.rect.width * 0.92
            )
            horizontal_rule = rect.width >= page.rect.width * 0.18 and rect.height <= max(3.0, page.rect.height * 0.006)
            vertical_rule = rect.height >= page.rect.height * 0.02 and rect.width <= max(3.0, page.rect.width * 0.006)
            top_header_rule = (
                dark_color and
                horizontal_rule and
                rect.y0 <= page.rect.height * 0.08 and
                rect.height <= 1.5
            )
            dark_edge_rule = dark_color and near_edge and (horizontal_rule or vertical_rule) and not top_header_rule
            large_soft_shape = rect.width >= 40 and rect.height >= 12 and area_ratio >= 0.02 and (opacity < 0.9 or shallow_color)

            if large_soft_shape or dark_edge_rule:
                vector_kind = "dark-edge-rule" if dark_edge_rule else "soft-vector"
                slim = {
                    "type": item.get("type"),
                    "kind": vector_kind,
                    "items": [(seg[0],) for seg in item.get("items", [])[:8]],
                    "width": round(item.get("width", 0) or 0, 1),
                    "fill": tuple(round(v, 2) for v in fill) if fill else None,
                    "color": tuple(round(v, 2) for v in color) if color else None,
                }
                fingerprint = hashlib.sha1(repr(slim).encode("utf-8")).hexdigest()
                candidates.append(
                    VectorCandidate(
                        page_index=page_idx,
                        rect=rect,
                        fingerprint=fingerprint,
                        opacity=opacity,
                        color_hint=f"{vector_kind} {color_hint}",
                    )
                )
        return candidates

    @staticmethod
    def _color_luminance(color_value) -> Optional[float]:
        if not color_value or not isinstance(color_value, tuple) or len(color_value) < 3:
            return None
        r, g, b = color_value[:3]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def _find_repeated_graphics(
        self,
        image_candidates: List[ImageCandidate],
        vector_candidates: List[VectorCandidate],
    ) -> List[WatermarkInfo]:
        watermarks: List[WatermarkInfo] = []

        def position_key(page_idx: int, rect: fitz.Rect) -> str:
            page = self.doc[page_idx]
            cx = ((rect.x0 + rect.x1) / 2) / max(1, page.rect.width)
            cy = ((rect.y0 + rect.y1) / 2) / max(1, page.rect.height)
            rw = rect.width / max(1, page.rect.width)
            rh = rect.height / max(1, page.rect.height)
            return f"{round(cx, 2)}_{round(cy, 2)}_{round(rw, 2)}_{round(rh, 2)}"

        img_groups: Dict[str, List[ImageCandidate]] = {}
        for cand in image_candidates:
            key = f"{cand.fingerprint}:{position_key(cand.page_index, cand.rect)}"
            img_groups.setdefault(key, []).append(cand)

        for group in img_groups.values():
            pages = {g.page_index for g in group}
            if len(pages) >= 2:
                repeated_pages = len(pages)
                for cand in group:
                    watermarks.append(
                WatermarkInfo(
                            type_name=self._classify_repeated_image(cand),
                            page_index=cand.page_index,
                            content=self._describe_repeated_image(cand),
                            rect=cand.rect,
                            is_text=False,
                            xref=cand.xref,
                            container_xref=cand.container_xref,
                            fingerprint=cand.fingerprint,
                            occurrences=repeated_pages,
                        )
                    )

        vec_groups: Dict[str, List[VectorCandidate]] = {}
        for cand in vector_candidates:
            key = f"{cand.fingerprint}:{position_key(cand.page_index, cand.rect)}"
            vec_groups.setdefault(key, []).append(cand)

        for group in vec_groups.values():
            pages = {g.page_index for g in group}
            if len(pages) >= max(2, math.ceil(self.total_pages * 0.3)):
                for cand in group:
                    is_soft = cand.color_hint.startswith("soft-vector")
                    watermarks.append(
                        WatermarkInfo(
                            type_name="斜向浅色矢量水印" if is_soft else "重复矢量水印",
                            page_index=cand.page_index,
                            content=f"opacity={cand.opacity:.2f} color={cand.color_hint}",
                            rect=cand.rect,
                            is_text=False,
                            fingerprint=cand.fingerprint,
                            occurrences=len(pages),
                            source="soft-vector" if is_soft else "auto",
                        )
                    )

        # 去重，避免重复图片水印反复插入 UI
        uniq = {}
        for wm in watermarks:
            key = (wm.page_index, wm.type_name, wm.xref, wm.fingerprint, tuple(round(v, 1) for v in wm.rect) if wm.rect else None)
            uniq[key] = wm
        return list(uniq.values())

    def _classify_repeated_image(self, cand: ImageCandidate) -> str:
        page = self.doc[cand.page_index]
        rect = cand.rect
        page_width = max(1.0, page.rect.width)
        page_height = max(1.0, page.rect.height)

        if rect.y0 >= page_height * 0.82 and rect.x1 <= page_width * 0.35:
            return "底部二维码水印"

        if (
            rect.width >= page_width * 0.55 and
            rect.height >= page_height * 0.55 and
            rect.y0 <= page_height * 0.12
        ):
            return "新东方在线右侧背景水印"

        return "重复图片水印"

    def _describe_repeated_image(self, cand: ImageCandidate) -> str:
        type_name = self._classify_repeated_image(cand)
        if type_name == "底部二维码水印":
            return "每页底部二维码"
        if type_name == "新东方在线右侧背景水印":
            return "新东方在线 / www.koolearn.com / 网络课堂电子教材系列"
        return f"图片 {cand.width}x{cand.height}"

    def _mark_repeated_texts(self, text_marks: List[WatermarkInfo]) -> List[WatermarkInfo]:
        groups: Dict[str, List[WatermarkInfo]] = {}
        for wm in text_marks:
            if not wm.rect:
                continue
            page = self.doc[wm.page_index]
            cx = ((wm.rect.x0 + wm.rect.x1) / 2) / max(1, page.rect.width)
            cy = ((wm.rect.y0 + wm.rect.y1) / 2) / max(1, page.rect.height)
            key = f"{wm.content.lower()}::{round(cx, 1)}::{round(cy, 1)}"
            groups.setdefault(key, []).append(wm)
        for marks in groups.values():
            if len(marks) >= 2:
                for wm in marks:
                    wm.occurrences = len(marks)
                    if "重复" not in wm.type_name:
                        wm.type_name = f"{wm.type_name} (重复)"
        return text_marks

    def analyze_roi(self):
        if not self.doc:
            messagebox.showinfo("提示", "请先打开 PDF 文件")
            return
        if not self.roi_bbox_pdf or not self.roi_points_pdf:
            messagebox.showinfo("提示", "请先框选区域")
            return

        current_hits = self._scan_roi_on_page(self.current_page, self.roi_bbox_pdf, self.roi_points_pdf)
        if not current_hits:
            visual_score = self._roi_visual_ink_score(self.current_page, self.roi_bbox_pdf, self.roi_points_pdf)
            if visual_score <= 0.0:
                self.status_var.set("选区中未发现文本/图形候选")
                messagebox.showinfo("提示", "选区中未发现明显水印")
                return
            current_hits = [
                WatermarkInfo(
                    type_name="选区视觉图形",
                    page_index=self.current_page,
                    content=f"基于渲染像素的选区图形 score={visual_score:.3f}",
                    rect=self.roi_bbox_pdf,
                    polygon=self.roi_points_pdf,
                    is_text=False,
                    confidence=min(0.99, 0.82 + visual_score),
                    source="roi-visual",
                )
            ]

        all_hits = list(current_hits)
        should_scan_all = messagebox.askyesno(
            "选区分析",
            "当前页选区内已发现候选水印。\n是否按相同相对位置扫描全部页面的重复图形/图像？"
        )

        if should_scan_all:
            template = self._prepare_roi_template(self.current_page, self.roi_bbox_pdf, self.roi_points_pdf)
            rel_poly = self._polygon_to_relative(self.roi_points_pdf, self.doc[self.current_page])
            for page_idx in range(self.total_pages):
                if page_idx == self.current_page:
                    continue
                page = self.doc[page_idx]
                poly_pdf = self._relative_to_polygon(rel_poly, page)
                bbox_pdf = self._polygon_bbox(poly_pdf)

                page_hits = self._scan_roi_on_page(page_idx, bbox_pdf, poly_pdf)
                if page_hits:
                    all_hits.extend(page_hits)
                    continue

                score = self._match_roi_template(page_idx, bbox_pdf, poly_pdf, template)
                if score >= 0.84:
                    all_hits.append(
                        WatermarkInfo(
                            type_name="选区匹配图形",
                            page_index=page_idx,
                            content="基于选区模板匹配的重复图形",
                            rect=bbox_pdf,
                            polygon=poly_pdf,
                            is_text=False,
                            confidence=score,
                            source="roi-template",
                        )
                    )

        # 合并去重并直接替换列表，支持分步去水印
        dedup = {}
        for wm in all_hits:
            key = (
                wm.page_index,
                wm.type_name,
                wm.xref,
                wm.fingerprint,
                tuple(round(v, 1) for v in wm.rect) if wm.rect else None,
            )
            if key not in dedup or dedup[key].confidence < wm.confidence:
                dedup[key] = wm
        self.watermarks = list(dedup.values())
        self._update_watermark_list()
        self.status_var.set(f"选区分析完成，共识别 {len(self.watermarks)} 个候选")

    def _scan_roi_on_page(
        self,
        page_idx: int,
        bbox_pdf: fitz.Rect,
        poly_pdf: List[Tuple[float, float]],
    ) -> List[WatermarkInfo]:
        page = self.doc[page_idx]
        hits: List[WatermarkInfo] = []

        # 1. 选区内文本
        for word in page.get_text("words", clip=bbox_pdf):
            rect = fitz.Rect(word[:4])
            center = ((rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2)
            if self._point_in_polygon(center, poly_pdf):
                text = word[4].strip()
                if text:
                    hits.append(
                        WatermarkInfo(
                            type_name="选区文本",
                            page_index=page_idx,
                            content=text,
                            rect=rect,
                            polygon=poly_pdf,
                            is_text=True,
                            source="roi",
                        )
                    )

        # 2. 选区内图像对象
        for cand in self._collect_image_candidates(page, page_idx):
            if self._rect_intersects_polygon(cand.rect, poly_pdf):
                hits.append(
                    WatermarkInfo(
                        type_name="选区图像",
                        page_index=page_idx,
                        content=f"图片 {cand.width}x{cand.height}",
                        rect=cand.rect,
                        polygon=poly_pdf,
                        is_text=False,
                        xref=cand.xref,
                        container_xref=cand.container_xref,
                        fingerprint=cand.fingerprint,
                        source="roi",
                    )
                )

        # 3. 选区内矢量对象
        for cand in self._collect_vector_candidates(page, page_idx):
            if self._rect_intersects_polygon(cand.rect, poly_pdf):
                hits.append(
                    WatermarkInfo(
                        type_name="选区矢量",
                        page_index=page_idx,
                        content=f"opacity={cand.opacity:.2f}",
                        rect=cand.rect,
                        polygon=poly_pdf,
                        is_text=False,
                        fingerprint=cand.fingerprint,
                        source="roi",
                    )
                )

        # 合并文字，避免一页一个字
        if hits:
            merged_texts = []
            text_rects = []
            keep_hits = []
            for wm in hits:
                if wm.is_text:
                    merged_texts.append(wm.content)
                    if wm.rect:
                        text_rects.append(wm.rect)
                else:
                    keep_hits.append(wm)
            if merged_texts:
                rect = None
                for r in text_rects:
                    rect = r if rect is None else rect | r
                keep_hits.append(
                    WatermarkInfo(
                        type_name="选区文本",
                        page_index=page_idx,
                        content=" ".join(merged_texts),
                        rect=rect,
                        polygon=poly_pdf,
                        is_text=True,
                        source="roi",
                    )
                )
            hits = keep_hits
        return hits

    def _prepare_roi_template(self, page_idx: int, bbox_pdf: fitz.Rect, poly_pdf: List[Tuple[float, float]]):
        img = self._render_roi_gray(page_idx, bbox_pdf)
        if img is None:
            return None
        mask = self._make_polygon_mask(bbox_pdf, poly_pdf, img.shape[1], img.shape[0])
        blur = cv2.GaussianBlur(img, (15, 15), 0)
        return {"img": blur, "mask": mask}

    def _roi_visual_ink_score(self, page_idx: int, bbox_pdf: fitz.Rect, poly_pdf: List[Tuple[float, float]]) -> float:
        img = self._render_roi_gray(page_idx, bbox_pdf, out_w=240, out_h=160)
        if img is None:
            return 0.0
        mask = self._make_polygon_mask(bbox_pdf, poly_pdf, img.shape[1], img.shape[0])
        pixels = img[mask > 0]
        if pixels.size < 20:
            return 0.0
        dark_ratio = float(np.mean(pixels < 80))
        contrast = float(np.std(pixels) / 255.0)
        if dark_ratio < 0.002 and contrast < 0.025:
            return 0.0
        return min(1.0, dark_ratio * 8.0 + contrast)

    def _match_roi_template(self, page_idx: int, bbox_pdf: fitz.Rect, poly_pdf: List[Tuple[float, float]], template) -> float:
        if template is None:
            return 0.0
        img = self._render_roi_gray(page_idx, bbox_pdf, template["img"].shape[1], template["img"].shape[0])
        if img is None:
            return 0.0
        blur = cv2.GaussianBlur(img, (15, 15), 0)
        mask = self._make_polygon_mask(bbox_pdf, poly_pdf, blur.shape[1], blur.shape[0])
        common_mask = cv2.bitwise_and(mask, template["mask"])
        if np.count_nonzero(common_mask) < 20:
            return 0.0
        diff = np.abs(blur.astype(np.float32) - template["img"].astype(np.float32))
        score = 1.0 - float(np.mean(diff[common_mask > 0]) / 255.0)
        return max(0.0, min(1.0, score))

    def _render_roi_gray(self, page_idx: int, bbox_pdf: fitz.Rect, out_w: int = 180, out_h: int = 140):
        page = self.doc[page_idx]
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), clip=bbox_pdf, alpha=False)
            if pix.width <= 0 or pix.height <= 0:
                return None
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n >= 3:
                arr = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2GRAY)
            else:
                arr = arr[:, :, 0]
            arr = cv2.resize(arr, (out_w, out_h), interpolation=cv2.INTER_AREA)
            return arr
        except Exception:
            return None

    # ---------------- remove / preview / save ----------------
    def preview_selected_watermarks(self):
        if not self.doc or not self.watermarks:
            messagebox.showinfo("提示", "请先打开 PDF 并分析水印")
            return
        selected = [w for w in self.watermarks if w.selected]
        if not selected:
            messagebox.showinfo("提示", "请先选择要去除的水印")
            return

        input_path = self.pdf_path
        start_page = self.current_page
        self.status_var.set("正在生成去水印预览...")
        threading.Thread(
            target=lambda: self._preview_watermarks_thread(input_path, start_page, selected),
            daemon=True,
        ).start()

    # 向后兼容旧调用名
    remove_selected_watermarks = preview_selected_watermarks

    def _preview_watermarks_thread(self, input_path: str, start_page: int, selected: List[WatermarkInfo]):
        temp_file = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                temp_file = tmp.name
            self._apply_watermarks_to_pdf(input_path, temp_file, selected)
            self.preview_generation += 1
            self.preview_temp_files.append(temp_file)

            def show_preview():
                self.load_pdf(temp_file, as_original=False, start_page=start_page)
                self.status_var.set("预览已生成：可继续框选、分析，满意后再保存")
                auto_scan = messagebox.askyesno(
                    "预览已生成",
                    "已载入本次去水印预览。\n\n现在你可以继续框选、分析剩余水印。\n是否立即自动分析当前预览文件中的剩余候选水印？"
                )
                if auto_scan:
                    self.analyze_watermarks()

            self.master.after(0, show_preview)
        except Exception as e:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
            self.master.after(0, lambda: messagebox.showerror("错误", f"生成预览失败: {e}"))
            self.master.after(0, lambda: self.status_var.set("生成预览失败"))

    def save_current_result(self):
        if not self.doc or not self.pdf_path:
            messagebox.showinfo("提示", "请先打开 PDF 文件")
            return
        if not self.is_preview_session:
            messagebox.showinfo("提示", "请先点击“预览去除效果”，确认满意后再保存")
            return

        base_name = os.path.splitext(os.path.basename(self.original_pdf_path or self.pdf_path))[0]
        output_path = filedialog.asksaveasfilename(
            title="保存当前预览结果",
            defaultextension=".pdf",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")],
            initialfile=f"{base_name}_无水印.pdf",
        )
        if not output_path:
            return

        try:
            self.status_var.set("正在保存当前结果...")
            with fitz.open(self.pdf_path) as work_doc:
                work_doc.save(output_path, garbage=3, deflate=True)
            self.status_var.set(f"已保存：{os.path.basename(output_path)}")
            messagebox.showinfo("保存完成", f"当前预览结果已保存到：\n{output_path}")
        except Exception as e:
            messagebox.showerror("错误", f"保存结果失败: {e}")
            self.status_var.set("保存结果失败")

    def restore_original_pdf(self):
        if not self.original_pdf_path:
            messagebox.showinfo("提示", "当前没有可返回的原始文件")
            return
        if not self.is_preview_session:
            messagebox.showinfo("提示", "当前已经是原始文件")
            return
        keep_page = self.current_page
        self.load_pdf(self.original_pdf_path, as_original=True, start_page=keep_page)
        self.status_var.set("已返回原始文件")

    def _apply_watermarks_to_pdf(self, input_path: str, output_path: str, selected: List[WatermarkInfo]):
        work_doc = fitz.open(input_path)
        try:
            xrefs_to_kill = set()
            for w in selected:
                if not w.is_text:
                    if w.xref:
                        xrefs_to_kill.add(w.xref)
                    if w.container_xref:
                        xrefs_to_kill.add(w.container_xref)

            for page_idx in range(work_doc.page_count):
                page = work_doc[page_idx]
                page_items = [w for w in selected if w.page_index == page_idx]
                if not page_items:
                    continue

                if any(w.source == "rendered-diagonal" for w in page_items):
                    self._remove_rendered_diagonal_pattern(work_doc, page)

                # 1) 斜向浅色矢量/渲染水印：从内容流中删除浅色或半透明的填充路径，
                #    而不是整块白色矩形，从而避免覆盖正文。
                soft_rects = []
                for wm in page_items:
                    if wm.source in ("soft-vector", "rendered-diagonal"):
                        if wm.rect:
                            soft_rects.append(wm.rect)
                        for rect in (wm.redaction_rects or []):
                            soft_rects.append(rect)
                if soft_rects:
                    removed_paths = self._remove_soft_vector_paths(work_doc, page, soft_rects)
                    if removed_paths == 0:
                        self._redact_pale_regions(work_doc, page, soft_rects)

                # 2) 图片水印按页面删除引用，避免清空图片流后留下黑块。
                for wm in page_items:
                    if not wm.is_text and wm.source not in ("soft-vector", "rendered-diagonal"):
                        if wm.xref:
                            try:
                                page.delete_image(wm.xref)
                                continue
                            except Exception:
                                pass
                        if wm.container_xref:
                            self._remove_xobject_calls_from_page(work_doc, page, wm.container_xref)
                        elif wm.xref:
                            self._remove_xobject_calls_from_page(work_doc, page, wm.xref)

                # 3) 文本 / ROI模板 / 其他矢量用局部擦除
                redact_needed = False
                for wm in page_items:
                    if wm.source in ("soft-vector", "rendered-diagonal"):
                        continue
                    if wm.is_text:
                        rects = wm.redaction_rects or ([wm.rect] if wm.rect else [])
                        for rect in rects:
                            if rect:
                                page.add_redact_annot(rect, fill=(1, 1, 1))
                                redact_needed = True
                    elif (not wm.xref and not wm.container_xref):
                        if wm.polygon and len(wm.polygon) >= 3:
                            shape = page.new_shape()
                            shape.draw_polyline([fitz.Point(x, y) for x, y in wm.polygon] + [fitz.Point(*wm.polygon[0])])
                            shape.finish(color=(1, 1, 1), fill=(1, 1, 1), width=0.5)
                            shape.commit(overlay=True)
                        elif wm.rect:
                            page.add_redact_annot(wm.rect, fill=(1, 1, 1))
                            redact_needed = True

                if redact_needed:
                    try:
                        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
                    except Exception:
                        pass

            work_doc.save(output_path, garbage=3, deflate=True)
        finally:
            work_doc.close()

    def _remove_soft_vector_paths(self, doc, page, rects) -> int:
        """从内容流中删除浅色/半透明矢量路径，返回删除的路径段数量。"""
        removed = 0
        for content_xref in page.get_contents():
            removed += _filter_watermark_paths(doc, page, content_xref, rects)
        return removed

    def _redact_pale_regions(self, doc, page, rects):
        """兜底方案：只对选区内浅色水印像素做小方格擦除，避免整块白矩形覆盖正文。"""
        if not rects:
            return
        processed = set()
        for rect in rects:
            if not rect or rect.is_empty or rect.width < 2 or rect.height < 2:
                continue
            key = (round(rect.x0, 1), round(rect.y0, 1), round(rect.x1, 1), round(rect.y1, 1))
            if key in processed:
                continue
            processed.add(key)
            self._redact_pale_region(page, rect)

    def _redact_pale_region(self, page, rect):
        scale = 2.0
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
        except Exception:
            return
        if pix.width <= 0 or pix.height <= 0:
            return
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        gray = arr[:, :, :3].mean(axis=2) if pix.n >= 3 else arr[:, :, 0]
        h, w = gray.shape
        cell = 12
        redacts = []
        for cy in range(0, h, cell):
            for cx in range(0, w, cell):
                block = gray[cy:cy + cell, cx:cx + cell]
                if block.size < 4:
                    continue
                if int((block < 150).sum()) > 0:
                    continue
                if float((block >= 200).mean()) < 0.55:
                    continue
                redacts.append(
                    fitz.Rect(
                        rect.x0 + cx / scale,
                        rect.y0 + cy / scale,
                        rect.x0 + min(w, cx + cell) / scale,
                        rect.y0 + min(h, cy + cell) / scale,
                    )
                )
                if len(redacts) >= 3000:
                    break
            if len(redacts) >= 3000:
                break
        if not redacts:
            return
        for r in redacts:
            page.add_redact_annot(r, fill=(1, 1, 1))
        try:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
        except Exception:
            pass

    @staticmethod
    def _remove_rendered_diagonal_pattern(doc, page):
        contents = page.get_contents()
        if not contents:
            return

        for content_xref in contents:
            try:
                raw = doc.xref_stream(content_xref)
                text = raw.decode("latin1", "ignore")
                first_text = text.find("\nBT")
                if first_text < 0:
                    continue
                prefix = text[:first_text]
                if "/Pattern" not in prefix or "scn" not in prefix or "f*" not in prefix:
                    continue
                doc.update_stream(content_xref, text[first_text + 1:].encode("latin1"))
            except Exception:
                continue

    def _remove_xobject_calls_from_page(self, doc, page, target_xref: int):
        name_map = {xref: name for xref, name, *_ in page.get_xobjects()}
        name = name_map.get(target_xref)
        if not name:
            return
        for content_xref in page.get_contents():
            try:
                raw = doc.xref_stream(content_xref)
                src = raw.decode("latin1", "ignore")
                updated = re.sub(rf"q.*?/{re.escape(name)}\s+Do\s+Q", " ", src, flags=re.DOTALL)
                updated = re.sub(rf"/{re.escape(name)}\s+Do", " ", updated)
                if updated != src:
                    doc.update_stream(content_xref, updated.encode("latin1", "ignore"))
            except Exception:
                continue

    # ---------------- list / selection ----------------
    def _update_watermark_list(self):
        self.text_listbox.delete(0, END)
        self.graphic_listbox.delete(0, END)
        self.text_items = []
        self.graphic_items = []

        for wm in sorted(self.watermarks, key=lambda x: (x.page_index, x.type_name, x.content)):
            if wm.is_text:
                self.text_items.append(wm)
                self.text_listbox.insert(END, wm.display_text())
            else:
                self.graphic_items.append(wm)
                self.graphic_listbox.insert(END, wm.display_text())

        for idx, wm in enumerate(self.text_items):
            if wm.selected:
                self.text_listbox.selection_set(idx)
        for idx, wm in enumerate(self.graphic_items):
            if wm.selected:
                self.graphic_listbox.selection_set(idx)

        self.status_var.set(f"检测到 {len(self.text_items)} 个文本候选，{len(self.graphic_items)} 个图形候选")
        if self.batch_running and self.batch_index < len(self.batch_paths):
            path = self.batch_paths[self.batch_index]
            self.batch_results[path] = {
                "watermarks": list(self.watermarks),
                "image_candidates": list(self.image_candidates),
                "vector_candidates": list(self.vector_candidates),
            }
            self.batch_index += 1
            self._refresh_batch_combo()
            self.master.after(0, self._process_next_batch_pdf)
        elif self.pdf_path in self.batch_results:
            self.batch_results[self.pdf_path] = {
                "watermarks": list(self.watermarks),
                "image_candidates": list(self.image_candidates),
                "vector_candidates": list(self.vector_candidates),
            }
            self._refresh_batch_combo()

    def on_watermark_select(self, is_text: bool):
        items = self.text_items if is_text else self.graphic_items
        selected_indices = set((self.text_listbox if is_text else self.graphic_listbox).curselection())
        for i, wm in enumerate(items):
            wm.selected = i in selected_indices

    def select_all(self, listbox: Listbox):
        listbox.selection_set(0, END)
        items = self.text_items if listbox is self.text_listbox else self.graphic_items
        for wm in items:
            wm.selected = True

    def clear_all(self, listbox: Listbox):
        listbox.selection_clear(0, END)
        items = self.text_items if listbox is self.text_listbox else self.graphic_items
        for wm in items:
            wm.selected = False

    def search_watermarks(self):
        term = self.search_var.get().strip().lower()
        if not term:
            return
        self.text_listbox.selection_clear(0, END)
        self.graphic_listbox.selection_clear(0, END)

        text_hits = 0
        for i, wm in enumerate(self.text_items):
            if term in wm.display_text().lower():
                self.text_listbox.selection_set(i)
                text_hits += 1
        graphic_hits = 0
        for i, wm in enumerate(self.graphic_items):
            if term in wm.display_text().lower():
                self.graphic_listbox.selection_set(i)
                graphic_hits += 1

        if text_hits:
            self.notebook.select(0)
        elif graphic_hits:
            self.notebook.select(1)
        self.status_var.set(f"搜索到 {text_hits + graphic_hits} 个结果")

    def add_custom_keyword(self):
        keyword = self.custom_keyword.get().strip()
        if not keyword:
            return
        if keyword.lower() in {k.lower() for k in WATERMARK_KEYWORDS}:
            messagebox.showinfo("提示", "该关键词已存在")
            return
        WATERMARK_KEYWORDS.append(keyword)
        self.status_var.set(f"已添加关键词: {keyword}")
        if self.doc:
            self.analyze_watermarks()

    # ---------------- geometry helpers ----------------
    def _point_on_page(self, x: float, y: float) -> bool:
        if not self.original_img:
            return False
        return (
            self.image_offset_x <= x <= self.image_offset_x + self.original_img.width and
            self.image_offset_y <= y <= self.image_offset_y + self.original_img.height
        )

    def _canvas_to_pdf(self, x: float, y: float) -> Tuple[float, float]:
        return (
            (x - self.image_offset_x) * self.scale_factor_x,
            (y - self.image_offset_y) * self.scale_factor_y,
        )

    def _pdf_to_canvas(self, x: float, y: float) -> Tuple[float, float]:
        return (
            x / self.scale_factor_x + self.image_offset_x,
            y / self.scale_factor_y + self.image_offset_y,
        )

    @staticmethod
    def _distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    @staticmethod
    def _perpendicular_distance(p, a, b) -> float:
        ax, ay = a
        bx, by = b
        px, py = p
        denom = math.hypot(bx - ax, by - ay)
        if denom == 0:
            return 0.0
        return abs((by - ay) * px - (bx - ax) * py + bx * ay - by * ax) / denom

    @staticmethod
    def _build_rotated_rect(a, b, half_width: float) -> List[Tuple[float, float]]:
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length == 0:
            return [a, b, b, a]
        nx, ny = -dy / length, dx / length
        return [
            (ax + nx * half_width, ay + ny * half_width),
            (bx + nx * half_width, by + ny * half_width),
            (bx - nx * half_width, by - ny * half_width),
            (ax - nx * half_width, ay - ny * half_width),
        ]

    @staticmethod
    def _flatten(points: Iterable[Tuple[float, float]]) -> List[float]:
        flat = []
        for x, y in points:
            flat.extend([x, y])
        return flat

    @staticmethod
    def _polygon_bbox(poly: List[Tuple[float, float]]) -> fitz.Rect:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        return fitz.Rect(min(xs), min(ys), max(xs), max(ys))

    @staticmethod
    def _point_in_polygon(point: Tuple[float, float], polygon: List[Tuple[float, float]]) -> bool:
        x, y = point
        inside = False
        n = len(polygon)
        for i in range(n):
            x1, y1 = polygon[i]
            x2, y2 = polygon[(i + 1) % n]
            if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-9) + x1):
                inside = not inside
        return inside

    def _rect_intersects_polygon(self, rect: fitz.Rect, polygon: List[Tuple[float, float]]) -> bool:
        corners = [(rect.x0, rect.y0), (rect.x1, rect.y0), (rect.x1, rect.y1), (rect.x0, rect.y1)]
        if any(self._point_in_polygon(c, polygon) for c in corners):
            return True
        poly_bbox = self._polygon_bbox(polygon)
        return rect.intersects(poly_bbox)

    @staticmethod
    def _polygon_to_relative(poly_pdf: List[Tuple[float, float]], page) -> List[Tuple[float, float]]:
        return [(x / page.rect.width, y / page.rect.height) for x, y in poly_pdf]

    @staticmethod
    def _relative_to_polygon(rel_poly: List[Tuple[float, float]], page) -> List[Tuple[float, float]]:
        return [(x * page.rect.width, y * page.rect.height) for x, y in rel_poly]

    @staticmethod
    def _make_polygon_mask(bbox_pdf: fitz.Rect, poly_pdf: List[Tuple[float, float]], width: int, height: int):
        pts = []
        for x, y in poly_pdf:
            px = int(round((x - bbox_pdf.x0) / max(1e-6, bbox_pdf.width) * (width - 1)))
            py = int(round((y - bbox_pdf.y0) / max(1e-6, bbox_pdf.height) * (height - 1)))
            pts.append([px, py])
        mask = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(mask, [np.array(pts, dtype=np.int32)], 255)
        return mask


def create_root():
    if HAS_TKDND:
        return TkinterDnD.Tk()
    return Tk()


if __name__ == "__main__":
    try:
        root = create_root()
        app = PDFWatermarkRemover(root)
        root.mainloop()
    except ImportError as e:
        module = str(e).split("'")[-2]
        print(f"错误：缺少依赖模块 {module}")
        print("请安装：pip install PyMuPDF pillow opencv-python tkinterdnd2")
        input("按回车退出...")
    except Exception as e:
        print(f"发生错误：{e}")
        input("按回车退出...")
