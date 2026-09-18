"""Watermark detection and ROI-analysis mixin."""

import hashlib
import math
import re
from typing import Dict, List, Optional, Tuple
import cv2
import pymupdf as fitz
import numpy as np

from .constants import WATERMARK_KEYWORDS
from .models import ImageCandidate, VectorCandidate, WatermarkInfo


class DetectionMixin:
    def _find_watermark_annotations(self, page, page_idx: int) -> List[WatermarkInfo]:
        """Find standard PDF Watermark annotations attached to a page."""
        results = []
        try:
            annotations = list(page.annots() or [])
        except Exception:
            return results

        for annot in annotations:
            try:
                type_name = annot.type[1] if annot.type else ""
                obj = self.doc.xref_object(annot.xref, compressed=True)
                if type_name != "Watermark" and not re.search(r"/Subtype\s*/Watermark\b", obj):
                    continue
                info = annot.info or {}
                description = (info.get("content") or "标准 PDF Watermark 注释").strip()
                results.append(
                    WatermarkInfo(
                        type_name="PDF 水印注释",
                        page_index=page_idx,
                        content=description,
                        rect=fitz.Rect(annot.rect),
                        is_text=False,
                        xref=annot.xref,
                        confidence=1.0,
                        source="watermark-annotation",
                    )
                )
            except Exception:
                continue
        return results

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
                    rgb = fitz.sRGB_to_rgb(span.get("color", 0))
                    luminance = self._color_luminance(tuple(channel / 255.0 for channel in rgb))
                    if luminance is not None and luminance >= 0.45:
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
                    source = "keyword-text" if keyword_text else "rotated-text"
                    if in_edge and not keyword_text and not rotated:
                        wm_type = "边缘文本"
                        source = "edge-text"
                    if footer_qr_text:
                        wm_type = "底部二维码说明文字"
                        source = "footer-qr-text"
                    results.append(
                        WatermarkInfo(
                            type_name=wm_type,
                            page_index=page_idx,
                            content=line_text,
                            rect=rect,
                            is_text=True,
                            source=source,
                        )
                    )
        return results

    def _scan_explicit_watermark_xrefs(self):
        """Locate image/Form objects carrying an explicit PDF watermark marker."""
        hits = set()
        for xref in range(1, self.doc.xref_length()):
            try:
                obj = self.doc.xref_object(xref, compressed=True)
            except Exception:
                continue
            if re.search(r"/Private\s*/Watermark\b", obj):
                hits.add(xref)
        return hits

    def _page_xobject_names(self, page, doc=None) -> Dict[int, List[str]]:
        """Return page resource names grouped by XObject xref without expanding duplicates."""
        result: Dict[int, List[str]] = {}
        pdf = doc or self.doc
        try:
            resource_type, resource_value = pdf.xref_get_key(page.xref, "Resources")
            if resource_type == "xref":
                match = re.search(r"(\d+)\s+0\s+R", resource_value)
                resource_text = pdf.xref_object(int(match.group(1))) if match else ""
            else:
                resource_text = resource_value or ""

            match = re.search(r"/XObject\s+(\d+)\s+0\s+R", resource_text)
            if match:
                xobject_text = pdf.xref_object(int(match.group(1)))
                for name, xref_text in re.findall(r"/([^\s/<>{}\[\]()]+)\s+(\d+)\s+0\s+R", xobject_text):
                    result.setdefault(int(xref_text), []).append(name)
        except Exception:
            pass
        return result

    def _page_content_text(self, page) -> str:
        chunks = []
        for content_xref in page.get_contents():
            try:
                chunks.append(self.doc.xref_stream(content_xref).decode("latin1", "ignore"))
            except Exception:
                continue
        return "\n".join(chunks)

    def _find_explicit_watermarks(self, page, page_idx: int) -> List[WatermarkInfo]:
        """Detect standard Watermark artifacts and producer-tagged image/Form XObjects."""
        results = []
        content = self._page_content_text(page)
        artifact_count = len(re.findall(r"/Subtype\s*/Watermark\b", content))
        if artifact_count:
            results.append(
                WatermarkInfo(
                    type_name="PDF 内置水印对象",
                    page_index=page_idx,
                    content=f"页面内容流明确标记为 Watermark（{artifact_count} 处）",
                    rect=fitz.Rect(page.rect),
                    is_text=False,
                    occurrences=artifact_count,
                    confidence=1.0,
                    source="explicit-watermark-artifact",
                )
            )

        if not self.explicit_watermark_xrefs:
            return results

        xobject_names = self._page_xobject_names(page)
        for xref in sorted(self.explicit_watermark_xrefs):
            for name in xobject_names.get(xref, []):
                call_count = len(re.findall(rf"/{re.escape(name)}\s+Do\b", content))
                if not call_count:
                    continue
                try:
                    subtype = self.doc.xref_get_key(xref, "Subtype")[1]
                    raw = self.doc.xref_stream(xref) if self.doc.xref_is_stream(xref) else str(xref).encode()
                except Exception:
                    subtype = ""
                    raw = str(xref).encode()
                source = "explicit-image-watermark" if "/Image" in subtype else "explicit-form-watermark"
                results.append(
                    WatermarkInfo(
                        type_name="PDF 内置图片水印" if source == "explicit-image-watermark" else "PDF 内置图形水印",
                        page_index=page_idx,
                        content=f"对象 {xref} /{name} 带 Private/Watermark 标记",
                        rect=fitz.Rect(page.rect),
                        is_text=False,
                        xref=xref,
                        fingerprint=hashlib.sha1(raw).hexdigest(),
                        occurrences=call_count,
                        confidence=1.0,
                        source=source,
                        resource_name=name,
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
        try:
            visible = page.get_image_info(hashes=True, xrefs=False)
        except Exception:
            return candidates

        # 1x1 / 1xN 图片常被 PDF 生成器当作线条或背景色使用，不应按水印候选展开。
        visible = [info for info in visible if info.get("width", 0) > 2 and info.get("height", 0) > 2]
        if not visible:
            return candidates

        try:
            resolved = page.get_image_info(hashes=True, xrefs=True)
        except Exception:
            resolved = visible
        xrefs = {}
        for info in resolved:
            key = (info.get("digest"), tuple(round(v, 3) for v in info.get("bbox", ())))
            xrefs[key] = int(info.get("xref", 0) or 0)

        resource_details = {}
        meaningful_xrefs = {xref for xref in xrefs.values() if xref > 0}
        if meaningful_xrefs:
            try:
                for info in page.get_images(full=True):
                    xref = int(info[0])
                    if xref not in meaningful_xrefs or xref in resource_details:
                        continue
                    container_xref = info[-1] if isinstance(info[-1], int) and info[-1] > 0 else None
                    resource_details[xref] = (container_xref, info[7])
            except Exception:
                pass

        seen = set()
        for info in visible:
            rect = fitz.Rect(info.get("bbox", (0, 0, 0, 0)))
            if rect.is_empty:
                continue
            digest = info.get("digest") or b""
            fingerprint = digest.hex() if isinstance(digest, bytes) else str(digest)
            key = (digest, tuple(round(v, 3) for v in rect))
            xref = xrefs.get(key, 0)
            container_xref, resource_name = resource_details.get(xref, (None, None))
            dedup_key = (xref, fingerprint, tuple(round(v, 2) for v in rect))
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            candidates.append(
                ImageCandidate(
                    page_index=page_idx,
                    xref=xref,
                    rect=rect,
                    fingerprint=fingerprint,
                    width=int(info.get("width", 0) or 0),
                    height=int(info.get("height", 0) or 0),
                    container_xref=container_xref,
                    resource_name=resource_name,
                )
            )
        return candidates

    def _is_background_image_candidate(self, cand: ImageCandidate) -> bool:
        page = self.doc[cand.page_index]
        rect = cand.rect
        if rect.is_empty:
            return False
        page_width = max(1.0, page.rect.width)
        page_height = max(1.0, page.rect.height)
        page_area = max(1.0, page_width * page_height)
        area_ratio = (rect.width * rect.height) / page_area
        large_cover = rect.width >= page_width * 0.55 and rect.height >= page_height * 0.55
        full_bleed = area_ratio >= 0.25 and rect.width >= page_width * 0.5 and rect.height >= page_height * 0.5
        return large_cover and (full_bleed or rect.y0 <= page_height * 0.12)

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

            # 兼容两类图形水印：
            # 1) 大面积、浅色/半透明矢量；
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

    @staticmethod
    def _rect_overlap_ratio(subject: fitz.Rect, other: fitz.Rect) -> float:
        """Return how much of ``subject`` is covered by ``other``."""
        if subject.is_empty or other.is_empty:
            return 0.0
        intersection = subject & other
        if intersection.is_empty:
            return 0.0
        return (intersection.width * intersection.height) / max(
            1.0, subject.width * subject.height
        )

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
                    is_background = self._is_background_image_candidate(cand)
                    watermarks.append(
                        WatermarkInfo(
                            type_name="背景图水印" if is_background else self._classify_repeated_image(cand),
                            page_index=cand.page_index,
                            content=self._describe_repeated_image(cand) if not is_background else "整页或大面积重复背景图可直接删除图像引用",
                            rect=cand.rect,
                            is_text=False,
                            xref=cand.xref,
                            container_xref=cand.container_xref,
                            resource_name=cand.resource_name,
                            fingerprint=cand.fingerprint,
                            occurrences=repeated_pages,
                            source="background-image" if is_background else "auto",
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

        # Deduplicate so repeated image marks do not flood the UI.
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

        if self._is_background_image_candidate(cand):
            return "背景图水印"

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
        if type_name == "背景图水印":
            return "整页或大面积重复背景图可直接删除图像引用"
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
            self.showinfo("提示", "请先打开 PDF 文件")
            return
        if not self.roi_bbox_pdf or not self.roi_points_pdf:
            self.showinfo("提示", "请先框选区域")
            return

        current_hits = self._scan_roi_on_page(self.current_page, self.roi_bbox_pdf, self.roi_points_pdf)
        if not current_hits:
            visual_score = self._roi_visual_ink_score(self.current_page, self.roi_bbox_pdf, self.roi_points_pdf)
            if visual_score <= 0.0:
                self.status_var.set("选区中未发现文本/图形候选")
                self.showinfo("提示", "选区中未发现明显水印")
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
        should_scan_all = self.askyesno(
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
                        resource_name=cand.resource_name,
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
