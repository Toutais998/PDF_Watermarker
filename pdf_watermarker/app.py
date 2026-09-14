"""Tkinter application shell for PDF Watermarker."""

import os
import tempfile
import threading
from typing import List

import pymupdf as fitz
from PIL import Image, ImageTk
from tkinter import (
    BOTH, END, LEFT, RIGHT, TOP, X, Y, Button, Canvas, Entry, Frame,
    Label, Listbox, StringVar, filedialog, simpledialog,
)
from tkinter import ttk

from pdf_decryptor import (
    IncorrectPasswordError, PasswordRequiredError, PdfDecryptionError,
    decrypt_pdf, is_pdf_encrypted,
)
from .constants import (
    BUTTON_BG, BUTTON_FG, FRAME_BG, MAIN_BG, ROI_FILL, ROI_OUTLINE,
    WATERMARK_KEYWORDS,
)
from .detection import DetectionMixin
from .geometry import GeometryMixin
from .models import ImageCandidate, VectorCandidate, WatermarkInfo
from .processing import ProcessingMixin
from .ui_preferences import PreferencesMixin
from .version import APP_DISPLAY_NAME

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_TKDND = True
except Exception:
    DND_FILES = None
    TkinterDnD = None
    HAS_TKDND = False



class PDFWatermarkRemover(PreferencesMixin, DetectionMixin, ProcessingMixin, GeometryMixin):
    def __init__(self, master):
        self.master = master
        self.master.title(APP_DISPLAY_NAME)
        self.master.geometry("1280x820")
        self.master.configure(bg=MAIN_BG)
        self._init_preferences()

        self.pdf_path = None
        self.original_pdf_path = None
        self.preview_pdf_path = None
        self.preview_temp_files: List[str] = []
        self.preview_generation = 0
        self.is_preview_session = False
        self.decrypted_pdf_cache: Dict[str, Tuple[str, object]] = {}
        self.pdf_passwords: Dict[str, str] = {}
        self.current_source_was_encrypted = False
        self.text_encoding_warning = ""
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
        self.explicit_watermark_xrefs = set()
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
        self.apply_theme()
        self._bind_drop_targets()
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- UI ----------------
    def _build_ui(self):
        top_frame = Frame(self.master, bg=MAIN_BG)
        top_frame.pack(side=TOP, fill=X, padx=10, pady=10)

        self.localized(Button(top_frame, bg=BUTTON_BG, fg=BUTTON_FG,
               command=self.open_pdf, width=12, height=2), "select_pdf").pack(side=LEFT, padx=5)
        self.localized(Button(top_frame, bg=BUTTON_BG, fg=BUTTON_FG,
               command=self.open_pdf_folder, width=12, height=2), "select_folder").pack(side=LEFT, padx=5)
        self.localized(Button(top_frame, bg=BUTTON_BG, fg=BUTTON_FG,
               command=self.analyze_watermarks, width=12, height=2), "analyze").pack(side=LEFT, padx=5)
        self.localized(Button(top_frame, bg=BUTTON_BG, fg=BUTTON_FG,
               command=self.preview_selected_watermarks, width=14, height=2), "preview").pack(side=LEFT, padx=5)
        self.localized(Button(top_frame, bg="#28a745", fg=BUTTON_FG,
               command=self.save_current_result, width=14, height=2), "save").pack(side=LEFT, padx=5)
        self.localized(Button(top_frame, bg="#6c757d", fg=BUTTON_FG,
               command=self.restore_original_pdf, width=12, height=2), "restore").pack(side=LEFT, padx=5)

        info_frame = Frame(self.master, bg=MAIN_BG)
        info_frame.pack(fill=X, padx=15, pady=(0, 4))
        self.mode_var = self.translated_var("当前模式：未打开文件")
        Label(info_frame, textvariable=self.mode_var, bg=MAIN_BG, fg="#555555").pack(side=LEFT)
        self.status_var = self.translated_var("欢迎使用 PDF 水印去除工具")
        Label(info_frame, textvariable=self.status_var, bg=MAIN_BG).pack(side=RIGHT)

        content_frame = Frame(self.master, bg=MAIN_BG)
        content_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)

        preview_frame = Frame(content_frame, bg=FRAME_BG)
        preview_frame.pack(side=LEFT, fill=BOTH, expand=True, padx=(0, 5))

        self.drop_hint_var = self.translated_var(
            value="支持拖入 PDF 文件" if HAS_TKDND else "拖拽支持需要安装 tkinterdnd2：pip install tkinterdnd2"
        )
        self.drop_hint = Label(preview_frame, textvariable=self.drop_hint_var, bg="#eef5ff", anchor="w")
        self.drop_hint.pack(fill=X, padx=10, pady=(10, 0))

        batch_frame = Frame(preview_frame, bg=FRAME_BG)
        batch_frame.pack(fill=X, padx=10, pady=(6, 0))
        self.localized(Label(batch_frame, bg=FRAME_BG), "batch_files").pack(side=LEFT)
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
        self.localized(Label(roi_frame, bg=FRAME_BG), "roi_mode").pack(side=LEFT)
        self.localized(ttk.Radiobutton(roi_frame, variable=self.roi_mode, value="axis"), "rectangle").pack(side=LEFT, padx=(5, 0))
        self.localized(ttk.Radiobutton(roi_frame, variable=self.roi_mode, value="rotated"), "rotated").pack(side=LEFT, padx=(5, 10))
        self.localized(Button(roi_frame, command=self.analyze_roi, width=14), "analyze_roi").pack(side=LEFT, padx=5)
        self.localized(Button(roi_frame, command=self.clear_roi, width=10), "clear_roi").pack(side=LEFT, padx=5)
        roi_help = self.localized(Label(
            preview_frame,
            bg=FRAME_BG,
            fg="#666666",
            anchor="w",
        ), "roi_help")
        roi_help.pack(fill=X, padx=15, pady=(0, 5))

        nav_frame = Frame(preview_frame, bg=FRAME_BG)
        nav_frame.pack(fill=X, padx=10, pady=(0, 10))
        self.localized(Button(nav_frame, command=self.prev_page, width=10), "previous").pack(side=LEFT, padx=5)
        self.page_var = StringVar(value="0/0")
        Label(nav_frame, textvariable=self.page_var, bg=FRAME_BG).pack(side=LEFT, padx=10)
        self.localized(Button(nav_frame, command=self.next_page, width=10), "next").pack(side=LEFT, padx=5)

        side_frame = Frame(content_frame, bg=FRAME_BG, width=430)
        side_frame.pack(side=RIGHT, fill=BOTH, padx=(5, 0))
        side_frame.pack_propagate(False)

        preference_frame = Frame(side_frame, bg=FRAME_BG)
        preference_frame.pack(fill=X, padx=10, pady=(10, 0))
        self.localized(Label(preference_frame, bg=FRAME_BG), "appearance").pack(side=LEFT)
        self.theme_combo = ttk.Combobox(preference_frame, textvariable=self.theme_display_var, state="readonly", width=10)
        self.theme_combo.pack(side=LEFT, padx=(4, 12))
        self.theme_combo.bind("<<ComboboxSelected>>", self._on_theme_changed)
        self.localized(Label(preference_frame, bg=FRAME_BG), "language").pack(side=LEFT)
        self.language_combo = ttk.Combobox(preference_frame, textvariable=self.language_display_var, state="readonly", width=10)
        self.language_combo.pack(side=LEFT, padx=(4, 0))
        self.language_combo.bind("<<ComboboxSelected>>", self._on_language_changed)
        self._refresh_preference_controls()

        search_frame = Frame(side_frame, bg=FRAME_BG)
        search_frame.pack(fill=X, padx=10, pady=10)
        self.localized(Label(search_frame, bg=FRAME_BG), "search_label").pack(side=LEFT)
        self.search_var = StringVar()
        Entry(search_frame, textvariable=self.search_var, width=28).pack(side=LEFT, fill=X, expand=True, padx=5)
        self.localized(Button(search_frame, command=self.search_watermarks), "search").pack(side=LEFT)

        self.notebook = ttk.Notebook(side_frame)
        self.notebook.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))

        self.text_listbox = self._create_watermark_tab("text_watermarks")
        self.graphic_listbox = self._create_watermark_tab("graphic_watermarks")

        self.text_listbox.bind("<<ListboxSelect>>", lambda e: self.on_watermark_select(True))
        self.graphic_listbox.bind("<<ListboxSelect>>", lambda e: self.on_watermark_select(False))

        custom_frame = Frame(side_frame, bg=FRAME_BG)
        custom_frame.pack(fill=X, padx=10, pady=(0, 10))
        self.localized(Label(custom_frame, bg=FRAME_BG), "custom_keyword").pack(anchor="w")
        self.custom_keyword = StringVar()
        Entry(custom_frame, textvariable=self.custom_keyword).pack(fill=X, pady=5)
        self.localized(Button(custom_frame, command=self.add_custom_keyword), "add").pack(anchor="e")

    def _create_watermark_tab(self, title_key: str) -> Listbox:
        frame = Frame(self.notebook, bg=FRAME_BG)
        self.notebook.add(frame, text=self.tr(title_key))
        self.localized_tab(self.notebook, frame, title_key)

        list_frame = Frame(frame, bg=FRAME_BG)
        list_frame.pack(fill=BOTH, expand=True, padx=5, pady=5)

        listbox = Listbox(list_frame, bg=FRAME_BG, selectmode="multiple")
        listbox.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        scrollbar.pack(side=RIGHT, fill=Y)
        listbox.config(yscrollcommand=scrollbar.set)

        btn_frame = Frame(frame, bg=FRAME_BG)
        btn_frame.pack(fill=X, padx=5, pady=5)
        self.localized(Button(btn_frame, command=lambda lb=listbox: self.select_all(lb), width=10), "select_all").pack(side=LEFT, padx=5)
        self.localized(Button(btn_frame, command=lambda lb=listbox: self.clear_all(lb), width=10), "clear_all").pack(side=LEFT, padx=5)
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
            self.translate_runtime("PDF 需要密码"),
            self.translate_runtime(f"文件已加密，请输入合法持有的用户密码或所有者密码：\n\n{os.path.basename(file_path)}"),
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
                    self.showerror("密码错误", "提供的 PDF 用户密码或所有者密码不正确，请重试")
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
                self.showwarning(
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
            self.showerror("文件类型错误", "拖入的内容中没有 PDF 文件")
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
            title=self.translate_runtime("选择一个或多个 PDF 文件"),
            filetypes=[(self.translate_runtime("PDF 文件"), "*.pdf"), (self.translate_runtime("所有文件"), "*.*")],
        )
        if file_paths:
            self.import_pdf_paths(list(file_paths))

    def open_pdf_folder(self):
        folder = filedialog.askdirectory(title=self.translate_runtime("选择包含 PDF 的文件夹"))
        if not folder:
            return
        pdf_files = []
        for root, _, names in os.walk(folder):
            pdf_files.extend(os.path.join(root, name) for name in names if name.lower().endswith(".pdf"))
        pdf_files.sort(key=str.lower)
        if not pdf_files:
            self.showinfo("未找到 PDF", "所选文件夹及其子文件夹中没有 PDF 文件")
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
            self.showinfo("批量识别进行中", "请等待当前批次识别完成后再导入新文件")
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
            self.showinfo("批量识别完成", self.status_var.get())
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
            self.showerror("文件类型错误", "请选择 PDF 文件")
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
            self.text_encoding_warning = self._text_encoding_diagnostic(self.doc)
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
            if self.text_encoding_warning:
                self.status_var.set(f"已打开: {os.path.basename(file_path)}（文本编码异常，复制可能乱码）")
            return True
        except InterruptedError:
            self.status_var.set("已取消打开加密 PDF")
            return False
        except PdfDecryptionError as e:
            self.showerror("PDF 解密失败", str(e))
            self.status_var.set("PDF 解密失败")
            return False
        except Exception as e:
            self.showerror("错误", f"打开 PDF 失败: {e}")
            return False

    @staticmethod
    def _text_encoding_diagnostic(doc) -> str:
        """Detect fonts that render correctly but cannot provide clipboard Unicode.

        A PDF can display glyphs from an embedded subset font while omitting a
        usable ToUnicode map.  There is no reliable inverse mapping to recover
        the author's characters without an external source/OCR, so report the
        condition instead of silently claiming the text was repaired.
        """
        suspicious = 0
        checked = set()
        try:
            for page in doc:
                for item in page.get_fonts(full=True):
                    xref = int(item[0] or 0)
                    if not xref or xref in checked:
                        continue
                    checked.add(xref)
                    subtype = str(item[2] or "")
                    if subtype not in ("Type0", "Type1", "TrueType", "CIDFontType0"):
                        continue
                    kind, value = doc.xref_get_key(xref, "ToUnicode")
                    base_name = str(item[3] or "").lower()
                    if kind == "null" or (kind != "xref" and not value):
                        if "fzbookmaker" in base_name or "identity" in str(item[5]).lower():
                            suspicious += 1
            return "" if not suspicious else f"发现 {suspicious} 个字体缺少 ToUnicode 映射"
        except Exception:
            return ""

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
            self.showinfo("提示", "请先打开 PDF 文件")
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
            explicit_marks = []
            image_candidates = []
            vector_candidates = []
            self.explicit_watermark_xrefs = self._scan_explicit_watermark_xrefs()
            for page_idx in range(self.total_pages):
                page = self.doc[page_idx]
                page_explicit = self._find_explicit_watermarks(page, page_idx)
                explicit_marks.extend(page_explicit)
                page_vectors = self._collect_vector_candidates(page, page_idx)
                vector_candidates.extend(page_vectors)
                page_text_marks = self._find_text_watermarks(page, page_idx)
                if any(w.source == "explicit-watermark-artifact" for w in page_explicit):
                    # 内置 Watermark Artifact 中的旋转文字已经由对象级候选覆盖；
                    # 再把它们作为文字候选会造成重复 redaction 和正文误伤。
                    page_text_marks = [w for w in page_text_marks if w.source != "rotated-text"]
                text_marks.extend(page_text_marks)
                page_image_candidates = self._collect_image_candidates(page, page_idx)
                image_candidates.extend(page_image_candidates)
                has_background_image = any(self._is_background_image_candidate(cand) for cand in page_image_candidates)
                # Background images skip rendered-diagonal pixel scanning.
                if not page_explicit and not has_background_image and not any(v.color_hint.startswith("soft-vector") for v in page_vectors):
                    text_marks.extend(self._find_rendered_diagonal_watermarks(page, page_idx))
                if page_idx % max(1, self.total_pages // 10) == 0:
                    pct = int(page_idx / max(1, self.total_pages) * 100)
                    self.master.after(0, lambda p=pct: self.status_var.set(f"正在扫描页面... {p}%"))

            graphic_marks = self._find_repeated_graphics(image_candidates, vector_candidates)
            text_marks = self._mark_repeated_texts(text_marks)

            self.image_candidates = image_candidates
            self.vector_candidates = vector_candidates
            self.watermarks = text_marks + explicit_marks + graphic_marks
            self.master.after(0, self._update_watermark_list)
        except Exception as e:
            error = str(e)
            self.master.after(0, lambda: self.showerror("错误", f"分析水印时出错: {error}"))
            self.master.after(0, lambda: self.status_var.set("分析水印失败"))
            if self.batch_running:
                path = self.batch_paths[self.batch_index]
                self.batch_results[path] = {"error": str(e), "watermarks": []}
                self.batch_index += 1
                self.master.after(0, self._process_next_batch_pdf)


    # ---------------- remove / preview / save ----------------
    def preview_selected_watermarks(self):
        if not self.doc or not self.watermarks:
            self.showinfo("提示", "请先打开 PDF 并分析水印")
            return
        selected = [w for w in self.watermarks if w.selected]
        if not selected:
            self.showinfo("提示", "请先选择要去除的水印")
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
                auto_scan = self.askyesno(
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
            error = str(e)
            self.master.after(0, lambda: self.showerror("错误", f"生成预览失败: {error}"))
            self.master.after(0, lambda: self.status_var.set("生成预览失败"))

    def save_current_result(self):
        if not self.doc or not self.pdf_path:
            self.showinfo("提示", "请先打开 PDF 文件")
            return
        if not self.is_preview_session:
            self.showinfo("提示", "请先点击“预览去除效果”，确认满意后再保存")
            return

        base_name = os.path.splitext(os.path.basename(self.original_pdf_path or self.pdf_path))[0]
        output_path = filedialog.asksaveasfilename(
            title=self.translate_runtime("保存当前预览结果"),
            defaultextension=".pdf",
            filetypes=[(self.translate_runtime("PDF 文件"), "*.pdf"), (self.translate_runtime("所有文件"), "*.*")],
            initialfile=f"{base_name}_{'watermark_removed' if self.language_code == 'en' else '无水印'}.pdf",
        )
        if not output_path:
            return

        try:
            self.status_var.set("正在保存当前结果...")
            use_ocr = False
            if self.text_encoding_warning:
                use_ocr = self.askyesno(
                    "修复复制乱码",
                    f"{self.text_encoding_warning}，这是源 PDF 的字体编码问题。\n\n"
                    "选择“是”：OCR 重建中文可复制文本层（页面会转为保真图像，文件可能变大）。\n"
                    "选择“否”：保留矢量正文并深度瘦身，但原有复制乱码无法修复。",
                )
            if use_ocr:
                self._save_ocr_searchable_pdf(self.pdf_path, output_path)
            else:
                with fitz.open(self.pdf_path) as work_doc:
                    self._save_unencrypted_pdf(work_doc, output_path)
            self.status_var.set(f"已保存：{os.path.basename(output_path)}")
            note = ""
            if self.text_encoding_warning and not use_ocr:
                note = (
                    "\n\n提示：源文件包含大量缺少 ToUnicode 的子集字体，显示正常但复制文本会乱码。"
                    "去水印和压缩不会改变该编码；需要安装 Tesseract 中文语言包后进行 OCR 才能重建可复制文本。"
                )
            elif use_ocr:
                note = "\n\n已通过 OCR 重建中文可搜索、可复制文本层。"
            self.showinfo("保存完成", f"当前预览结果已保存到：\n{output_path}\n\n已确认密码和 PDF 保护均已清除。{note}")
        except Exception as e:
            self.showerror("错误", f"保存结果失败: {e}")
            self.status_var.set("保存结果失败")

    def restore_original_pdf(self):
        if not self.original_pdf_path:
            self.showinfo("提示", "当前没有可返回的原始文件")
            return
        if not self.is_preview_session:
            self.showinfo("提示", "当前已经是原始文件")
            return
        keep_page = self.current_page
        self.load_pdf(self.original_pdf_path, as_original=True, start_page=keep_page)
        self.status_var.set("已返回原始文件")


    # ---------------- list / selection ----------------
    def _update_watermark_list(self):
        self.text_listbox.delete(0, END)
        self.graphic_listbox.delete(0, END)
        self.text_items = []
        self.graphic_items = []

        for wm in sorted(self.watermarks, key=lambda x: (x.page_index, x.type_name, x.content)):
            if wm.is_text:
                self.text_items.append(wm)
                self.text_listbox.insert(END, self.translate_runtime(wm.display_text()))
            else:
                self.graphic_items.append(wm)
                self.graphic_listbox.insert(END, self.translate_runtime(wm.display_text()))

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
            if term in self.translate_runtime(wm.display_text()).lower():
                self.text_listbox.selection_set(i)
                text_hits += 1
        graphic_hits = 0
        for i, wm in enumerate(self.graphic_items):
            if term in self.translate_runtime(wm.display_text()).lower():
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
            self.showinfo("提示", "该关键词已存在")
            return
        WATERMARK_KEYWORDS.append(keyword)
        self.status_var.set(f"已添加关键词: {keyword}")
        if self.doc:
            self.analyze_watermarks()

    # ---------------- geometry helpers ----------------


def create_root():
    if HAS_TKDND:
        return TkinterDnD.Tk()
    from tkinter import Tk
    return Tk()
