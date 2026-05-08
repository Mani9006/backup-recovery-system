"""Compression manager for backup archives.

Supports gzip, bzip2, and zip compression formats with configurable
compression levels and streaming for large files.
"""

from __future__ import annotations

import bz2
import gzip
import logging
import os
import shutil
import tarfile
import zipfile
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CompressionError(Exception):
    """Raised when a compression operation fails."""

    pass


class CompressionMethod(Enum):
    """Supported compression methods."""

    GZIP = "gzip"
    BZIP2 = "bzip2"
    ZIP = "zip"
    NONE = "none"


class CompressionManager:
    """Manages compression and decompression of backup files.

    Supports multiple compression algorithms with configurable levels.
    Provides both file-based and stream-based compression interfaces.
    """

    # Default compression level (1-9, where 9 is best compression)
    DEFAULT_LEVEL = 6

    def __init__(
        self,
        method: str = "gzip",
        level: int = DEFAULT_LEVEL,
    ) -> None:
        """Initialize the compression manager.

        Args:
            method: Compression method (gzip, bzip2, zip, none).
            level: Compression level from 1 (fastest) to 9 (best).

        Raises:
            CompressionError: If the compression method is invalid.
        """
        try:
            self.method = CompressionMethod(method.lower())
        except ValueError:
            valid = [m.value for m in CompressionMethod]
            raise CompressionError(
                f"Invalid compression method '{method}'. "
                f"Must be one of: {', '.join(valid)}"
            )

        if not 1 <= level <= 9:
            raise CompressionError(
                f"Compression level must be between 1 and 9, got {level}"
            )
        self.level = level

        logger.debug(
            "CompressionManager initialized (method=%s, level=%d)",
            self.method.value,
            self.level,
        )

    def compress_gzip(self, source: str, destination: str) -> None:
        """Compress a file using gzip.

        Args:
            source: Path to the source file.
            destination: Path to the output compressed file.

        Raises:
            CompressionError: If the source file does not exist or compression fails.
        """
        source_path = Path(source)
        if not source_path.exists():
            raise CompressionError(f"Source file not found: {source}")

        try:
            with open(source, "rb") as f_in:
                with gzip.open(destination, "wb", compresslevel=self.level) as f_out:
                    shutil.copyfileobj(f_in, f_out)

            original_size = source_path.stat().st_size
            compressed_size = Path(destination).stat().st_size
            ratio = compressed_size / original_size if original_size > 0 else 0
            logger.info(
                "Gzip compressed: %s -> %s (%.1f%% of original)",
                source,
                destination,
                ratio * 100,
            )

        except OSError as exc:
            raise CompressionError(f"Gzip compression failed: {exc}") from exc

    def compress_bzip2(self, source: str, destination: str) -> None:
        """Compress a file using bzip2.

        Args:
            source: Path to the source file.
            destination: Path to the output compressed file.

        Raises:
            CompressionError: If the source file does not exist or compression fails.
        """
        source_path = Path(source)
        if not source_path.exists():
            raise CompressionError(f"Source file not found: {source}")

        try:
            with open(source, "rb") as f_in:
                with bz2.open(destination, "wb", compresslevel=self.level) as f_out:
                    shutil.copyfileobj(f_in, f_out)

            original_size = source_path.stat().st_size
            compressed_size = Path(destination).stat().st_size
            ratio = compressed_size / original_size if original_size > 0 else 0
            logger.info(
                "Bzip2 compressed: %s -> %s (%.1f%% of original)",
                source,
                destination,
                ratio * 100,
            )

        except OSError as exc:
            raise CompressionError(f"Bzip2 compression failed: {exc}") from exc

    def compress_zip(self, source: str, destination: str) -> None:
        """Compress a file or directory into a zip archive.

        Args:
            source: Path to the source file or directory.
            destination: Path to the output zip file.

        Raises:
            CompressionError: If the source does not exist or compression fails.
        """
        source_path = Path(source)
        if not source_path.exists():
            raise CompressionError(f"Source not found: {source}")

        try:
            compression = zipfile.ZIP_DEFLATED
            with zipfile.ZipFile(destination, "w", compression=compression) as zf:
                if source_path.is_file():
                    arcname = source_path.name
                    zf.write(source, arcname)
                    logger.debug("Added file to zip: %s", arcname)
                elif source_path.is_dir():
                    for file_path in source_path.rglob("*"):
                        if file_path.is_file():
                            arcname = str(file_path.relative_to(source_path))
                            zf.write(file_path, arcname)
                            logger.debug("Added to zip: %s", arcname)

            logger.info(
                "Zip archive created: %s (%.2f MB)",
                destination,
                Path(destination).stat().st_size / (1024 * 1024),
            )

        except OSError as exc:
            raise CompressionError(f"Zip compression failed: {exc}") from exc

    def compress(self, source: str, destination: str) -> None:
        """Compress using the configured method.

        Args:
            source: Path to the source file.
            destination: Path to the output compressed file.
        """
        if self.method == CompressionMethod.GZIP:
            self.compress_gzip(source, destination)
        elif self.method == CompressionMethod.BZIP2:
            self.compress_bzip2(source, destination)
        elif self.method == CompressionMethod.ZIP:
            self.compress_zip(source, destination)
        elif self.method == CompressionMethod.NONE:
            # Just copy the file
            shutil.copy2(source, destination)
            logger.info("No compression applied (copied): %s -> %s", source, destination)

    def decompress_gzip(self, source: str, destination: str) -> None:
        """Decompress a gzip file.

        Args:
            source: Path to the gzip compressed file.
            destination: Path to the output decompressed file.

        Raises:
            CompressionError: If decompression fails.
        """
        try:
            with gzip.open(source, "rb") as f_in:
                with open(destination, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            logger.info("Gzip decompressed: %s -> %s", source, destination)
        except OSError as exc:
            raise CompressionError(f"Gzip decompression failed: {exc}") from exc

    def decompress_bzip2(self, source: str, destination: str) -> None:
        """Decompress a bzip2 file.

        Args:
            source: Path to the bzip2 compressed file.
            destination: Path to the output decompressed file.

        Raises:
            CompressionError: If decompression fails.
        """
        try:
            with bz2.open(source, "rb") as f_in:
                with open(destination, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            logger.info("Bzip2 decompressed: %s -> %s", source, destination)
        except OSError as exc:
            raise CompressionError(f"Bzip2 decompression failed: {exc}") from exc

    def decompress_zip(self, source: str, destination: str) -> None:
        """Extract a zip archive.

        Args:
            source: Path to the zip file.
            destination: Directory to extract to.

        Raises:
            CompressionError: If extraction fails.
        """
        try:
            dest_path = Path(destination)
            dest_path.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(source, "r") as zf:
                zf.extractall(destination)
            logger.info(
                "Zip extracted: %s -> %s (%d files)",
                source,
                destination,
                len(zf.namelist()),
            )
        except OSError as exc:
            raise CompressionError(f"Zip extraction failed: {exc}") from exc

    def decompress(self, source: str, destination: str) -> None:
        """Auto-detect compression format and decompress.

        Args:
            source: Path to the compressed file.
            destination: Path to the output file or directory.

        Raises:
            CompressionError: If the format cannot be detected or decompression fails.
        """
        source_path = Path(source)
        if not source_path.exists():
            raise CompressionError(f"Source file not found: {source}")

        suffixes = source_path.suffixes

        if ".gz" in suffixes or ".gzip" in suffixes:
            self.decompress_gzip(source, destination)
        elif ".bz2" in suffixes or ".bzip2" in suffixes:
            self.decompress_bzip2(source, destination)
        elif ".zip" in suffixes:
            self.decompress_zip(source, destination)
        elif source_path.suffix == ".gz":
            self.decompress_gzip(source, destination)
        elif source_path.suffix == ".bz2":
            self.decompress_bzip2(source, destination)
        elif source_path.suffix == ".zip":
            self.decompress_zip(source, destination)
        else:
            # Unknown format, try gzip first, then copy
            try:
                self.decompress_gzip(source, destination)
            except Exception:
                logger.warning(
                    "Unknown compression format for %s, copying as-is", source
                )
                shutil.copy2(source, destination)

    @staticmethod
    def detect_format(file_path: str) -> Optional[str]:
        """Detect the compression format of a file.

        Args:
            file_path: Path to the file to check.

        Returns:
            Detected format name (gzip, bzip2, zip, none) or None.
        """
        path = Path(file_path)
        if not path.exists():
            return None

        suffixes = [s.lower() for s in path.suffixes]

        if ".gz" in suffixes or path.suffix.lower() == ".gz":
            return "gzip"
        if ".bz2" in suffixes or path.suffix.lower() == ".bz2":
            return "bzip2"
        if ".zip" in suffixes or path.suffix.lower() == ".zip":
            return "zip"

        # Try magic number detection
        try:
            with open(file_path, "rb") as f:
                magic = f.read(4)
            if magic[:2] == b"\x1f\x8b":
                return "gzip"
            if magic[:3] == b"BZh":
                return "bzip2"
            if magic[:4] == b"PK\x03\x04":
                return "zip"
        except OSError:
            pass

        return "none"
