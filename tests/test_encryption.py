"""Tests for the encryption module."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.encryption import EncryptionError, EncryptionManager


class TestEncryptionManager:
    """Tests for EncryptionManager."""

    def test_init_with_key(self):
        """Test initialization with a password key."""
        mgr = EncryptionManager(key="my_secret_password")
        assert mgr is not None

    def test_init_without_key(self):
        """Test initialization without a key (generates random)."""
        mgr = EncryptionManager()
        assert mgr is not None
        assert mgr._key is not None

    def test_encrypt_decrypt_file(self, tmp_path: Path):
        """Test encrypting and decrypting a file."""
        source = tmp_path / "secret.txt"
        original = b"This is sensitive backup data"
        source.write_bytes(original)
        encrypted = tmp_path / "secret.txt.enc"
        decrypted = tmp_path / "secret_restored.txt"

        mgr = EncryptionManager(key="my_password_123")
        mgr.encrypt_file(str(source), str(encrypted))

        assert encrypted.exists()
        assert encrypted.stat().st_size > 0

        mgr.decrypt_file(str(encrypted), str(decrypted))
        assert decrypted.read_bytes() == original

    def test_encrypt_decrypt_data(self):
        """Test encrypting and decrypting bytes in memory."""
        original = b"Sensitive data to encrypt"
        mgr = EncryptionManager(key="test_password")

        encrypted = mgr.encrypt_data(original)
        assert encrypted != original
        assert len(encrypted) > 0

        decrypted = mgr.decrypt_data(encrypted)
        assert decrypted == original

    def test_different_keys_fail(self, tmp_path: Path):
        """Test that decryption with wrong key fails."""
        source = tmp_path / "secret.txt"
        source.write_bytes(b"Secret content")
        encrypted = tmp_path / "secret.txt.enc"
        decrypted = tmp_path / "secret_restored.txt"

        encrypt_mgr = EncryptionManager(key="correct_password")
        encrypt_mgr.encrypt_file(str(source), str(encrypted))

        decrypt_mgr = EncryptionManager(key="wrong_password")
        with pytest.raises(EncryptionError):
            decrypt_mgr.decrypt_file(str(encrypted), str(decrypted))

    def test_encrypt_missing_source(self, tmp_path: Path):
        """Test that encrypting a missing file raises an error."""
        mgr = EncryptionManager(key="password")
        with pytest.raises(EncryptionError, match="not found"):
            mgr.encrypt_file(str(tmp_path / "missing"), str(tmp_path / "out"))

    def test_decrypt_missing_source(self, tmp_path: Path):
        """Test that decrypting a missing file raises an error."""
        mgr = EncryptionManager(key="password")
        with pytest.raises(EncryptionError, match="not found"):
            mgr.decrypt_file(str(tmp_path / "missing"), str(tmp_path / "out"))

    def test_decrypt_corrupted_file(self, tmp_path: Path):
        """Test that decrypting a corrupted file raises an error."""
        corrupted = tmp_path / "corrupted.enc"
        corrupted.write_bytes(b"\x00\x00\x00\x10invalid_data")

        mgr = EncryptionManager(key="password")
        with pytest.raises(EncryptionError):
            mgr.decrypt_file(str(corrupted), str(tmp_path / "out"))

    def test_generate_key(self):
        """Test key generation."""
        key = EncryptionManager.generate_key()
        assert isinstance(key, str)
        assert len(key) > 0

    def test_hash_password(self):
        """Test password hashing."""
        hashed = EncryptionManager.hash_password("my_password")
        assert isinstance(hashed, str)
        assert "$" in hashed  # salt$hash format

    def test_is_available(self):
        """Test that encryption is available."""
        mgr = EncryptionManager(key="test")
        assert mgr.is_available is True

    def test_file_format_structure(self, tmp_path: Path):
        """Test that encrypted file has correct structure."""
        import struct

        source = tmp_path / "data.txt"
        source.write_bytes(b"test data")
        encrypted = tmp_path / "data.enc"

        mgr = EncryptionManager(key="password")
        mgr.encrypt_file(str(source), str(encrypted))

        data = encrypted.read_bytes()
        # First 4 bytes: salt length
        salt_len = struct.unpack(">I", data[:4])[0]
        assert salt_len == 16  # SALT_SIZE

        # Next bytes: salt
        salt = data[4 : 4 + salt_len]
        assert len(salt) == salt_len

        # Remaining: Fernet token
        token = data[4 + salt_len :]
        assert len(token) > 0
