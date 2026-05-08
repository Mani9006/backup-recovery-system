"""Storage backends for local and cloud backup targets."""

from src.storage.local import LocalStorage
from src.storage.s3 import S3Storage
from src.storage.gcs import GCSStorage
from src.storage.azure import AzureStorage

__all__ = ["LocalStorage", "S3Storage", "GCSStorage", "AzureStorage"]
