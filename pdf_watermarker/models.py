"""Data models shared by detection, processing, and the GUI."""

import hashlib
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import pymupdf as fitz

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
    resource_name: Optional[str] = None
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
    resource_name: Optional[str] = None


@dataclass
class VectorCandidate:
    page_index: int
    rect: fitz.Rect
    fingerprint: str
    opacity: float
    color_hint: str
