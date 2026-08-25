import os
import sys
import tempfile
from tkinter import Tk, filedialog, messagebox, Frame, Button, Label, Listbox, Checkbutton, Entry, Canvas
from tkinter import ttk, BooleanVar, StringVar, IntVar, TOP, BOTH, X, Y, LEFT, RIGHT, BOTTOM, END, HORIZONTAL, RAISED
import threading
import fitz  # PyMuPDF
from PIL import Image, ImageTk
from math import cos, pi, atan
import shutil
import io
import re

# 使用最新的PyPDF2导入方式
import PyPDF2
from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.generic import NameObject

# 设置界面样式和颜色
MAIN_BG = "#f0f0f0"
BUTTON_BG = "#007bff"
BUTTON_FG = "white"
FRAME_BG = "white"
HIGHLIGHT_BG = "#ffeb99"

# 常见水印关键词
WATERMARK_KEYWORDS = [
    "中国知网", "cnki", "www.cnki.net", 
    "学位论文", "版权所有", "学术期刊", 
    "请勿复制", "请勿传播", "版权保护",
    "confidential", "watermark", "draft",
    "机密", "草稿", "内部文件", "内部资料",
    "copyright", "all rights reserved",
    "初稿", "评审稿"
]


class WatermarkInfo:
    """水印信息类"""
    def __init__(self, type_name, index, page_index, content=None, rect=None, is_text=True):
        self.type_name = type_name  # 水印类型
        self.index = index  # 在页面内容中的索引
        self.page_index = page_index  # 页面索引
        self.content = content  # 水印内容
        self.rect = rect  # 水印位置
        self.selected = True  # 是否被选中移除
        self.is_text = is_text  # 是否为文本水印
        
    def __str__(self):
        if self.content and len(str(self.content)) > 30:
            display_content = str(self.content)[:30] + "..."
        else:
            display_content = self.content

        return f"页面 {self.page_index+1}: {self.type_name} 水印 - {display_content}"
        

class PDFWatermarkRemover:
    """PDF水印移除器"""
    def __init__(self, master):
        self.master = master
        master.title("PDF 水印去除工具")
        master.geometry("1200x700")
        master.configure(bg=MAIN_BG)
        
        # 创建界面组件
        self.setup_ui()
        
        # PDF相关变量
        self.pdf_path = None
        self.watermarks = []
        self.current_page = 0
        self.total_pages = 0
        self.doc = None
        self.reader = None
        
        # 框选区域相关变量
        self.roi_start_x = -1
        self.roi_start_y = -1
        self.roi_end_x = -1
        self.roi_end_y = -1
        self.roi_rectangle = None
        self.is_drawing = False
        self.scale_factor = 1.0
        
    def setup_ui(self):
        """设置UI界面"""
        # 顶部按钮区域
        top_frame = Frame(self.master, bg=MAIN_BG)
        top_frame.pack(side=TOP, fill=X, padx=10, pady=10)
        
        btn_open = Button(top_frame, text="选择PDF", bg=BUTTON_BG, fg=BUTTON_FG,
                         command=self.open_pdf, width=15, height=2)
        btn_open.pack(side=LEFT, padx=5)
        
        btn_analyze = Button(top_frame, text="分析水印", bg=BUTTON_BG, fg=BUTTON_FG,
                           command=self.analyze_watermarks, width=15, height=2)
        btn_analyze.pack(side=LEFT, padx=5)
        
        btn_remove = Button(top_frame, text="去除选中水印", bg=BUTTON_BG, fg=BUTTON_FG,
                          command=self.remove_selected_watermarks, width=15, height=2)
        btn_remove.pack(side=LEFT, padx=5)
        
        # 状态显示
        self.status_var = StringVar()
        self.status_var.set("欢迎使用PDF水印去除工具")
        status_label = Label(top_frame, textvariable=self.status_var, bg=MAIN_BG)
        status_label.pack(side=RIGHT, padx=10)
        
        # 主内容区域 - 左侧PDF预览
        content_frame = Frame(self.master, bg=MAIN_BG)
        content_frame.pack(fill=BOTH, expand=True, padx=10, pady=5)
        
        # 左侧PDF预览
        preview_frame = Frame(content_frame, bg=FRAME_BG)
        preview_frame.pack(side=LEFT, fill=BOTH, expand=True, padx=(0,5))
        
        # PDF预览Canvas - 使用Canvas代替Label以便更好地控制绘制
        self.pdf_canvas = Canvas(preview_frame, bg="white", highlightthickness=0)
        self.pdf_canvas.pack(fill=BOTH, expand=True, padx=10, pady=10)
        self.pdf_canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.pdf_canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.pdf_canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        
        # ROI区域按钮
        roi_frame = Frame(preview_frame, bg=FRAME_BG)
        roi_frame.pack(fill=X, padx=10, pady=(0,5))
        
        Label(roi_frame, text="框选区域操作:", bg=FRAME_BG).pack(side=LEFT)
        
        btn_analyze_roi = Button(roi_frame, text="分析所选区域", command=self.analyze_roi, width=15)
        btn_analyze_roi.pack(side=LEFT, padx=5)
        
        btn_clear_roi = Button(roi_frame, text="清除选区", command=self.clear_roi, width=10)
        btn_clear_roi.pack(side=LEFT, padx=5)
        
        # 页面导航
        nav_frame = Frame(preview_frame, bg=FRAME_BG)
        nav_frame.pack(fill=X, padx=10, pady=(0,10))
        
        btn_prev = Button(nav_frame, text="上一页", command=self.prev_page, width=10)
        btn_prev.pack(side=LEFT, padx=5)
        
        self.page_var = StringVar()
        self.page_var.set("0/0")
        page_label = Label(nav_frame, textvariable=self.page_var, bg=FRAME_BG)
        page_label.pack(side=LEFT, padx=10)
        
        btn_next = Button(nav_frame, text="下一页", command=self.next_page, width=10)
        btn_next.pack(side=LEFT, padx=5)
        
        # 右侧水印列表
        watermark_frame = Frame(content_frame, bg=FRAME_BG, width=400)
        watermark_frame.pack(side=RIGHT, fill=BOTH, expand=True, padx=(5,0))
        watermark_frame.pack_propagate(False)
        
        # 搜索框
        search_frame = Frame(watermark_frame, bg=FRAME_BG)
        search_frame.pack(fill=X, padx=10, pady=10)
        
        Label(search_frame, text="搜索水印:", bg=FRAME_BG).pack(side=LEFT)
        
        self.search_var = StringVar()
        search_entry = Entry(search_frame, textvariable=self.search_var, width=30)
        search_entry.pack(side=LEFT, padx=5, fill=X, expand=True)
        search_btn = Button(search_frame, text="搜索", command=self.search_watermarks)
        search_btn.pack(side=LEFT, padx=5)
        
        # 使用选项卡分类显示水印
        self.notebook = ttk.Notebook(watermark_frame)
        self.notebook.pack(fill=BOTH, expand=True, padx=10, pady=(0,10))
        
        # 文本水印选项卡
        text_frame = Frame(self.notebook, bg=FRAME_BG)
        self.notebook.add(text_frame, text="文本水印")
        
        # 图形水印选项卡
        graphic_frame = Frame(self.notebook, bg=FRAME_BG)
        self.notebook.add(graphic_frame, text="图形水印")
        
        # 文本水印列表
        text_list_frame = Frame(text_frame, bg=FRAME_BG)
        text_list_frame.pack(fill=BOTH, expand=True, padx=5, pady=5)
        
        self.text_watermark_listbox = Listbox(text_list_frame, bg=FRAME_BG, selectmode="multiple", height=15)
        self.text_watermark_listbox.pack(side=LEFT, fill=BOTH, expand=True)
        
        text_scrollbar = ttk.Scrollbar(text_list_frame, orient="vertical", command=self.text_watermark_listbox.yview)
        text_scrollbar.pack(side=RIGHT, fill=Y)
        self.text_watermark_listbox.config(yscrollcommand=text_scrollbar.set)
        
        self.text_watermark_listbox.bind('<<ListboxSelect>>', lambda e: self.on_watermark_select(e, is_text=True))
        
        # 图形水印列表
        graphic_list_frame = Frame(graphic_frame, bg=FRAME_BG)
        graphic_list_frame.pack(fill=BOTH, expand=True, padx=5, pady=5)
        
        self.graphic_watermark_listbox = Listbox(graphic_list_frame, bg=FRAME_BG, selectmode="multiple", height=15)
        self.graphic_watermark_listbox.pack(side=LEFT, fill=BOTH, expand=True)
        
        graphic_scrollbar = ttk.Scrollbar(graphic_list_frame, orient="vertical", command=self.graphic_watermark_listbox.yview)
        graphic_scrollbar.pack(side=RIGHT, fill=Y)
        self.graphic_watermark_listbox.config(yscrollcommand=graphic_scrollbar.set)
        
        self.graphic_watermark_listbox.bind('<<ListboxSelect>>', lambda e: self.on_watermark_select(e, is_text=False))
        
        # 全选/取消全选按钮 - 文本水印
        text_select_frame = Frame(text_frame, bg=FRAME_BG)
        text_select_frame.pack(fill=X, padx=5, pady=5)
        
        Button(text_select_frame, text="全选", command=lambda: self.select_all(True), width=10).pack(side=LEFT, padx=5)
        Button(text_select_frame, text="取消全选", command=lambda: self.clear_all(True), width=10).pack(side=LEFT, padx=5)
        
        # 全选/取消全选按钮 - 图形水印
        graphic_select_frame = Frame(graphic_frame, bg=FRAME_BG)
        graphic_select_frame.pack(fill=X, padx=5, pady=5)
        
        Button(graphic_select_frame, text="全选", command=lambda: self.select_all(False), width=10).pack(side=LEFT, padx=5)
        Button(graphic_select_frame, text="取消全选", command=lambda: self.clear_all(False), width=10).pack(side=LEFT, padx=5)

        # 添加自定义水印关键词
        custom_frame = Frame(watermark_frame, bg=FRAME_BG)
        custom_frame.pack(fill=X, padx=10, pady=(0,10))
        
        Label(custom_frame, text="添加自定义水印关键词:", bg=FRAME_BG).pack(anchor="w")
        
        self.custom_keyword = StringVar()
        entry = ttk.Entry(custom_frame, textvariable=self.custom_keyword)
        entry.pack(fill=X, pady=5)
        
        Button(custom_frame, text="添加", command=self.add_custom_keyword).pack(anchor="e")

    def search_watermarks(self):
        """搜索水印"""
        search_term = self.search_var.get().lower()
        if not search_term:
            return
            
        # 清除之前的选择
        self.text_watermark_listbox.selection_clear(0, END)
        self.graphic_watermark_listbox.selection_clear(0, END)
        
        text_matches = 0
        graphic_matches = 0
        
        # 搜索文本水印
        for i in range(self.text_watermark_listbox.size()):
            item_text = self.text_watermark_listbox.get(i).lower()
            if search_term in item_text:
                self.text_watermark_listbox.selection_set(i)
                # 确保第一个匹配项可见
                if text_matches == 0:
                    self.text_watermark_listbox.see(i)
                text_matches += 1
                
        # 搜索图形水印
        for i in range(self.graphic_watermark_listbox.size()):
            item_text = self.graphic_watermark_listbox.get(i).lower()
            if search_term in item_text:
                self.graphic_watermark_listbox.selection_set(i)
                # 确保第一个匹配项可见
                if graphic_matches == 0 and text_matches == 0:
                    self.graphic_watermark_listbox.see(i)
                    # 如果图形水印有匹配但文本没有，切换到图形水印标签
                    self.notebook.select(1)
                graphic_matches += 1
                
        # 根据匹配结果决定显示哪个选项卡
        if text_matches > 0:
            # 如果文本水印有匹配，切换到文本水印标签
            self.notebook.select(0)
            
        total_matches = text_matches + graphic_matches
        self.status_var.set(f"搜索 '{search_term}' 找到 {total_matches} 个结果")

    def display_page(self, page_index):
        """显示指定页面"""
        if not self.doc or page_index < 0 or page_index >= self.total_pages:
            return
            
        # 获取页面
        page = self.doc[page_index]
        
        # 设置缩放因子以适应窗口
        zoom = 1.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)
        
        # 将pixmap转换为PIL图像
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        
        # 清除Canvas上的所有内容
        self.pdf_canvas.delete("all")
        
        # 调整图像大小以适应canvas
        canvas_width = self.pdf_canvas.winfo_width() or 700
        canvas_height = self.pdf_canvas.winfo_height() or 800
        
        # 保持宽高比
        img_ratio = img.width / img.height
        canvas_ratio = canvas_width / canvas_height
        
        if img_ratio > canvas_ratio:
            # 图像更宽
            new_width = canvas_width
            new_height = int(canvas_width / img_ratio)
        else:
            # 图像更高
            new_height = canvas_height
            new_width = int(canvas_height * img_ratio)
        
        self.scale_factor = new_width / img.width
        img = img.resize((new_width, new_height), Image.LANCZOS)
        
        # 保存原始图像副本用于框选
        self.original_img = img
        self.photo_image = ImageTk.PhotoImage(img)
        
        # 在Canvas上显示图像
        self.pdf_canvas.create_image(0, 0, image=self.photo_image, anchor="nw")
        
        # 更新当前页信息
        self.current_page = page_index
        self.page_var.set(f"{page_index + 1}/{self.total_pages}")

        # 清除任何框选
        self.roi_start_x = -1
        self.roi_start_y = -1
        self.roi_end_x = -1
        self.roi_end_y = -1
        self.roi_rectangle = None

    def on_mouse_down(self, event):
        """鼠标按下事件，开始框选"""
        # 记录起始位置
        self.roi_start_x = event.x
        self.roi_start_y = event.y
        
        # 清除已有的框选
        if self.roi_rectangle is not None:
            self.pdf_canvas.delete(self.roi_rectangle)
            self.roi_rectangle = None

    def on_mouse_drag(self, event):
        """鼠标拖动事件，实时更新框选区域"""
        if self.roi_start_x < 0 or self.roi_start_y < 0:
            return
            
        # 更新结束位置
        self.roi_end_x = event.x
        self.roi_end_y = event.y
        
        # 删除之前的矩形(如果存在)
        if self.roi_rectangle is not None:
            self.pdf_canvas.delete(self.roi_rectangle)
            
        # 绘制新矩形
        self.roi_rectangle = self.pdf_canvas.create_rectangle(
            self.roi_start_x, self.roi_start_y, 
            self.roi_end_x, self.roi_end_y,
            outline="red", width=2
        )

    def on_mouse_up(self, event):
        """鼠标释放事件，完成框选"""
        if self.roi_start_x < 0 or self.roi_start_y < 0:
            return
            
        # 更新最终结束位置
        self.roi_end_x = event.x
        self.roi_end_y = event.y
        
        # 确保有足够大的选区
        if abs(self.roi_end_x - self.roi_start_x) < 10 or abs(self.roi_end_y - self.roi_start_y) < 10:
            # 选区太小，清除
            if self.roi_rectangle is not None:
                self.pdf_canvas.delete(self.roi_rectangle)
                self.roi_rectangle = None
            self.roi_start_x = -1
            self.roi_start_y = -1
            return
            
        # 绘制最终矩形
        if self.roi_rectangle is not None:
            self.pdf_canvas.delete(self.roi_rectangle)
        self.roi_rectangle = self.pdf_canvas.create_rectangle(
            self.roi_start_x, self.roi_start_y, 
            self.roi_end_x, self.roi_end_y,
            outline="red", width=2
        )

    def clear_roi(self):
        """清除框选区域"""
        if self.roi_rectangle is not None:
            self.pdf_canvas.delete(self.roi_rectangle)
            self.roi_rectangle = None
        
        self.roi_start_x = -1
        self.roi_start_y = -1
        self.roi_end_x = -1
        self.roi_end_y = -1

    def analyze_roi(self):
        """分析框选区域"""
        if not self.doc or self.current_page >= self.total_pages:
            messagebox.showinfo("提示", "请先打开PDF文件")
            return
            
        if self.roi_start_x < 0 or self.roi_rectangle is None:
            messagebox.showinfo("提示", "请先框选区域")
            return
            
        # 获取当前页面
        page = self.doc[self.current_page]
        
        # 转换屏幕坐标到PDF坐标
        pdf_width = page.rect.width
        pdf_height = page.rect.height
        
        # 获取显示图像的尺寸
        img_width = self.original_img.width
        img_height = self.original_img.height
        
        # 计算缩放比例
        x_scale = pdf_width / img_width
        y_scale = pdf_height / img_height
        
        # 转换坐标
        x0 = min(self.roi_start_x, self.roi_end_x) * x_scale
        y0 = min(self.roi_start_y, self.roi_end_y) * y_scale
        x1 = max(self.roi_start_x, self.roi_end_x) * x_scale
        y1 = max(self.roi_start_y, self.roi_end_y) * y_scale
        
        # 创建矩形区域
        rect = fitz.Rect(x0, y0, x1, y1)
        
        # 保存原始水印列表
        original_watermarks = self.watermarks.copy()
        
        # 清空水印列表以准备只显示框选区域的内容
        self.watermarks = []
        
        # 分析当前页面框选区域
        current_page_watermarks = self._analyze_roi_in_page(self.current_page, rect)
        self.watermarks.extend(current_page_watermarks)
        
        if len(current_page_watermarks) > 0:
            # 弹出对话框询问是否分析所有页面的相同位置
            analyze_all = messagebox.askyesno("水印分析", 
                "已在当前页面检测到水印。是否分析所有页面相同位置的水印？\n"
                "这将有助于识别重复出现的水印，如页眉页脚或背景水印。")
                
            if analyze_all:
                # 计算归一化的相对位置（相对于页面大小的百分比）
                rel_rect = self._get_relative_rect(rect, page)
                
                # 分析进度信息
                total_pages = self.total_pages
                progress_step = max(1, total_pages // 10)  # 每10%更新一次状态
                self.status_var.set("开始分析所有页面的水印...")
                self.master.update()
                
                # 遍历其他页面查找相同位置的水印
                for page_idx in range(total_pages):
                    # 跳过当前页面，因为已经分析过了
                    if page_idx == self.current_page:
                        continue
                    
                    # 更新状态
                    if page_idx % progress_step == 0:
                        progress_pct = int(page_idx / total_pages * 100)
                        self.status_var.set(f"正在分析页面 {page_idx+1}/{total_pages}... ({progress_pct}%)")
                        self.master.update()
                    
                    # 获取当前页面
                    current_page = self.doc[page_idx]
                    
                    # 将相对位置转换回此页面的实际坐标
                    page_rect = self._get_absolute_rect(rel_rect, current_page)
                    
                    # 分析此页面相同位置的水印
                    page_watermarks = self._analyze_roi_in_page(page_idx, page_rect)
                    self.watermarks.extend(page_watermarks)
                
                self.status_var.set(f"水印分析完成，共在 {total_pages} 页中检测到 {len(self.watermarks)} 个水印")
        
        # 更新水印列表显示
        self._update_watermark_list()
        
        # 显示结果
        if len(self.watermarks) > 0:
            message = f"共检测到 {len(self.watermarks)} 个水印"
            if len(self.watermarks) > len(current_page_watermarks):
                message += f"，其中当前页面 {len(current_page_watermarks)} 个，其他页面 {len(self.watermarks) - len(current_page_watermarks)} 个"
            self.status_var.set(f"框选区域分析完成，{message}")
            messagebox.showinfo("框选结果", f"已分析框选区域，{message}")
            
            # 添加恢复按钮
            restore_btn = Button(
                self.master, 
                text="恢复显示所有水印", 
                command=lambda: self.restore_all_watermarks(original_watermarks, restore_btn)
            )
            restore_btn.pack(side=TOP, pady=5)
        else:
            self.status_var.set("框选区域内未发现水印内容")
            messagebox.showinfo("框选结果", "框选区域内未发现水印内容")
            # 恢复原始水印列表
            self.watermarks = original_watermarks
            self._update_watermark_list()
    
    def _analyze_roi_in_page(self, page_idx, rect):
        """分析指定页面指定区域内的水印"""
        watermarks = []
        page = self.doc[page_idx]
        
        # 分析文本内容
        text = page.get_text("text", clip=rect)
        if text.strip():
            watermark = WatermarkInfo("框选文本", 0, page_idx, text, rect, is_text=True)
            watermarks.append(watermark)
            
        # 分析图像内容
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(1, 1), clip=rect)
            if pix.width > 0 and pix.height > 0:
                # 分析区域内是否有图形或图像
                has_image = False
                
                # 检查是否包含非白色像素(简单方法检测有意义的内容)
                samples = pix.samples
                if any(v < 250 for v in samples[::3]):  # 每3个取样点检查一个R值
                    has_image = True
                    
                if has_image:
                    watermark = WatermarkInfo("框选图形", 0, page_idx, f"第{page_idx+1}页图形区域", rect, is_text=False)
                    watermarks.append(watermark)
        except:
            pass
            
        return watermarks
    
    def _get_relative_rect(self, rect, page):
        """将矩形区域坐标转换为相对于页面大小的比例"""
        page_width = page.rect.width
        page_height = page.rect.height
        return {
            "x0_rel": rect.x0 / page_width,
            "y0_rel": rect.y0 / page_height,
            "x1_rel": rect.x1 / page_width,
            "y1_rel": rect.y1 / page_height
        }
    
    def _get_absolute_rect(self, rel_rect, page):
        """将相对矩形坐标转换为页面实际坐标"""
        page_width = page.rect.width
        page_height = page.rect.height
        return fitz.Rect(
            rel_rect["x0_rel"] * page_width,
            rel_rect["y0_rel"] * page_height,
            rel_rect["x1_rel"] * page_width,
            rel_rect["y1_rel"] * page_height
        )
    
    def restore_all_watermarks(self, original_watermarks, restore_btn):
        """恢复显示所有水印"""
        self.watermarks = original_watermarks
        self._update_watermark_list()
        self.status_var.set(f"已恢复显示所有 {len(original_watermarks)} 个水印")
        restore_btn.destroy()

    def open_pdf(self):
        """打开PDF文件"""
        file_path = filedialog.askopenfilename(
            title="选择PDF文件",
            filetypes=[("PDF文件", "*.pdf"), ("所有文件", "*.*")]
        )
        
        if file_path:
            self.pdf_path = file_path
            self.status_var.set(f"已打开: {os.path.basename(file_path)}")
            
            try:
                # 打开PDF文件
                self.doc = fitz.open(file_path)
                self.reader = PdfReader(file_path)
                self.total_pages = self.doc.page_count
                self.current_page = 0
                self.page_var.set(f"1/{self.total_pages}")
                
                # 显示第一页
                self.display_page(0)
                
                # 清空水印列表
                self.watermarks = []
                self.text_watermark_listbox.delete(0, END)
                self.graphic_watermark_listbox.delete(0, END)
            except Exception as e:
                messagebox.showerror("错误", f"打开PDF文件时出错: {str(e)}")
    
    def highlight_watermarks(self):
        """高亮显示当前页面的水印"""
        # 这里可以添加代码来在页面预览中高亮显示水印位置
        pass
    
    def prev_page(self):
        """上一页"""
        if self.current_page > 0:
            self.display_page(self.current_page - 1)
    
    def next_page(self):
        """下一页"""
        if self.current_page < self.total_pages - 1:
            self.display_page(self.current_page + 1)
    
    def analyze_watermarks(self):
        """分析水印"""
        if not self.pdf_path:
            messagebox.showinfo("提示", "请先打开一个PDF文件")
            return
        
        self.status_var.set("正在分析水印...")
        self.watermarks = []
        self.text_watermark_listbox.delete(0, END)
        self.graphic_watermark_listbox.delete(0, END)
        
        # 使用线程执行分析以避免UI冻结
        threading.Thread(target=self._analyze_watermarks_thread).start()
    
    def _analyze_watermarks_thread(self):
        """分析水印线程"""
        try:
            # 使用PyMuPDF分析水印
            self._analyze_with_pymupdf()
            
            # 查找重复出现的内容（可能是水印）
            self._find_repeated_content()
            
            # 更新UI
            self.master.after(0, self._update_watermark_list)
        except Exception as e:
            self.master.after(0, lambda: messagebox.showerror("错误", f"分析水印时出错: {str(e)}"))
            self.master.after(0, lambda: self.status_var.set("分析水印失败"))
    
    def _analyze_with_pymupdf(self):
        """使用PyMuPDF分析PDF水印"""
        for page_idx in range(self.total_pages):
            # 使用PyMuPDF获取页面内容
            page = self.doc[page_idx]
            
            # 1. 查找旋转的文本 (可能是水印)
            for block in page.get_text("dict")["blocks"]:
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            # 检查旋转角度
                            if abs(span.get("dir", [1, 0])[0]) < 0.95:  # 角度超过约18度
                                text = span.get("text", "").strip()
                                if text:
                                    rect = fitz.Rect(span["bbox"])
                                    watermark = WatermarkInfo("旋转文本", 0, page_idx, text, rect, is_text=True)
                                    self.watermarks.append(watermark)
            
            # 2. 查找页面边缘区域的文本 (常见水印位置)
            self._find_edge_text_watermarks(page, page_idx)
            
            # 3. 查找包含关键词的文本 (如中国知网等)
            self._find_keyword_watermarks(page, page_idx)
            
            # 4. 查找透明或半透明对象 (可能是水印)
            for annot in page.annots():
                if annot.type[1] == 'Watermark':
                    content = f"标记水印注释: {annot.info}"
                    watermark = WatermarkInfo("标记水印", 0, page_idx, content, annot.rect, is_text=False)
                    self.watermarks.append(watermark)
            
            # 5. 查找图像和形状对象 (可能是图形水印)
            self._find_image_watermarks(page, page_idx)
            
            # 6. 使用xref检查对象
            for xref in page.get_contents():
                try:
                    content = self.doc.xref_stream(xref).decode('utf-8', errors='ignore')
                    # 检查是否包含水印相关关键词
                    if '/Watermark' in content:
                        watermark = WatermarkInfo("内容流水印", xref, page_idx, "内容流中包含Watermark标记", is_text=False)
                        self.watermarks.append(watermark)
                    
                    # 查找常见的水印操作码组合
                    if 'BDC' in content and 'EMC' in content and ('Tm' in content or 'cm' in content):
                        watermark = WatermarkInfo("可能水印", xref, page_idx, "含有水印特征操作码", is_text=False)
                        self.watermarks.append(watermark)
                except:
                    pass
                    
    def _find_edge_text_watermarks(self, page, page_idx):
        """查找页面边缘区域的文本"""
        page_width = page.rect.width
        page_height = page.rect.height
        
        # 边缘区域定义 (页面的上下左右边缘)
        top_margin = page_height * 0.1
        bottom_margin = page_height * 0.1
        left_margin = page_width * 0.1
        right_margin = page_width * 0.1
        
        # 获取页面文本块
        text_dict = page.get_text("dict")
        
        for block in text_dict["blocks"]:
            if "lines" not in block:
                continue
                
            for line in block["lines"]:
                for span in line["spans"]:
                    rect = fitz.Rect(span["bbox"])
                    text = span["text"].strip()
                    
                    if not text:
                        continue
                    
                    # 检查文本是否在页面边缘
                    is_in_edge = (
                        rect.y0 <= top_margin or  # 上边缘
                        rect.y1 >= page_height - bottom_margin or  # 下边缘
                        rect.x0 <= left_margin or  # 左边缘
                        rect.x1 >= page_width - right_margin  # 右边缘
                    )
                    
                    if is_in_edge:
                        # 检查是否可能是页码 (通常在底部中央)
                        is_page_number = (
                            rect.y1 >= page_height - bottom_margin and  # 底部
                            rect.x0 >= page_width * 0.4 and rect.x1 <= page_width * 0.6 and  # 中间位置
                            len(text) <= 4 and text.isdigit()  # 较短的数字
                        )
                        
                        if not is_page_number:
                            watermark = WatermarkInfo("边缘文本", 0, page_idx, text, rect, is_text=True)
                            self.watermarks.append(watermark)
                            
    def _find_keyword_watermarks(self, page, page_idx):
        """查找包含关键词的文本"""
        text_dict = page.get_text("dict")
        
        for block in text_dict["blocks"]:
            if "lines" not in block:
                continue
                
            for line in block["lines"]:
                line_text = ""
                line_rect = None
                
                # 收集一整行的文本
                for span in line["spans"]:
                    span_text = span["text"].strip()
                    if span_text:
                        line_text += span_text
                        
                        # 更新行的矩形区域
                        span_rect = fitz.Rect(span["bbox"])
                        if line_rect is None:
                            line_rect = span_rect
                        else:
                            line_rect = line_rect.include_rect(span_rect)
                
                # 检查文本中是否包含水印关键词
                if line_text:
                    for keyword in WATERMARK_KEYWORDS:
                        if keyword.lower() in line_text.lower():
                            watermark = WatermarkInfo("关键词水印", 0, page_idx, line_text, line_rect, is_text=True)
                            self.watermarks.append(watermark)
                            break
                            
    def _find_image_watermarks(self, page, page_idx):
        """查找可能的图像水印"""
        # 获取页面上的图像
        image_list = page.get_images(full=True)
        
        for img_index, img_info in enumerate(image_list):
            xref = img_info[0]  # 图像的xref号
            
            # 获取图像属性
            try:
                base_image = self.doc.extract_image(xref)
                if not base_image:
                    continue
                    
                # 检查图像大小
                image_size = base_image["width"] * base_image["height"]
                
                # 如果图像大小适中（可能是水印而非大图）
                if image_size < page.rect.width * page.rect.height * 0.3:
                    # 查找图像在页面上的位置
                    for item in page.get_drawings():
                        if "image" in item and item["image"] == xref:
                            if "rect" in item:
                                rect = fitz.Rect(item["rect"])
                                watermark = WatermarkInfo("图像对象", xref, page_idx, f"图像 #{img_index}", rect, is_text=False)
                                self.watermarks.append(watermark)
                                break
            except:
                pass
    
    def _find_repeated_content(self):
        """查找在多页中重复出现的内容（可能是水印）"""
        # 构建重复内容映射
        repeated_content = {}
        
        # 按文本内容和位置分组
        for watermark in self.watermarks:
            if not watermark.content:
                continue
                
            # 位置归一化 (将矩形坐标转换为相对位置)
            position_key = "unknown"
            if watermark.rect:
                page_idx = watermark.page_index
                if page_idx < self.total_pages:
                    page = self.doc[page_idx]
                    page_width = page.rect.width
                    page_height = page.rect.height
                    
                    # 计算中心点的相对位置
                    if page_width > 0 and page_height > 0:
                        center_x = (watermark.rect.x0 + watermark.rect.x1) / 2 / page_width
                        center_y = (watermark.rect.y0 + watermark.rect.y1) / 2 / page_height
                        
                        # 将位置分为9个区域 (3x3网格)
                        x_pos = int(center_x * 3)
                        y_pos = int(center_y * 3)
                        position_key = f"{x_pos}_{y_pos}"
            
            # 使用内容和位置作为键
            key = f"{watermark.content}:{position_key}"
            
            if key not in repeated_content:
                repeated_content[key] = []
            repeated_content[key].append(watermark.page_index)
        
        # 标记重复出现在多页的内容
        for key, pages in repeated_content.items():
            if len(pages) > 1:  # 出现在多页
                if len(pages) >= self.total_pages * 0.5:  # 至少出现在一半的页面中
                    # 更新水印对象，添加重复信息
                    content = key.split(':', 1)[0]
                    for watermark in self.watermarks:
                        if watermark.content == content:
                            watermark.type_name = f"{watermark.type_name} (重复于{len(pages)}页)"
    
    def _update_watermark_list(self):
        """更新水印列表UI"""
        self.text_watermark_listbox.delete(0, END)
        self.graphic_watermark_listbox.delete(0, END)
        
        if not self.watermarks:
            self.status_var.set("未检测到水印")
            return
            
        # 按页面排序水印
        self.watermarks.sort(key=lambda w: (w.page_index, w.index))
        
        # 更新列表
        for i, watermark in enumerate(self.watermarks):
            if watermark.is_text:
                self.text_watermark_listbox.insert(END, str(watermark))
                if watermark.selected:
                    self.text_watermark_listbox.selection_set(i)
            else:
                self.graphic_watermark_listbox.insert(END, str(watermark))
                if watermark.selected:
                    self.graphic_watermark_listbox.selection_set(i)
        
        self.status_var.set(f"检测到 {len(self.watermarks)} 个可能的水印")
    
    def on_watermark_select(self, event, is_text):
        """水印选择事件"""
        selected = self.text_watermark_listbox.curselection() if is_text else self.graphic_watermark_listbox.curselection()
        for i, watermark in enumerate(self.watermarks):
            watermark.selected = i in selected
    
    def select_all(self, is_text):
        """全选水印"""
        for i in range(len(self.watermarks)):
            if is_text:
                self.text_watermark_listbox.selection_set(i)
            else:
                self.graphic_watermark_listbox.selection_set(i)
            self.watermarks[i].selected = True
    
    def clear_all(self, is_text):
        """取消全选水印"""
        self.text_watermark_listbox.selection_clear(0, END) if is_text else self.graphic_watermark_listbox.selection_clear(0, END)
        for watermark in self.watermarks:
            watermark.selected = False
    
    def remove_selected_watermarks(self):
        """移除选中的水印"""
        if not self.pdf_path or not self.watermarks:
            messagebox.showinfo("提示", "请先打开PDF并分析水印")
            return
            
        # 检查是否有选中的水印
        selected_watermarks = [w for w in self.watermarks if w.selected]
        if not selected_watermarks:
            messagebox.showinfo("提示", "请先选择要移除的水印")
            return
            
        # 询问保存路径
        output_path = filedialog.asksaveasfilename(
            title="保存去水印PDF",
            defaultextension=".pdf",
            filetypes=[("PDF文件", "*.pdf"), ("所有文件", "*.*")],
            initialfile=f"{os.path.splitext(os.path.basename(self.pdf_path))[0]}_无水印.pdf"
        )
        
        if not output_path:
            return
            
        self.status_var.set("正在移除水印...")
        
        # 使用线程执行水印移除以避免UI冻结
        threading.Thread(target=lambda: self._remove_watermarks_thread(output_path, selected_watermarks)).start()
    
    def _remove_watermarks_thread(self, output_path, selected_watermarks):
        """水印移除线程"""
        try:
            # 使用PyMuPDF处理水印
            # 创建一个临时文档
            output_doc = fitz.open()
            
            is_modified = False
            total_pages = self.total_pages
            progress_step = max(1, total_pages // 10)  # 每10%更新一次状态
            
            # 按页面处理
            for page_idx in range(total_pages):
                # 更新状态
                if page_idx % progress_step == 0:
                    progress_pct = int(page_idx / total_pages * 100)
                    self.master.after(0, lambda p=progress_pct: self.status_var.set(f"正在处理页面 {page_idx+1}/{total_pages}... ({p}%)"))
                
                page = self.doc[page_idx]
                # 获取当前页面需要移除的水印
                page_watermarks = [w for w in selected_watermarks if w.page_index == page_idx]
                
                if page_watermarks:
                    # 收集需要移除的文本内容和位置
                    texts_to_remove = []
                    rects_to_redact = []
                    
                    for watermark in page_watermarks:
                        if watermark.rect and watermark.content:
                            texts_to_remove.append(watermark.content)
                            rects_to_redact.append(watermark.rect)
                            
                            # 如果是关键词水印，可能需要考虑不完全匹配的情况
                            if watermark.type_name.startswith("关键词水印"):
                                content = watermark.content
                                for keyword in WATERMARK_KEYWORDS:
                                    if keyword.lower() in content.lower():
                                        # 尝试找出关键词在文本中的位置
                                        keyword_pos = content.lower().find(keyword.lower())
                                        if keyword_pos >= 0:
                                            # 计算关键词在矩形区域中的相对位置
                                            rel_pos = keyword_pos / len(content)
                                            rect_width = watermark.rect.width
                                            # 创建一个更精确的矩形，只覆盖关键词部分
                                            keyword_rect = fitz.Rect(
                                                watermark.rect.x0 + rel_pos * rect_width,
                                                watermark.rect.y0,
                                                watermark.rect.x0 + (rel_pos + len(keyword)/len(content)) * rect_width,
                                                watermark.rect.y1
                                            )
                                            rects_to_redact.append(keyword_rect)
                        
                        # 处理注释水印
                        if watermark.type_name.startswith("标记水印"):
                            for annot in page.annots():
                                if annot.type[1] == 'Watermark':
                                    page.delete_annot(annot)
                                    is_modified = True
                        
                        # 处理内容流水印
                        if watermark.type_name.startswith("内容流") or watermark.type_name.startswith("可能水印"):
                            xref = watermark.index
                            if xref > 0:
                                try:
                                    original = self.doc.xref_stream(xref).decode('utf-8', errors='ignore')
                                    # 移除水印相关内容
                                    modified = self._remove_watermark_content(original)
                                    if modified != original:
                                        # 更新内容
                                        self.doc.update_stream(xref, modified.encode('utf-8'))
                                        is_modified = True
                                except:
                                    pass
                    
                    # 使用PyMuPDF的redact功能移除文本水印
                    if texts_to_remove or rects_to_redact:
                        # 创建一个副本，以避免修改原始文档
                        temp_doc = fitz.open()
                        temp_doc.insert_pdf(self.doc, from_page=page_idx, to_page=page_idx)
                        temp_page = temp_doc[0]
                        
                        # 使用redact功能覆盖文本区域
                        for rect in rects_to_redact:
                            # 白色填充，无边框
                            temp_page.add_redact_annot(rect, fill=(1, 1, 1))
                        
                        # 应用redaction
                        temp_page.apply_redactions()
                        
                        # 将处理后的页面添加到输出文档
                        output_doc.insert_pdf(temp_doc)
                        temp_doc.close()
                        is_modified = True
                    else:
                        # 如果没有需要redact的区域，直接复制原页面
                        output_doc.insert_pdf(self.doc, from_page=page_idx, to_page=page_idx)
                else:
                    # 如果没有水印，直接复制页面
                    output_doc.insert_pdf(self.doc, from_page=page_idx, to_page=page_idx)
            
            # 保存处理后的文档
            if is_modified:
                output_doc.save(output_path)
                self.master.after(0, lambda: self.status_var.set(f"水印已成功移除，文件已保存至: {os.path.basename(output_path)}"))
                self.master.after(0, lambda: messagebox.showinfo("成功", f"水印已成功移除，文件已保存至:\n{output_path}"))
            else:
                # 如果没有修改，直接保存原始文档
                self.doc.save(output_path)
                self.master.after(0, lambda: self.status_var.set("未检测到需要移除的水印，已保存原始文件副本"))
                self.master.after(0, lambda: messagebox.showinfo("提示", "未检测到需要移除的水印，已保存原始文件副本"))
                
            # 关闭临时文档
            output_doc.close()
                
        except Exception as e:
            self.master.after(0, lambda: messagebox.showerror("错误", f"移除水印时出错: {str(e)}"))
            self.master.after(0, lambda: self.status_var.set("移除水印失败"))
    
    def _remove_watermark_content(self, content):
        """从PDF内容中移除水印相关内容"""
        # 移除BDC...EMC标记内容段
        cleaned = ""
        i = 0
        while i < len(content):
            # 检查是否有BDC开头的标记内容块
            bdc_pos = content.find('/Artifact', i)
            if bdc_pos >= 0:
                watermark_pos = content.find('/Watermark', bdc_pos)
                if watermark_pos >= 0 and watermark_pos < bdc_pos + 50:  # 如果Watermark在BDC附近
                    emc_pos = content.find('EMC', watermark_pos)
                    if emc_pos >= 0:
                        cleaned += content[i:bdc_pos]
                        i = emc_pos + 3  # 跳过EMC
                        continue
            
            # 处理其他可能的水印特征
            if '/Watermark' in content[i:i+100]:
                # 寻找这段水印区域的结束位置
                end_pos = content.find('Q', i + 10)
                if end_pos > i:
                    i = end_pos + 1
                    continue
            
            # 添加非水印内容
            cleaned += content[i]
            i += 1
            
        return cleaned

    def add_custom_keyword(self):
        """添加自定义水印关键词"""
        keyword = self.custom_keyword.get().strip()
        if keyword:
            if keyword not in WATERMARK_KEYWORDS:
                WATERMARK_KEYWORDS.append(keyword)
                self.status_var.set(f"已添加自定义水印关键词: {keyword}")
                # 如果PDF已加载，重新分析
                if self.doc:
                    self.analyze_watermarks()
            else:
                messagebox.showinfo("提示", "该关键词已存在")


if __name__ == "__main__":
    try:
        # 创建应用程序
        root = Tk()
        app = PDFWatermarkRemover(root)
        root.mainloop()
    except ImportError as e:
        module = str(e).split("'")[-2]
        print(f"错误：缺少必要的依赖模块 {module}")
        print("请使用以下命令安装依赖：")
        print("pip install PyPDF2 PyMuPDF pillow")
        input("按回车键退出...")
    except Exception as e:
        print(f"发生错误：{str(e)}")
        input("按回车键退出...")
