import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pikepdf
import pymupdf as fitz

from pdf_decryptor import (
    IncorrectPasswordError,
    PasswordRequiredError,
    decrypt_pdf,
    is_pdf_encrypted,
)
from pdf_watermarker.app import PDFWatermarkRemover
from pdf_watermarker.models import WatermarkInfo
from pdf_watermarker.ui_preferences import PreferencesMixin
from pdf_watermarker.version import APP_DISPLAY_NAME, APP_VERSION


class PdfWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="pdfwm_tests_")
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _plain_pdf(self, name="plain.pdf") -> Path:
        path = self.root / name
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), "DRAFT WATERMARK")
        page.insert_text((72, 140), "Body text must remain")
        doc.save(path)
        doc.close()
        return path

    def _encrypted_pdf(self, user_password: str, name="locked.pdf") -> Path:
        source = self._plain_pdf("encryption_source.pdf")
        target = self.root / name
        with pikepdf.open(source) as pdf:
            pdf.save(
                target,
                encryption=pikepdf.Encryption(
                    user=user_password,
                    owner="owner-password",
                    R=6,
                ),
            )
        return target

    def test_current_version(self):
        self.assertEqual(APP_VERSION, 15)
        self.assertEqual(APP_DISPLAY_NAME, "PDF 水印去除工具 Mark15")

    def test_english_runtime_translation(self):
        preferences = PreferencesMixin()
        preferences.language_code = "en"
        translated = preferences.translate_runtime("页面 2: 关键词文本 - 无内容描述")
        self.assertEqual(translated, "Page 2: Keyword text - no description")
        self.assertEqual(
            preferences.translate_runtime("预览已生成：可继续框选、分析，满意后再保存"),
            "Preview ready; continue selecting or analyzing before saving",
        )

    def test_text_redaction_saves_an_unencrypted_pdf(self):
        source = self._plain_pdf()
        target = self.root / "clean.pdf"
        mark = WatermarkInfo(
            page_index=0,
            type_name="文本水印",
            content="DRAFT WATERMARK",
            rect=fitz.Rect(65, 50, 220, 85),
            is_text=True,
        )
        remover = PDFWatermarkRemover.__new__(PDFWatermarkRemover)
        remover._apply_watermarks_to_pdf(str(source), str(target), [mark])

        self.assertFalse(is_pdf_encrypted(target))
        with fitz.open(target) as result:
            text = result[0].get_text()
            self.assertEqual(result.pagelayout, "OneColumn")
        self.assertNotIn("DRAFT WATERMARK", text)
        self.assertIn("Body text must remain", text)

    def test_save_dialog_starts_in_source_pdf_folder(self):
        source_folder = self.root / "source"
        source_folder.mkdir()
        source = source_folder / "input.pdf"

        remover = PDFWatermarkRemover.__new__(PDFWatermarkRemover)
        remover.doc = object()
        remover.pdf_path = str(self.root / "preview.pdf")
        remover.original_pdf_path = str(source)
        remover.is_preview_session = True
        remover.language_code = "en"
        remover.translate_runtime = lambda value: value

        with mock.patch("pdf_watermarker.app.filedialog.asksaveasfilename", return_value="") as dialog:
            remover.save_current_result()

        self.assertEqual(dialog.call_args.kwargs["initialdir"], str(source_folder.resolve()))
        self.assertEqual(dialog.call_args.kwargs["initialfile"], "input_watermark_removed.pdf")

    def test_keyword_detection_finds_edge_watermark(self):
        source = self._plain_pdf()
        remover = PDFWatermarkRemover.__new__(PDFWatermarkRemover)
        with fitz.open(source) as doc:
            matches = remover._find_text_watermarks(doc[0], 0)
        self.assertTrue(any(mark.content == "DRAFT WATERMARK" for mark in matches))

    def test_known_password_unlock(self):
        source = self._encrypted_pdf("secret")
        target = self.root / "unlocked.pdf"
        self.assertTrue(is_pdf_encrypted(source))
        with self.assertRaises(IncorrectPasswordError):
            decrypt_pdf(source, target, "wrong")

        result = decrypt_pdf(source, target, "secret")
        self.assertTrue(result.was_encrypted)
        self.assertFalse(is_pdf_encrypted(target))
        self.assertEqual(result.page_count, 1)

    def test_empty_user_password_unlock(self):
        source = self._encrypted_pdf("", "owner_only.pdf")
        target = self.root / "owner_only_unlocked.pdf"
        result = decrypt_pdf(source, target)
        self.assertTrue(result.was_encrypted)
        self.assertFalse(is_pdf_encrypted(target))

    def test_nonempty_password_is_required(self):
        source = self._encrypted_pdf("secret", "password_required.pdf")
        with self.assertRaises(PasswordRequiredError):
            decrypt_pdf(source, self.root / "must_not_exist.pdf")


if __name__ == "__main__":
    unittest.main()
