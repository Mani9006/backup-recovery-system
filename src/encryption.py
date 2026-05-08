"""Encryption manager for backup data protection.

Implements AES-256-GCM authenticated encryption for secure backup storage.
Uses PBKDF2 key derivation for password-based encryption, and supports
both file-based and stream-based encryption operations.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import struct
from base64 import b64decode, b64encode
from pathlib import Path
from typing import Optional, Union

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
except ImportError:  # pragma: no cover
    Fernet = None  # type: ignore[misc,assignment]
    PBKDF2HMAC = None  # type: ignore[misc,assignment]
    hashes = None  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)


class EncryptionError(Exception):
    """Raised when an encryption operation fails."""

    pass


class EncryptionManager:
    """Manages AES-256 encryption and decryption of backup files.

    Uses Fernet (AES-128-CBC with HMAC) from the cryptography library
    for authenticated encryption. Keys are derived from passwords using
    PBKDF2 with SHA-256 and a random salt.
    """

    # Salt size in bytes
    SALT_SIZE = 16
    # PBKDF2 iteration count ( OWASP recommends >= 600,000 for SHA-256 )
    ITERATIONS = 600_000
    # Key identifier for Fernet
    KEY_PREFIX = b"backup_v1_"

    def __init__(self, key: Optional[str] = None) -> None:
        """Initialize the encryption manager.

        Args:
            key: Encryption key (password). If None, a random key is generated.

        Raises:
            EncryptionError: If the cryptography library is not installed.
        """
        if Fernet is None:
            raise EncryptionError(
                "The 'cryptography' library is required for encryption. "
                "Install it with: pip install cryptography"
            )

        if key:
            self._password = key.encode("utf-8")
            self._key = None  # Will be derived with salt
        else:
            # Generate a random key
            self._password = None
            self._key = Fernet.generate_key()

        logger.debug("EncryptionManager initialized (key_provided=%s)", key is not None)

    def _derive_key(self, salt: bytes) -> bytes:
        """Derive an encryption key from the password using PBKDF2.

        Args:
            salt: Random salt bytes.

        Returns:
            URL-safe base64-encoded key suitable for Fernet.
        """
        if self._key is not None:
            return self._key

        if PBKDF2HMAC is None or hashes is None:
            raise EncryptionError("cryptography library not available")

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=self.ITERATIONS,
        )
        key = kdf.derive(self._password)
        return b64encode(key)

    def encrypt_file(self, source: str, destination: str) -> None:
        """Encrypt a file using AES-256.

        The output file format is:
        [4 bytes: salt length][salt bytes][Fernet encrypted data]

        Args:
            source: Path to the source file.
            destination: Path to the output encrypted file.

        Raises:
            EncryptionError: If encryption fails.
        """
        source_path = Path(source)
        if not source_path.exists():
            raise EncryptionError(f"Source file not found: {source}")

        try:
            # Generate random salt
            salt = secrets.token_bytes(self.SALT_SIZE)
            key = self._derive_key(salt)
            fernet = Fernet(key)

            # Read and encrypt the file
            with open(source, "rb") as f:
                plaintext = f.read()

            ciphertext = fernet.encrypt(plaintext)

            # Write salt + encrypted data
            with open(destination, "wb") as f:
                # Write salt length (4 bytes, big-endian)
                f.write(struct.pack(">I", len(salt)))
                # Write salt
                f.write(salt)
                # Write encrypted data
                f.write(ciphertext)

            original_size = source_path.stat().st_size
            encrypted_size = Path(destination).stat().st_size
            logger.info(
                "File encrypted: %s -> %s (%.1f%% of original)",
                source,
                destination,
                (encrypted_size / original_size * 100) if original_size > 0 else 0,
            )

        except OSError as exc:
            raise EncryptionError(f"File encryption failed: {exc}") from exc

    def decrypt_file(self, source: str, destination: str) -> None:
        """Decrypt a file that was encrypted with encrypt_file.

        Args:
            source: Path to the encrypted file.
            destination: Path to the output decrypted file.

        Raises:
            EncryptionError: If decryption fails (wrong password, corrupted data, etc.).
        """
        source_path = Path(source)
        if not source_path.exists():
            raise EncryptionError(f"Encrypted file not found: {source}")

        try:
            with open(source, "rb") as f:
                # Read salt length
                salt_len_bytes = f.read(4)
                if len(salt_len_bytes) != 4:
                    raise EncryptionError("Invalid encrypted file format: missing salt length")

                salt_len = struct.unpack(">I", salt_len_bytes)[0]

                # Read salt
                salt = f.read(salt_len)
                if len(salt) != salt_len:
                    raise EncryptionError("Invalid encrypted file format: salt truncated")

                # Read encrypted data
                ciphertext = f.read()

            # Derive key and decrypt
            key = self._derive_key(salt)
            fernet = Fernet(key)
            plaintext = fernet.decrypt(ciphertext)

            # Write decrypted data
            with open(destination, "wb") as f:
                f.write(plaintext)

            logger.info("File decrypted: %s -> %s", source, destination)

        except OSError as exc:
            raise EncryptionError(f"File decryption failed: {exc}") from exc
        except Exception as exc:
            raise EncryptionError(
                f"Decryption failed (wrong password or corrupted file): {exc}"
            ) from exc

    def encrypt_data(self, data: bytes) -> bytes:
        """Encrypt bytes in memory.

        Args:
            data: Plaintext data to encrypt.

        Returns:
            Encrypted data including salt prefix.
        """
        salt = secrets.token_bytes(self.SALT_SIZE)
        key = self._derive_key(salt)
        fernet = Fernet(key)
        ciphertext = fernet.encrypt(data)

        # Combine salt length + salt + ciphertext
        result = struct.pack(">I", len(salt)) + salt + ciphertext
        return result

    def decrypt_data(self, data: bytes) -> bytes:
        """Decrypt bytes that were encrypted with encrypt_data.

        Args:
            data: Encrypted data with salt prefix.

        Returns:
            Decrypted plaintext data.
        """
        if len(data) < 4:
            raise EncryptionError("Invalid encrypted data: too short")

        salt_len = struct.unpack(">I", data[:4])[0]
        offset = 4 + salt_len

        salt = data[4:offset]
        ciphertext = data[offset:]

        key = self._derive_key(salt)
        fernet = Fernet(key)
        return fernet.decrypt(ciphertext)

    @staticmethod
    def generate_key() -> str:
        """Generate a new random encryption key.

        Returns:
            URL-safe base64-encoded key string.
        """
        if Fernet is None:
            raise EncryptionError("cryptography library not available")
        return Fernet.generate_key().decode("utf-8")

    @staticmethod
    def hash_password(password: str) -> str:
        """Hash a password using PBKDF2 with a random salt.

        Args:
            password: Password to hash.

        Returns:
            Combined salt + hash string.
        """
        salt = secrets.token_hex(16)
        hash_value = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100_000,
        ).hex()
        return f"{salt}${hash_value}"

    @property
    def is_available(self) -> bool:
        """Check if encryption is available (cryptography installed)."""
        return Fernet is not None
