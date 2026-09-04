"""Content-preserving PDF decryption using pikepdf/libqpdf."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Literal

import pikepdf

PathLike = str | os.PathLike[str]
Password = str | bytes | None


class PdfDecryptionError(Exception):
    """Base class for errors raised by :func:`decrypt_pdf`."""


class InputFileError(PdfDecryptionError):
    """The input path cannot be read or is not a regular file."""


class OutputFileError(PdfDecryptionError):
    """The output path is invalid or cannot be written."""


class NotPdfError(PdfDecryptionError):
    """The input does not look like a PDF file."""


class PasswordRequiredError(PdfDecryptionError):
    """The PDF needs a non-empty password, but none was supplied."""


class IncorrectPasswordError(PdfDecryptionError):
    """The supplied user or owner password is incorrect."""


class CorruptPdfError(PdfDecryptionError):
    """The PDF is damaged beyond libqpdf's recovery capability."""


class UnsupportedEncryptionError(PdfDecryptionError):
    """The PDF uses an encryption/security handler unsupported by libqpdf."""


class PdfProcessingError(PdfDecryptionError):
    """libqpdf failed for a reason that could not be classified safely."""


class NotEncryptedError(PdfDecryptionError):
    """Raised only when ``require_encrypted=True`` and input is unencrypted."""


@dataclass(frozen=True, slots=True)
class EncryptionDetails:
    """Non-secret encryption facts read from the input PDF."""

    revision: int
    version: int
    key_bits: int
    stream_method: str
    string_method: str
    file_method: str


@dataclass(frozen=True, slots=True)
class DecryptResult:
    """A successful decryption/copy result."""

    input_path: Path
    output_path: Path
    was_encrypted: bool
    password_kind: Literal["user", "owner"] | None
    encryption: EncryptionDetails | None
    page_count: int
    had_digital_signatures: bool
    warnings: tuple[str, ...]


_UNSUPPORTED_MARKERS = (
    "unsupported encryption",
    "unsupported security handler",
    "unsupported crypt filter",
    "unknown crypt filter",
    "unknown encryption",
    "aes-gcm",
    "not implemented for encrypted",
)

_CORRUPT_MARKERS = (
    "damaged pdf",
    "unable to find trailer",
    "unable to find xref",
    "unable to find page tree",
    "unable to find /root",
    "root dictionary",
    "xref stream",
    "unexpected eof",
    "not a pdf file",
    "can't find startxref",
    "invalid pdf",
)


def _looks_like_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            return b"%PDF-" in stream.read(1024)
    except OSError as exc:
        raise InputFileError(f"无法读取输入文件: {path}: {exc}") from exc


def _enum_name(value: object) -> str:
    name = getattr(value, "name", None)
    return str(name if name is not None else value)


def _encryption_details(pdf: pikepdf.Pdf) -> EncryptionDetails:
    info = pdf.encryption
    return EncryptionDetails(
        revision=int(info.R),
        version=int(info.V),
        key_bits=int(info.bits),
        stream_method=_enum_name(info.stream_method),
        string_method=_enum_name(info.string_method),
        file_method=_enum_name(info.file_method),
    )


def _has_digital_signatures(pdf: pikepdf.Pdf) -> bool:
    root = pdf.Root
    if "/Perms" in root:
        return True
    acroform = root.get("/AcroForm")
    if acroform is None:
        return False
    fields = acroform.get("/Fields", [])
    pending = list(fields)
    visited: set[tuple[int, int]] = set()
    while pending:
        field = pending.pop()
        objgen = tuple(field.objgen)
        if objgen != (0, 0):
            if objgen in visited:
                continue
            visited.add(objgen)
        if str(field.get("/FT", "")) == "/Sig":
            return True
        pending.extend(field.get("/Kids", []))
    return False


def _classify_pdf_error(exc: pikepdf.PdfError, *, phase: str) -> PdfDecryptionError:
    message = str(exc)
    folded = message.casefold()
    if any(marker in folded for marker in _UNSUPPORTED_MARKERS):
        return UnsupportedEncryptionError(f"不支持的 PDF 加密方式: {message}")
    if any(marker in folded for marker in _CORRUPT_MARKERS):
        return CorruptPdfError(f"PDF 文件损坏（{phase}）: {message}")
    return PdfProcessingError(f"PDF 处理失败（{phase}）: {message}")


def _validate_paths(input_path: PathLike, output_path: PathLike) -> tuple[Path, Path]:
    source = Path(input_path).expanduser().resolve(strict=False)
    target = Path(output_path).expanduser().resolve(strict=False)
    if not source.exists():
        raise InputFileError(f"输入文件不存在: {source}")
    if not source.is_file():
        raise InputFileError(f"输入路径不是普通文件: {source}")
    if source == target:
        raise OutputFileError("输出路径不能与输入路径相同；请输出为一个新 PDF")
    if not target.parent.exists() or not target.parent.is_dir():
        raise OutputFileError(f"输出目录不存在: {target.parent}")
    return source, target


def is_pdf_encrypted(input_path: PathLike) -> bool:
    """Return whether a PDF has an encryption dictionary without guessing a password."""

    source = Path(input_path).expanduser().resolve(strict=False)
    if not source.exists():
        raise InputFileError(f"输入文件不存在: {source}")
    if not source.is_file():
        raise InputFileError(f"输入路径不是普通文件: {source}")
    if not _looks_like_pdf(source):
        raise NotPdfError(f"输入文件没有有效的 PDF 文件头: {source}")
    try:
        with pikepdf.Pdf.open(
            source, password="", suppress_warnings=True, attempt_recovery=True
        ) as pdf:
            return bool(pdf.is_encrypted)
    except pikepdf.PasswordError:
        return True
    except pikepdf.PdfError as exc:
        raise _classify_pdf_error(exc, phase="检查加密状态") from exc
    except OSError as exc:
        raise InputFileError(f"无法打开输入文件: {source}: {exc}") from exc


def decrypt_pdf(
    input_path: PathLike,
    output_path: PathLike,
    password: Password = None,
    *,
    require_encrypted: bool = False,
    check_syntax: bool = True,
    progress: Callable[[int], None] | None = None,
) -> DecryptResult:
    """Open with one supplied password and atomically write an unencrypted PDF."""

    source, target = _validate_paths(input_path, output_path)
    if not _looks_like_pdf(source):
        raise NotPdfError(f"输入文件没有有效的 PDF 文件头: {source}")
    if password is not None and not isinstance(password, (str, bytes)):
        raise TypeError("password 必须是 str、bytes 或 None")

    supplied_password: str | bytes = "" if password is None else password
    temp_name: str | None = None
    try:
        try:
            pdf = pikepdf.Pdf.open(
                source,
                password=supplied_password,
                suppress_warnings=True,
                attempt_recovery=True,
            )
        except pikepdf.PasswordError as exc:
            if password is None:
                raise PasswordRequiredError("PDF 已加密，需要提供用户密码或所有者密码") from exc
            raise IncorrectPasswordError("提供的 PDF 用户密码或所有者密码不正确") from exc
        except pikepdf.PdfError as exc:
            raise _classify_pdf_error(exc, phase="打开") from exc
        except OSError as exc:
            raise InputFileError(f"无法打开输入文件: {source}: {exc}") from exc

        with pdf:
            was_encrypted = bool(pdf.is_encrypted)
            if require_encrypted and not was_encrypted:
                raise NotEncryptedError("输入 PDF 未加密")
            details = _encryption_details(pdf) if was_encrypted else None
            was_linearized = bool(pdf.is_linearized)
            if was_encrypted and bool(pdf.owner_password_matched):
                password_kind: Literal["user", "owner"] | None = "owner"
            elif was_encrypted and bool(pdf.user_password_matched):
                password_kind = "user"
            else:
                password_kind = None
            try:
                page_count = len(pdf.pages)
                had_signatures = _has_digital_signatures(pdf)
                syntax_warnings = tuple(pdf.check_pdf_syntax()) if check_syntax else ()
                parser_warnings = tuple(str(item) for item in pdf.get_warnings())
            except pikepdf.PdfError as exc:
                raise _classify_pdf_error(exc, phase="完整性检查") from exc

            fd, temp_name = tempfile.mkstemp(
                prefix=f".{target.stem}.", suffix=".tmp.pdf", dir=target.parent
            )
            os.close(fd)
            try:
                pdf.save(
                    temp_name,
                    encryption=False,
                    object_stream_mode=pikepdf.ObjectStreamMode.preserve,
                    normalize_content=False,
                    recompress_flate=False,
                    fix_metadata_version=False,
                    linearize=was_linearized,
                    progress=progress,
                )
            except pikepdf.PdfError as exc:
                raise _classify_pdf_error(exc, phase="写入") from exc
            except OSError as exc:
                raise OutputFileError(f"无法写入输出文件: {target}: {exc}") from exc

        try:
            with pikepdf.Pdf.open(temp_name, suppress_warnings=True) as check:
                if check.is_encrypted:
                    raise PdfProcessingError("输出验证失败：生成的 PDF 仍然带有加密")
                if len(check.pages) != page_count:
                    raise PdfProcessingError(
                        f"输出验证失败：页数从 {page_count} 变为 {len(check.pages)}"
                    )
        except pikepdf.PdfError as exc:
            raise _classify_pdf_error(exc, phase="输出验证") from exc

        try:
            os.replace(temp_name, target)
            temp_name = None
        except OSError as exc:
            raise OutputFileError(f"无法提交输出文件: {target}: {exc}") from exc
        warnings = tuple(dict.fromkeys((*syntax_warnings, *parser_warnings)))
        return DecryptResult(
            input_path=source,
            output_path=target,
            was_encrypted=was_encrypted,
            password_kind=password_kind,
            encryption=details,
            page_count=page_count,
            had_digital_signatures=had_signatures,
            warnings=warnings,
        )
    finally:
        if temp_name is not None:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
