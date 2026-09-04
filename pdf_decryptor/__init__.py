"""Authorized, local PDF decryption.

This package intentionally contains no password discovery or guessing features.
"""

from .core import (
    CorruptPdfError,
    DecryptResult,
    EncryptionDetails,
    IncorrectPasswordError,
    InputFileError,
    NotEncryptedError,
    NotPdfError,
    OutputFileError,
    PasswordRequiredError,
    PdfDecryptionError,
    PdfProcessingError,
    UnsupportedEncryptionError,
    decrypt_pdf,
    is_pdf_encrypted,
)

__all__ = [
    "CorruptPdfError",
    "DecryptResult",
    "EncryptionDetails",
    "IncorrectPasswordError",
    "InputFileError",
    "NotEncryptedError",
    "NotPdfError",
    "OutputFileError",
    "PasswordRequiredError",
    "PdfDecryptionError",
    "PdfProcessingError",
    "UnsupportedEncryptionError",
    "decrypt_pdf",
    "is_pdf_encrypted",
]

