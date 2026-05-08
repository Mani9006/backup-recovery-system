"""Integrity verification for backup files.

Provides checksum computation and verification using multiple
hash algorithms, plus archive integrity checking.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tarfile
import zipfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class IntegrityError(Exception):
    """Raised when an integrity verification fails."""

    pass


class IntegrityVerifier:
    """Manages integrity verification for backup files.

    Computes and verifies checksums using SHA-256, SHA-512, or MD5.
    Can also perform structural integrity checks on tar and zip archives.
    """

    # Supported hash algorithms
    SUPPORTED_ALGORITHMS = {
        "sha256": hashlib.sha256,
        "sha512": hashlib.sha512,
        "md5": hashlib.md5,
    }

    def __init__(self, algorithm: str = "sha256") -> None:
        """Initialize the integrity verifier.

        Args:
            algorithm: Hash algorithm to use (sha256, sha512, md5).

        Raises:
            IntegrityError: If the algorithm is not supported.
        """
        algorithm = algorithm.lower()
        if algorithm not in self.SUPPORTED_ALGORITHMS:
            valid = ", ".join(self.SUPPORTED_ALGORITHMS.keys())
            raise IntegrityError(
                f"Unsupported hash algorithm '{algorithm}'. "
                f"Must be one of: {valid}"
            )
        self.algorithm = algorithm
        self._hash_func = self.SUPPORTED_ALGORITHMS[algorithm]

    def compute_checksum(self, file_path: str) -> str:
        """Compute the checksum of a file.

        Reads the file in chunks to handle large files efficiently.

        Args:
            file_path: Path to the file.

        Returns:
            Hexadecimal checksum string.

        Raises:
            IntegrityError: If the file cannot be read.
        """
        path = Path(file_path)
        if not path.exists():
            raise IntegrityError(f"File not found: {file_path}")

        hasher = self._hash_func()

        try:
            bytes_read = 0
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(8192)  # 8KB chunks
                    if not chunk:
                        break
                    hasher.update(chunk)
                    bytes_read += len(chunk)

            checksum = hasher.hexdigest()
            logger.debug(
                "Checksum computed (%s): %s... for %s (%d bytes)",
                self.algorithm,
                checksum[:16],
                file_path,
                bytes_read,
            )
            return checksum

        except OSError as exc:
            raise IntegrityError(f"Failed to read file for checksum: {exc}") from exc

    def verify_checksum(self, file_path: str, expected_checksum: str) -> bool:
        """Verify a file against an expected checksum.

        Args:
            file_path: Path to the file to verify.
            expected_checksum: Expected checksum value.

        Returns:
            True if the checksum matches, False otherwise.
        """
        actual = self.compute_checksum(file_path)
        match = actual.lower() == expected_checksum.lower()

        if match:
            logger.info("Checksum verified for %s", file_path)
        else:
            logger.error(
                "Checksum mismatch for %s:\n  Expected: %s\n  Actual:   %s",
                file_path,
                expected_checksum,
                actual,
            )

        return match

    def verify_from_file(self, file_path: str, checksum_file: str) -> bool:
        """Verify a file using a checksum file in the format 'CHECKSUM  FILENAME'.

        Args:
            file_path: Path to the file to verify.
            checksum_file: Path to the checksum file.

        Returns:
            True if the checksum matches.

        Raises:
            IntegrityError: If the checksum file cannot be read.
        """
        if not Path(checksum_file).exists():
            raise IntegrityError(f"Checksum file not found: {checksum_file}")

        try:
            with open(checksum_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue

                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        expected_checksum = parts[0]
                        return self.verify_checksum(file_path, expected_checksum)

            raise IntegrityError(f"No valid checksum found in {checksum_file}")

        except OSError as exc:
            raise IntegrityError(f"Failed to read checksum file: {exc}") from exc

    def write_checksum_file(self, file_path: str, checksum_file: str) -> None:
        """Write a checksum file for a given file.

        Args:
            file_path: Path to the file to checksum.
            checksum_file: Path where the checksum file will be written.
        """
        checksum = self.compute_checksum(file_path)
        filename = Path(file_path).name

        with open(checksum_file, "w", encoding="utf-8") as f:
            f.write(f"{checksum}  {filename}\n")

        logger.info("Checksum file written: %s", checksum_file)

    def verify_archive(self, archive_path: str, checksum_file: Optional[str] = None) -> bool:
        """Verify the integrity of an archive file.

        Performs both checksum verification (if a checksum file is provided)
        and structural integrity checks on the archive format.

        Args:
            archive_path: Path to the archive file.
            checksum_file: Optional path to a checksum file.

        Returns:
            True if the archive passes all integrity checks.
        """
        path = Path(archive_path)
        if not path.exists():
            logger.error("Archive not found: %s", archive_path)
            return False

        results = []

        # Checksum verification
        if checksum_file and Path(checksum_file).exists():
            try:
                checksum_valid = self.verify_from_file(archive_path, checksum_file)
                results.append(checksum_valid)
                if not checksum_valid:
                    logger.error("Archive checksum verification failed")
            except IntegrityError as exc:
                logger.warning("Checksum verification skipped: %s", exc)
        else:
            logger.debug("No checksum file provided, skipping checksum verification")

        # Structural integrity check
        try:
            structural_valid = self._check_archive_structure(archive_path)
            results.append(structural_valid)
        except Exception as exc:
            logger.error("Archive structural check failed: %s", exc)
            results.append(False)

        return all(results) if results else True

    def _check_archive_structure(self, archive_path: str) -> bool:
        """Check the structural integrity of an archive.

        Args:
            archive_path: Path to the archive file.

        Returns:
            True if the archive structure is valid.
        """
        path = Path(archive_path)
        suffix = path.suffix.lower()

        try:
            if suffix == ".zip":
                return self._check_zip_structure(archive_path)
            elif suffix in (".tar", ".gz", ".bz2", ".tgz"):
                return self._check_tar_structure(archive_path)
            elif suffix == ".json":
                # Metadata files - just check if valid JSON
                import json
                with open(archive_path, "r", encoding="utf-8") as f:
                    json.load(f)
                return True
            else:
                # Unknown format - assume valid if readable
                logger.debug("Unknown archive format, skipping structural check")
                return True

        except Exception as exc:
            logger.error("Archive structure check failed: %s", exc)
            return False

    def _check_zip_structure(self, archive_path: str) -> bool:
        """Check the structural integrity of a zip archive.

        Args:
            archive_path: Path to the zip file.

        Returns:
            True if the zip archive is valid.
        """
        try:
            with zipfile.ZipFile(archive_path, "r") as zf:
                # Test each member file
                bad_files = zf.testzip()
                if bad_files:
                    logger.error("Corrupt files in zip archive: %s", bad_files)
                    return False

                logger.debug(
                    "Zip archive verified: %d files, %d bytes",
                    len(zf.namelist()),
                    Path(archive_path).stat().st_size,
                )
                return True

        except zipfile.BadZipFile as exc:
            logger.error("Invalid zip file: %s", exc)
            return False

    def _check_tar_structure(self, archive_path: str) -> bool:
        """Check the structural integrity of a tar archive.

        Args:
            archive_path: Path to the tar file.

        Returns:
            True if the tar archive is valid.
        """
        try:
            with tarfile.open(archive_path, "r:*") as tf:
                members = tf.getmembers()
                for member in members:
                    # Check that each member can be accessed
                    if member.isfile():
                        try:
                            f = tf.extractfile(member)
                            if f:
                                # Read a small amount to verify
                                f.read(1)
                                f.close()
                        except Exception as exc:
                            logger.error(
                                "Cannot read tar member '%s': %s", member.name, exc
                            )
                            return False

                logger.debug(
                    "Tar archive verified: %d members, %d bytes",
                    len(members),
                    Path(archive_path).stat().st_size,
                )
                return True

        except tarfile.TarError as exc:
            logger.error("Invalid tar archive: %s", exc)
            return False

    def batch_checksum(self, directory: str, output_file: str) -> None:
        """Compute checksums for all files in a directory and write to a file.

        Args:
            directory: Directory containing files to checksum.
            output_file: Path to the output checksum file.
        """
        dir_path = Path(directory)
        if not dir_path.exists():
            raise IntegrityError(f"Directory not found: {directory}")

        checksums = []
        for file_path in sorted(dir_path.rglob("*")):
            if file_path.is_file():
                checksum = self.compute_checksum(str(file_path))
                rel_path = file_path.relative_to(dir_path)
                checksums.append(f"{checksum}  {rel_path}")

        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(checksums))
            f.write("\n")

        logger.info(
            "Batch checksum complete: %d files -> %s", len(checksums), output_file
        )
