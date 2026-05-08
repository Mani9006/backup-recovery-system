"""Tests for the compression module."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.compression import CompressionError, CompressionManager


class TestCompressionManager:
    """Tests for CompressionManager."""

    def _create_test_file(self, path: Path, size: int = 1000) -> None:
        """Create a test file with random content."""
        content = b"ABCDEFGHIJ" * (size // 10 + 1)
        path.write_bytes(content[:size])

    def test_init_default(self):
        """Test default initialization."""
        mgr = CompressionManager()
        assert mgr.method.value == "gzip"
        assert mgr.level == 6

    def test_init_custom(self):
        """Test initialization with custom parameters."""
        mgr = CompressionManager(method="zip", level=9)
        assert mgr.method.value == "zip"
        assert mgr.level == 9

    def test_init_invalid_method(self):
        """Test that invalid method raises error."""
        with pytest.raises(CompressionError, match="Invalid compression method"):
            CompressionManager(method="invalid")

    def test_init_invalid_level(self):
        """Test that invalid level raises error."""
        with pytest.raises(CompressionError, match="between 1 and 9"):
            CompressionManager(level=0)

    def test_compress_gzip(self, tmp_path: Path):
        """Test gzip compression."""
        source = tmp_path / "test.txt"
        self._create_test_file(source)
        dest = tmp_path / "test.txt.gz"

        mgr = CompressionManager(method="gzip")
        mgr.compress_gzip(str(source), str(dest))

        assert dest.exists()
        assert dest.stat().st_size > 0
        assert dest.stat().st_size < source.stat().st_size

    def test_compress_gzip_missing_source(self, tmp_path: Path):
        """Test that compression fails with missing source."""
        mgr = CompressionManager(method="gzip")
        with pytest.raises(CompressionError, match="not found"):
            mgr.compress_gzip(str(tmp_path / "missing"), str(tmp_path / "out.gz"))

    def test_compress_bzip2(self, tmp_path: Path):
        """Test bzip2 compression."""
        source = tmp_path / "test.txt"
        self._create_test_file(source)
        dest = tmp_path / "test.txt.bz2"

        mgr = CompressionManager(method="bzip2")
        mgr.compress_bzip2(str(source), str(dest))

        assert dest.exists()
        assert dest.stat().st_size > 0

    def test_compress_zip(self, tmp_path: Path):
        """Test zip compression of a file."""
        source = tmp_path / "test.txt"
        self._create_test_file(source)
        dest = tmp_path / "test.zip"

        mgr = CompressionManager(method="zip")
        mgr.compress_zip(str(source), str(dest))

        assert dest.exists()
        assert dest.stat().st_size > 0

    def test_compress_zip_directory(self, tmp_path: Path):
        """Test zip compression of a directory."""
        source = tmp_path / "testdir"
        source.mkdir()
        (source / "file1.txt").write_text("Hello")
        (source / "file2.txt").write_text("World")

        dest = tmp_path / "test.zip"

        mgr = CompressionManager(method="zip")
        mgr.compress_zip(str(source), str(dest))

        assert dest.exists()

    def test_compress_none(self, tmp_path: Path):
        """Test no compression (just copy)."""
        source = tmp_path / "test.txt"
        self._create_test_file(source)
        dest = tmp_path / "test_copy.txt"

        mgr = CompressionManager(method="none")
        mgr.compress(str(source), str(dest))

        assert dest.exists()
        assert dest.stat().st_size == source.stat().st_size

    def test_decompress_gzip(self, tmp_path: Path):
        """Test gzip decompression."""
        source = tmp_path / "test.txt"
        original = b"Hello, World! This is test content."
        source.write_bytes(original)
        compressed = tmp_path / "test.txt.gz"
        decompressed = tmp_path / "test_out.txt"

        mgr = CompressionManager(method="gzip")
        mgr.compress_gzip(str(source), str(compressed))
        mgr.decompress_gzip(str(compressed), str(decompressed))

        assert decompressed.read_bytes() == original

    def test_decompress_bzip2(self, tmp_path: Path):
        """Test bzip2 decompression."""
        source = tmp_path / "test.txt"
        original = b"Hello, World!"
        source.write_bytes(original)
        compressed = tmp_path / "test.txt.bz2"
        decompressed = tmp_path / "test_out.txt"

        mgr = CompressionManager(method="bzip2")
        mgr.compress_bzip2(str(source), str(compressed))
        mgr.decompress_bzip2(str(compressed), str(decompressed))

        assert decompressed.read_bytes() == original

    def test_decompress_zip(self, tmp_path: Path):
        """Test zip extraction."""
        source = tmp_path / "test.txt"
        original = b"Hello, World!"
        source.write_bytes(original)
        compressed = tmp_path / "test.zip"
        extract_dir = tmp_path / "extracted"

        mgr = CompressionManager(method="zip")
        mgr.compress_zip(str(source), str(compressed))
        mgr.decompress_zip(str(compressed), str(extract_dir))

        assert extract_dir.exists()
        extracted_files = list(extract_dir.iterdir())
        assert len(extracted_files) > 0

    def test_auto_decompress(self, tmp_path: Path):
        """Test auto-detection and decompression."""
        source = tmp_path / "test.txt"
        original = b"Auto-decompress test content"
        source.write_bytes(original)
        compressed = tmp_path / "test.txt.gz"
        decompressed = tmp_path / "test_out.txt"

        mgr = CompressionManager()
        mgr.compress_gzip(str(source), str(compressed))
        mgr.decompress(str(compressed), str(decompressed))

        assert decompressed.read_bytes() == original

    def test_detect_format(self, tmp_path: Path):
        """Test compression format detection."""
        mgr = CompressionManager()

        # Create files with different formats
        gz_file = tmp_path / "test.gz"
        bz2_file = tmp_path / "test.bz2"
        zip_file = tmp_path / "test.zip"
        plain_file = tmp_path / "test.txt"

        # Write magic numbers for testing
        gz_file.write_bytes(b"\x1f\x8b\x08\x00")
        bz2_file.write_bytes(b"BZh9")
        zip_file.write_bytes(b"PK\x03\x04")
        plain_file.write_bytes(b"plain text")

        assert mgr.detect_format(str(gz_file)) == "gzip"
        assert mgr.detect_format(str(bz2_file)) == "bzip2"
        assert mgr.detect_format(str(zip_file)) == "zip"
        assert mgr.detect_format(str(plain_file)) == "none"

    def test_detect_format_nonexistent(self):
        """Test format detection on nonexistent file."""
        mgr = CompressionManager()
        assert mgr.detect_format("/nonexistent/file.gz") is None
