"""PDF watermark removal, save, and OCR-processing mixin."""

import os
import re
import sys
from typing import Dict, List, Optional

import pymupdf as fitz
import numpy as np

from pdf_decryptor import is_pdf_encrypted
from .content_stream import _filter_watermark_paths
from .models import WatermarkInfo


class ProcessingMixin:
    def _apply_watermarks_to_pdf(self, input_path: str, output_path: str, selected: List[WatermarkInfo]):
        work_doc = fitz.open(input_path)
        try:
            selected_by_page: Dict[int, List[WatermarkInfo]] = {}
            for w in selected:
                selected_by_page.setdefault(w.page_index, []).append(w)

            for page_idx, page_items in selected_by_page.items():
                if page_idx < 0 or page_idx >= work_doc.page_count:
                    continue
                page = work_doc[page_idx]

                if any(w.source == "explicit-watermark-artifact" for w in page_items):
                    self._remove_explicit_watermark_artifacts(work_doc, page)

                if any(w.source == "rendered-diagonal" for w in page_items):
                    self._remove_rendered_diagonal_pattern(work_doc, page)

                # 1) 斜向浅色矢量/渲染水印：从内容流中删除浅色或半透明的填充路径，
                #    而不是整块白色矩形，从而避免覆盖正文。
                soft_rects = []
                for wm in page_items:
                    if wm.source in ("soft-vector", "rendered-diagonal"):
                        if wm.redaction_rects:
                            soft_rects.extend(wm.redaction_rects)
                        elif wm.rect:
                            soft_rects.append(wm.rect)
                if soft_rects:
                    removed_paths = self._remove_soft_vector_paths(work_doc, page, soft_rects)
                    if removed_paths == 0:
                        self._redact_pale_regions(work_doc, page, soft_rects)

                # 2) 图片水印按页面删除引用，避免清空图片流后留下黑块。
                for wm in page_items:
                    if wm.is_text or wm.source in (
                        "soft-vector", "rendered-diagonal", "explicit-watermark-artifact", "background-image"
                    ):
                        continue
                    removed_calls = 0
                    if wm.container_xref:
                        removed_calls += self._remove_xobject_calls_from_page(
                            work_doc, page, wm.container_xref
                        )
                    if wm.xref or wm.resource_name:
                        removed_calls += self._remove_xobject_calls_from_page(
                            work_doc, page, wm.xref or 0, wm.resource_name
                        )
                    if removed_calls == 0 and wm.xref:
                        try:
                            page.delete_image(wm.xref)
                        except Exception:
                            pass

                # 3) 文本 / ROI模板 / 其他矢量用局部擦除
                redact_needed = False
                for wm in page_items:
                    if wm.source in (
                        "soft-vector", "rendered-diagonal", "explicit-watermark-artifact", "background-image",
                        "explicit-image-watermark", "explicit-form-watermark",
                    ):
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

            self._save_unencrypted_pdf(work_doc, output_path)
        finally:
            work_doc.close()

    @staticmethod
    def _save_unencrypted_pdf(doc, output_path: str):
        doc.save(
            output_path,
            # Remove unreferenced watermark/font objects and compact the many
            # small streams emitted by PDF Candy without rasterizing text.
            garbage=4,
            clean=1,
            deflate=True,
            use_objstms=1,
            encryption=fitz.PDF_ENCRYPT_NONE,
        )
        if is_pdf_encrypted(output_path):
            raise RuntimeError("输出 PDF 仍带有密码或保护，已拒绝保存")

    @staticmethod
    def _find_tessdata() -> str:
        package_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.environ.get("TESSDATA_PREFIX"),
            os.path.join(package_dir, "tessdata"),
            os.path.join(os.path.dirname(package_dir), "tessdata"),
            os.path.join(getattr(sys, "_MEIPASS", ""), "tessdata"),
            os.path.join(os.environ.get("ProgramFiles", ""), "Tesseract-OCR", "tessdata"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tesseract-OCR", "tessdata"),
        ]
        for folder in candidates:
            if not folder:
                continue
            if all(os.path.isfile(os.path.join(folder, name)) for name in ("chi_sim.traineddata", "eng.traineddata")):
                return folder
        raise RuntimeError(
            "未找到 Tesseract 的 chi_sim.traineddata 和 eng.traineddata。"
            "请安装 Tesseract 中文语言包，或把两个文件放入 Mark12.py 同目录的 tessdata 文件夹。"
        )

    def _save_ocr_searchable_pdf(self, input_path: str, output_path: str):
        """Create an image-backed PDF with a correct invisible OCR text layer."""
        tessdata = self._find_tessdata()
        result = fitz.open()
        try:
            with fitz.open(input_path) as source:
                total = source.page_count
                for index, page in enumerate(source):
                    self.status_var.set(f"正在 OCR 修复复制文本：{index + 1}/{total}")
                    self.master.update_idletasks()
                    pix = page.get_pixmap(dpi=180, colorspace=fitz.csRGB, alpha=False)
                    one_page = fitz.open(
                        "pdf",
                        pix.pdfocr_tobytes(
                            compress=True,
                            language="chi_sim+eng",
                            tessdata=tessdata,
                        ),
                    )
                    try:
                        result.insert_pdf(one_page)
                    finally:
                        one_page.close()
            self._save_unencrypted_pdf(result, output_path)
        finally:
            result.close()

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
        redacts = []
        for rect in rects:
            if not rect or rect.is_empty or rect.width < 2 or rect.height < 2:
                continue
            key = (round(rect.x0, 1), round(rect.y0, 1), round(rect.x1, 1), round(rect.y1, 1))
            if key in processed:
                continue
            processed.add(key)
            redacts.extend(self._redact_pale_region(page, rect, 3000 - len(redacts)))
            if len(redacts) >= 3000:
                break
        for rect in redacts:
            page.add_redact_annot(rect, fill=(1, 1, 1))
        if redacts:
            try:
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            except Exception:
                pass

    def _redact_pale_region(self, page, rect, max_cells: int = 3000):
        if max_cells <= 0:
            return []
        scale = 2.0
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
        except Exception:
            return []
        if pix.width <= 0 or pix.height <= 0:
            return []
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
                if len(redacts) >= max_cells:
                    break
            if len(redacts) >= max_cells:
                break
        return redacts

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

    @staticmethod
    def _remove_explicit_watermark_artifacts(doc, page) -> int:
        pattern = re.compile(
            r"/Artifact\s*<<(?:(?!>>).)*?/Subtype\s*/Watermark\b"
            r"(?:(?!>>).)*?>>\s*BDC(?:(?!\bEMC\b).)*?\bEMC\b",
            re.DOTALL,
        )
        removed = 0
        for content_xref in page.get_contents():
            try:
                raw = doc.xref_stream(content_xref)
                src = raw.decode("latin1", "ignore")
                updated, count = pattern.subn(" ", src)
                if count:
                    doc.update_stream(content_xref, updated.encode("latin1", "ignore"))
                    removed += count
            except Exception:
                continue
        return removed

    def _remove_xobject_calls_from_page(
        self,
        doc,
        page,
        target_xref: int,
        resource_name: Optional[str] = None,
    ) -> int:
        names = {resource_name} if resource_name else set()
        if target_xref:
            names.update(self._page_xobject_names(page, doc).get(target_xref, []))
            if not names:
                for xref, name, *_ in page.get_xobjects():
                    if xref == target_xref:
                        names.add(name)
                for info in page.get_images(full=True):
                    if info[0] == target_xref:
                        names.add(info[7])
        names.discard(None)
        if not names:
            return 0

        removed = 0
        for content_xref in page.get_contents():
            try:
                raw = doc.xref_stream(content_xref)
                src = raw.decode("latin1", "ignore")
                updated = src
                for name in names:
                    updated, count = re.subn(
                        rf"/{re.escape(name)}\s+Do\b", " ", updated
                    )
                    removed += count
                if updated != src:
                    doc.update_stream(content_xref, updated.encode("latin1", "ignore"))
            except Exception:
                continue
        return removed
