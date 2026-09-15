"""Persistent language and appearance preferences for the Tkinter UI."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tkinter import Button, Canvas, Entry, Frame, Label, Listbox, StringVar, messagebox
from tkinter import ttk

from .version import APP_DISPLAY_NAME, APP_VERSION


TEXT = {
    "zh": {
        "select_pdf": "选择 PDF",
        "select_folder": "选择文件夹",
        "analyze": "分析水印",
        "preview": "预览去除效果",
        "save_direct": "直接保存",
        "save_preview": "预览后保存",
        "restore": "返回原文件",
        "appearance": "外观：",
        "language": "语言：",
        "theme_system": "跟随系统",
        "theme_light": "明亮",
        "theme_dark": "黑暗",
        "language_zh": "中文",
        "language_en": "English",
        "batch_files": "批量文件：",
        "roi_mode": "选区模式：",
        "rectangle": "矩形",
        "rotated": "斜框",
        "analyze_roi": "分析所选区域",
        "clear_roi": "清除选区",
        "roi_help": "斜框模式：先拖出方向线，再移动鼠标确定宽度并单击确认；右键清空",
        "previous": "上一页",
        "next": "下一页",
        "search_label": "搜索水印：",
        "search": "搜索",
        "text_watermarks": "文本水印",
        "graphic_watermarks": "图形水印",
        "custom_keyword": "添加自定义水印关键词：",
        "add": "添加",
        "select_all": "全选",
        "clear_all": "取消全选",
    },
    "en": {
        "select_pdf": "Open PDF",
        "select_folder": "Open Folder",
        "analyze": "Analyze",
        "preview": "Preview Removal",
        "save_direct": "Save Directly",
        "save_preview": "Save Preview Result",
        "restore": "Restore Original",
        "appearance": "Appearance:",
        "language": "Language:",
        "theme_system": "System",
        "theme_light": "Light",
        "theme_dark": "Dark",
        "language_zh": "中文",
        "language_en": "English",
        "batch_files": "Batch files:",
        "roi_mode": "Selection mode:",
        "rectangle": "Rectangle",
        "rotated": "Rotated",
        "analyze_roi": "Analyze Selection",
        "clear_roi": "Clear Selection",
        "roi_help": "Rotated mode: drag a direction line, move to set width, click to confirm; right-click to clear",
        "previous": "Previous",
        "next": "Next",
        "search_label": "Search watermarks:",
        "search": "Search",
        "text_watermarks": "Text Watermarks",
        "graphic_watermarks": "Graphic Watermarks",
        "custom_keyword": "Add custom watermark keyword:",
        "add": "Add",
        "select_all": "Select All",
        "clear_all": "Clear All",
    },
}


ENGLISH_REPLACEMENTS = (
    ("当前模式：未打开文件", "Mode: no file open"),
    ("当前模式：预览结果", "Mode: preview"),
    ("当前模式：原始文件", "Mode: original"),
    ("可继续框选/分析后再保存", "continue selecting/analyzing before saving"),
    ("欢迎使用 PDF 水印去除工具", "Welcome to PDF Watermarker"),
    ("支持拖入 PDF 文件", "Drag PDF files here"),
    ("拖拽支持需要安装 tkinterdnd2：pip install tkinterdnd2", "Drag and drop requires tkinterdnd2: pip install tkinterdnd2"),
    ("请先打开 PDF 并分析水印", "Open and analyze a PDF first"),
    ("请先选择要去除的水印", "Select watermarks to remove first"),
    ("请先打开 PDF 文件", "Open a PDF first"),
    ("请先框选区域", "Select an area first"),
    ("正在分析水印...", "Analyzing watermarks..."),
    ("分析水印失败", "Watermark analysis failed"),
    ("正在生成去水印预览...", "Generating removal preview..."),
    ("生成预览失败", "Preview generation failed"),
    ("正在保存当前结果...", "Saving result..."),
    ("保存结果失败", "Failed to save result"),
    ("正在直接保存 PDF...", "Saving PDF directly..."),
    ("直接保存失败", "Direct save failed"),
    ("直接保存 PDF", "Save PDF Directly"),
    ("PDF 已直接保存到：", "The PDF was saved directly to:"),
    ("已移除 PDF/A 只读声明和 PDF 保护，并设置为连续滚动布局。", "The PDF/A read-only declaration and PDF protection were removed, and continuous scrolling was enabled."),
    ("已返回原始文件", "Original file restored"),
    ("已清除选区", "Selection cleared"),
    ("矩形选区已创建", "Rectangle selection created"),
    ("斜框选区已创建", "Rotated selection created"),
    ("斜框宽度太小，请重新框选", "Rotated selection is too narrow; try again"),
    ("已确定斜框方向，请移动鼠标设置宽度，然后单击确认", "Direction set; move to choose width, then click to confirm"),
    ("选区中未发现文本/图形候选", "No text or graphic candidates found in the selection"),
    ("选区中未发现明显水印", "No obvious watermark found in the selection"),
    ("请选择 PDF 文件", "Select a PDF file"),
    ("拖入的内容中没有 PDF 文件", "The dropped items contain no PDF files"),
    ("所选文件夹及其子文件夹中没有 PDF 文件", "No PDF files were found in the selected folder or its subfolders"),
    ("选择一个或多个 PDF 文件", "Select one or more PDF files"),
    ("选择包含 PDF 的文件夹", "Select a folder containing PDFs"),
    ("PDF 文件", "PDF files"),
    ("所有文件", "All files"),
    ("请等待当前批次识别完成后再导入新文件", "Wait for the current batch to finish before importing more files"),
    ("打开 PDF 失败", "Failed to open PDF"),
    ("分析水印时出错", "Error analyzing watermarks"),
    ("PDF 已加密，需要提供用户密码或所有者密码", "This PDF is encrypted and requires a user or owner password"),
    ("提供的 PDF 用户密码或所有者密码不正确，请重试", "The PDF user or owner password is incorrect; try again"),
    ("文件已加密，请输入合法持有的用户密码或所有者密码：", "This file is encrypted. Enter its valid user or owner password:"),
    ("输入 PDF 含数字签名。解密和后续去水印会重写文件，原签名通常不再有效。", "The input PDF is digitally signed. Decryption and watermark removal rewrite it and normally invalidate the signature."),
    ("当前没有可返回的原始文件", "There is no original file to restore"),
    ("当前已经是原始文件", "The original file is already open"),
    ("该关键词已存在", "That keyword already exists"),
    ("密码错误", "Incorrect Password"),
    ("PDF 需要密码", "PDF Password Required"),
    ("数字签名提示", "Digital Signature Warning"),
    ("文件类型错误", "Invalid File Type"),
    ("未找到 PDF", "No PDFs Found"),
    ("批量识别进行中", "Batch Analysis in Progress"),
    ("批量识别完成", "Batch Analysis Complete"),
    ("PDF 解密失败", "PDF Decryption Failed"),
    ("预览已生成", "Preview Ready"),
    ("预览已生成：可继续框选、分析，满意后再保存", "Preview ready; continue selecting or analyzing before saving"),
    ("已载入本次去水印预览。", "The watermark-removal preview is open."),
    ("现在你可以继续框选、分析剩余水印。", "You can continue selecting and analyzing remaining watermarks."),
    ("是否立即自动分析当前预览文件中的剩余候选水印？", "Analyze remaining candidates in the preview now?"),
    ("保存完成", "Save Complete"),
    ("保存当前预览结果", "Save Current Preview"),
    ("当前预览结果已保存到：", "The preview result was saved to:"),
    ("已保存：", "Saved:"),
    ("已确认密码和 PDF 保护均已清除。", "Password and PDF protection removal was verified."),
    ("源文件包含大量缺少 ToUnicode 的子集字体，显示正常但复制文本会乱码。", "The source contains many subset fonts without ToUnicode mappings; display is normal, but copied text may be garbled."),
    ("去水印和压缩不会改变该编码；需要安装 Tesseract 中文语言包后进行 OCR 才能重建可复制文本。", "Watermark removal and compression do not change that encoding; install Tesseract Chinese data and use OCR to rebuild copyable text."),
    ("已通过 OCR 重建中文可搜索、可复制文本层。", "OCR rebuilt a searchable and copyable Chinese text layer."),
    ("如需去除水印，请先点击“预览去除效果”；仅解除只读限制请使用“直接保存”", "To remove watermarks, preview the result first; to remove only read-only restrictions, use Save Directly"),
    ("选区分析", "Selection Analysis"),
    ("选区分析完成，共识别", "Selection analysis complete; detected"),
    ("当前页选区内已发现候选水印。", "Watermark candidates were found in the current selection."),
    ("是否按相同相对位置扫描全部页面的重复图形/图像？", "Scan every page for repeated graphics/images at the same relative position?"),
    ("正在扫描页面", "Scanning pages"),
    ("无法打开文件", "Unable to open file"),
    ("批量识别", "Batch analysis"),
    ("个文件，共", " files; total"),
    ("识别中", "analyzing"),
    ("等待中", "waiting"),
    ("失败", "failed"),
    ("正在检查 PDF 加密状态", "Checking PDF encryption"),
    ("正在授权解密", "Decrypting with authorization"),
    ("已授权解密", "Decrypted with authorization"),
    ("已解密并打开", "Decrypted and opened"),
    ("已打开", "Opened"),
    ("已取消打开加密 PDF", "Opening encrypted PDF was cancelled"),
    ("文本编码异常，复制可能乱码", "text encoding issue; copied text may be garbled"),
    ("发现", "Found"),
    ("个字体缺少 ToUnicode 映射", " fonts without ToUnicode mappings"),
    ("正在 OCR 修复复制文本", "Rebuilding searchable text with OCR"),
    ("修复复制乱码", "Repair Garbled Copied Text"),
    ("这是源 PDF 的字体编码问题。", "This is caused by the source PDF's font encoding."),
    ("选择“是”：OCR 重建中文可复制文本层（页面会转为保真图像，文件可能变大）。", "Choose Yes to rebuild searchable text with OCR (pages become high-fidelity images and the file may grow)."),
    ("选择“否”：保留矢量正文并深度瘦身，但原有复制乱码无法修复。", "Choose No to preserve vector content and compact the file without repairing copied text."),
    ("检测到", "Detected"),
    ("个文本候选", " text candidates"),
    ("个图形候选", " graphic candidates"),
    ("个候选", " candidates"),
    ("搜索到", "Found"),
    ("个结果", " results"),
    ("已添加关键词", "Keyword added"),
    ("关键词文本", "Keyword text"),
    ("旋转文本", "Rotated text"),
    ("边缘文本", "Edge text"),
    ("底部二维码说明文字", "Footer QR instruction"),
    ("PDF 内置水印对象", "PDF watermark artifact"),
    ("PDF 内置图片水印", "Embedded image watermark"),
    ("PDF 内置图形水印", "Embedded graphic watermark"),
    ("斜向浅灰水印", "Pale diagonal watermark"),
    ("背景图水印", "Background image watermark"),
    ("底部二维码水印", "Footer QR watermark"),
    ("重复图片水印", "Repeated image watermark"),
    ("重复矢量水印", "Repeated vector watermark"),
    ("斜向浅色矢量水印", "Pale diagonal vector watermark"),
    ("选区视觉图形", "Selection visual graphic"),
    ("选区匹配图形", "Selection-matched graphic"),
    ("选区文本", "Selection text"),
    ("选区图像", "Selection image"),
    ("选区矢量", "Selection vector"),
    ("页面内容流明确标记为 Watermark", "Page content stream explicitly marked as Watermark"),
    ("带 Private/Watermark 标记", "has a Private/Watermark marker"),
    ("整页或大面积重复背景图可直接删除图像引用", "Full-page or large repeated background image whose reference can be removed"),
    ("每页底部二维码", "Footer QR code on every page"),
    ("基于渲染像素的选区图形", "Selection graphic detected from rendered pixels"),
    ("基于选区模板匹配的重复图形", "Repeated graphic matched from the selection template"),
    ("输出 PDF 仍带有密码或保护，已拒绝保存", "The output PDF is still password-protected; saving was refused"),
    ("图片", "Image"),
    ("页面", "Page"),
    ("重复", "repeated"),
    ("相似度", "similarity"),
    ("无内容描述", "no description"),
    ("提示", "Notice"),
    ("错误", "Error"),
)


PALETTES = {
    "light": {"bg": "#f0f0f0", "surface": "#ffffff", "text": "#202124", "muted": "#555555", "canvas": "#ffffff", "field": "#ffffff"},
    "dark": {"bg": "#1e1f22", "surface": "#2b2d31", "text": "#f2f3f5", "muted": "#b5bac1", "canvas": "#35373c", "field": "#383a40"},
}


class TranslatedStringVar(StringVar):
    def __init__(self, owner, value=""):
        self.owner = owner
        self.source_value = value
        super().__init__(master=owner.master, value=owner.translate_runtime(value))

    def set(self, value):
        self.source_value = str(value)
        super().set(self.owner.translate_runtime(self.source_value))

    def refresh(self):
        super().set(self.owner.translate_runtime(self.source_value))


class PreferencesMixin:
    def _init_preferences(self):
        self._localized_widgets = []
        self._localized_tabs = []
        settings = self._load_preferences()
        self.language_code = settings.get("language", "zh")
        self.theme_mode = settings.get("theme", "system")
        if self.language_code not in TEXT:
            self.language_code = "zh"
        if self.theme_mode not in ("system", "light", "dark"):
            self.theme_mode = "system"
        self.language_display_var = StringVar(master=self.master)
        self.theme_display_var = StringVar(master=self.master)
        self._set_window_title()

    @staticmethod
    def _preferences_path() -> Path:
        if sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support"
        elif os.name == "nt":
            base = Path(os.environ.get("APPDATA", Path.home()))
        else:
            base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        return base / "PDFWaterMarker" / "settings.json"

    def _load_preferences(self):
        try:
            return json.loads(self._preferences_path().read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    def _save_preferences(self):
        try:
            path = self._preferences_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"language": self.language_code, "theme": self.theme_mode}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def tr(self, key):
        return TEXT[self.language_code].get(key, TEXT["zh"].get(key, key))

    def translate_runtime(self, value):
        text = str(value)
        if self.language_code != "en":
            return text
        # Prefer complete phrases over generic words such as “提示” and “错误”.
        for source, target in sorted(ENGLISH_REPLACEMENTS, key=lambda pair: len(pair[0]), reverse=True):
            text = text.replace(source, target)
        return text

    def localized(self, widget, key):
        self._localized_widgets.append((widget, key))
        widget.configure(text=self.tr(key))
        return widget

    def localized_tab(self, notebook, tab_id, key):
        self._localized_tabs.append((notebook, tab_id, key))
        notebook.tab(tab_id, text=self.tr(key))

    def translated_var(self, value=""):
        return TranslatedStringVar(self, value)

    def _refresh_preference_controls(self):
        language_values = [self.tr("language_zh"), self.tr("language_en")]
        theme_values = [self.tr("theme_system"), self.tr("theme_light"), self.tr("theme_dark")]
        self.language_combo.configure(values=language_values)
        self.theme_combo.configure(values=theme_values)
        self.language_display_var.set(language_values[0 if self.language_code == "zh" else 1])
        self.theme_display_var.set(theme_values[("system", "light", "dark").index(self.theme_mode)])

    def _on_language_changed(self, _event=None):
        selected = self.language_display_var.get()
        self.language_code = "en" if selected == self.tr("language_en") else "zh"
        self._set_window_title()
        for widget, key in self._localized_widgets:
            widget.configure(text=self.tr(key))
        for notebook, tab_id, key in self._localized_tabs:
            notebook.tab(tab_id, text=self.tr(key))
        for variable in (self.mode_var, self.status_var, self.drop_hint_var):
            variable.refresh()
        self._refresh_preference_controls()
        if self.watermarks:
            self._update_watermark_list()
        self._save_preferences()

    def _set_window_title(self):
        self.master.title(APP_DISPLAY_NAME if self.language_code == "zh" else f"PDF Watermarker Mark{APP_VERSION}")

    def _on_theme_changed(self, _event=None):
        selected = self.theme_display_var.get()
        values = [self.tr("theme_system"), self.tr("theme_light"), self.tr("theme_dark")]
        self.theme_mode = ("system", "light", "dark")[values.index(selected)] if selected in values else "system"
        self.apply_theme()
        self._save_preferences()

    def _system_theme(self):
        if sys.platform == "darwin":
            result = subprocess.run(
                ["defaults", "read", "-g", "AppleInterfaceStyle"],
                capture_output=True,
                text=True,
                check=False,
            )
            return "dark" if "dark" in result.stdout.casefold() else "light"
        if os.name == "nt":
            try:
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return "light" if value else "dark"
            except OSError:
                return "light"
        return "dark" if os.environ.get("GTK_THEME", "").casefold().endswith(":dark") else "light"

    def apply_theme(self):
        active = self._system_theme() if self.theme_mode == "system" else self.theme_mode
        palette = PALETTES[active]
        self.master.configure(bg=palette["bg"])
        style = ttk.Style(self.master)
        style.configure("TFrame", background=palette["bg"])
        style.configure("TLabel", background=palette["bg"], foreground=palette["text"])
        style.configure("TRadiobutton", background=palette["bg"], foreground=palette["text"])
        style.configure("TNotebook", background=palette["bg"], borderwidth=0)
        style.configure("TNotebook.Tab", background=palette["surface"], foreground=palette["text"])
        style.configure("TCombobox", fieldbackground=palette["field"], foreground=palette["text"])

        def visit(widget):
            try:
                if isinstance(widget, Frame):
                    widget.configure(bg=palette["bg"])
                elif isinstance(widget, Label):
                    widget.configure(bg=palette["bg"], fg=palette["text"])
                elif isinstance(widget, Entry):
                    widget.configure(bg=palette["field"], fg=palette["text"], insertbackground=palette["text"])
                elif isinstance(widget, Listbox):
                    widget.configure(bg=palette["field"], fg=palette["text"], selectbackground="#0a84ff")
                elif isinstance(widget, Canvas):
                    widget.configure(bg=palette["canvas"])
                elif isinstance(widget, Button):
                    button_text = "#111111" if sys.platform == "darwin" else palette["text"]
                    if widget.cget("bg") not in ("#007bff", "#28a745", "#6c757d"):
                        widget.configure(bg=palette["surface"], fg=button_text, activebackground=palette["field"])
                    elif sys.platform == "darwin":
                        widget.configure(fg=button_text)
            except Exception:
                pass
            for child in widget.winfo_children():
                visit(child)

        visit(self.master)

    def showinfo(self, title, message):
        return messagebox.showinfo(self.translate_runtime(title), self.translate_runtime(message))

    def showerror(self, title, message):
        return messagebox.showerror(self.translate_runtime(title), self.translate_runtime(message))

    def showwarning(self, title, message):
        return messagebox.showwarning(self.translate_runtime(title), self.translate_runtime(message))

    def askyesno(self, title, message):
        return messagebox.askyesno(self.translate_runtime(title), self.translate_runtime(message))
