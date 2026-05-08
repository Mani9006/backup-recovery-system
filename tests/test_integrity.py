"""Tests for the integrity verification module."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from src.integrity import IntegrityError, IntegrityVerifier


class TestIntegrityVerifier:
    """Tests for IntegrityVerifier."""

    def test_init_default(self):
        """Test default initialization with SHA-256."""
        verifier = IntegrityVerifier()
        assert verifier.algorithm == "sha256"

    def test_init_sha512(self):
        """Test initialization with SHA-512."""
        verifier = IntegrityVerifier(algorithm="sha512")
        assert verifier.algorithm == "sha512"

    def test_init_invalid_algorithm(self):
        """Test that invalid algorithm raises error."""
        with pytest.raises(IntegrityError, match="Unsupported hash algorithm"):
            IntegrityVerifier(algorithm="invalid")

    def test_compute_checksum(self, tmp_path: Path):
        """Test checksum computation."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        verifier = IntegrityVerifier()
        checksum = verifier.compute_checksum(str(test_file))

        assert isinstance(checksum, str)
        assert len(checksum) == 64  # SHA-256 hex length
        # Verify it's a valid hex string
        int(checksum, 16)  # Should not raise

    def test_compute_checksum_sha512(self, tmp_path: Path):
        """Test SHA-512 checksum computation."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        verifier = IntegrityVerifier(algorithm="sha512")
        checksum = verifier.compute_checksum(str(test_file))

        assert len(checksum) == 128  # SHA-512 hex length

    def test_compute_checksum_missing_file(self, tmp_path: Path):
        """Test that missing file raises error."""
        verifier = IntegrityVerifier()
        with pytest.raises(IntegrityError, match="not found"):
            verifier.compute_checksum(str(tmp_path / "missing"))

    def test_verify_checksum(self, tmp_path: Path):
        """Test checksum verification."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        verifier = IntegrityVerifier()
        checksum = verifier.compute_checksum(str(test_file))

        # Correct checksum
        assert verifier.verify_checksum(str(test_file), checksum) is True

        # Wrong checksum
        wrong = "0" * 64
        assert verifier.verify_checksum(str(test_file), wrong) is False

    def test_write_checksum_file(self, tmp_path: Path):
        """Test writing a checksum file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")
        checksum_file = tmp_path / "test.txt.sha256"

        verifier = IntegrityVerifier()
        verifier.write_checksum_file(str(test_file), str(checksum_file))

        assert checksum_file.exists()
        content = checksum_file.read_text()
        assert "test.txt" in content
        assert len(content.split()[0]) == 64  # SHA-256 hex

    def test_verify_from_file(self, tmp_path: Path):
        """Test verification from a checksum file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")
        checksum_file = tmp_path / "test.txt.sha256"

        verifier = IntegrityVerifier()
        verifier.write_checksum_file(str(test_file), str(checksum_file))

        assert verifier.verify_from_file(str(test_file), str(checksum_file)) is True

    def test_verify_from_file_missing(self, tmp_path: Path):
        """Test verification with missing checksum file."""
        test_file = tmp_path / "test.txt"
        test_file.touch()

        verifier = IntegrityVerifier()
        with pytest.raises(IntegrityError, match="not found"):
            verifier.verify_from_file(str(test_file), str(tmp_path / "missing"))

    def test_check_zip_structure(self, tmp_path: Path):
        """Test zip archive integrity check."""
        zip_file = tmp_path / "test.zip"
        test_file = tmp_path / "content.txt"
        test_file.write_text("Hello, World!")

        with zipfile.ZipFile(zip_file, "w") as zf:
            zf.write(test_file, "content.txt")

        verifier = IntegrityVerifier()
        assert verifier._check_zip_structure(str(zip_file)) is True

    def test_check_corrupt_zip(self, tmp_path: Path):
        """Test detecting a corrupt zip file."""
        zip_file = tmp_path / "corrupt.zip"
        zip_file.write_bytes(b"PK\x03\x04\x00\x00invalid_data")

        verifier = IntegrityVerifier()
        assert verifier._check_zip_structure(str(zip_file)) is False

    def test_check_tar_structure(self, tmp_path: Path):
        """Test tar archive integrity check."""
        import tarfile

        tar_file = tmp_path / "test.tar"
        test_file = tmp_path / "content.txt"
        test_file.write_text("Hello, World!")

        with tarfile.open(tar_file, "w") as tf:
            tf.add(test_file, "content.txt")

        verifier = IntegrityVerifier()
        assert verifier._check_tar_structure(str(tar_file)) is True

    def test_batch_checksum(self, tmp_path: Path):
        """Test batch checksum computation."""
        # Create multiple files
        for i in range(3):
            (tmp_path / f"file{i}.txt").write_text(f"content {i}")

        output = tmp_path / "checksums.txt"

        verifier = IntegrityVerifier()
        verifier.batch_checksum(str(tmp_path), str(output))

        assert output.exists()
        lines = output.read_text().strip().split("\n")
        assert len(lines) == 3

    def test_batch_checksum_empty_dir(self, tmp_path: Path):
        """Test batch checksum on empty directory."""
        output = tmp_path / "checksums.txt"

        verifier = IntegrityVerifier()
        verifier.batch_checksum(str(tmp_path), str(output))

        assert output.exists()
        assert output.read_text().strip() == ""

    def test_verify_archive_with_checksum(self, tmp_path: Path):
        """Test full archive verification with checksum."""
        test_file = tmp_path / "backup.txt"
        test_file.write_bytes(b"test backup data")

        checksum_file = tmp_path / "backup.txt.sha256"
        verifier = IntegrityVerifier()
        verifier.write_checksum_file(str(test_file), str(checksum_file))

        result = verifier.verify_archive(str(test_file), str(checksum_file))
        assert result is True

    def test_verify_archive_no_checksum(self, tmp_path: Path):
        """Test archive verification without checksum file."""
        test_file = tmp_path / "backup.txt"
        test_file.write_text("test data")

        verifier = IntegrityVerifier()
        result = verifier.verify_archive(str(test_file))
        # Should still pass with structural check only
        assert result is True
