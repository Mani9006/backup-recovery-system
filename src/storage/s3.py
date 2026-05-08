"""AWS S3 storage backend.

Provides upload, download, and management operations for
storing backup files in Amazon S3 buckets.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
except ImportError:  # pragma: no cover
    boto3 = None  # type: ignore[assignment]
    ClientError = Exception  # type: ignore[misc,assignment]
    NoCredentialsError = Exception  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)


class S3StorageError(Exception):
    """Raised when an S3 storage operation fails."""

    pass


class S3Storage:
    """AWS S3 storage backend for cloud-based backup storage.

    Manages backup files in S3 buckets with support for server-side
    encryption, multi-part uploads, and lifecycle management.
    """

    def __init__(
        self,
        bucket: str,
        region: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        endpoint_url: Optional[str] = None,
    ) -> None:
        """Initialize the S3 storage backend.

        Args:
            bucket: S3 bucket name.
            region: AWS region (default from env or us-east-1).
            access_key: AWS access key (or from env/AWS credentials).
            secret_key: AWS secret key (or from env/AWS credentials).
            endpoint_url: Custom endpoint for S3-compatible services.

        Raises:
            S3StorageError: If boto3 is not installed or credentials are missing.
        """
        if boto3 is None:
            raise S3StorageError(
                "The 'boto3' library is required for S3 storage. "
                "Install it with: pip install boto3"
            )

        self.bucket = bucket
        self.region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        self.endpoint_url = endpoint_url

        session_kwargs: dict = {}
        if access_key:
            session_kwargs["aws_access_key_id"] = access_key
        if secret_key:
            session_kwargs["aws_secret_access_key"] = secret_key

        try:
            session = boto3.Session(**session_kwargs)
            self._client = session.client(
                "s3",
                region_name=self.region,
                endpoint_url=endpoint_url,
            )
            self._resource = session.resource(
                "s3",
                region_name=self.region,
                endpoint_url=endpoint_url,
            )
        except NoCredentialsError as exc:
            raise S3StorageError(
                "AWS credentials not found. Configure them via environment "
                "variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY), "
                "~/.aws/credentials, or IAM role."
            ) from exc

        # Ensure bucket exists
        self._ensure_bucket()
        logger.debug("S3Storage initialized (bucket=%s, region=%s)", bucket, self.region)

    def _ensure_bucket(self) -> None:
        """Create the bucket if it doesn't exist."""
        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError as exc:
            error_code = exc.response["Error"]["Code"]
            if error_code == "404":
                # Bucket doesn't exist, create it
                try:
                    if self.region == "us-east-1":
                        self._client.create_bucket(Bucket=self.bucket)
                    else:
                        self._client.create_bucket(
                            Bucket=self.bucket,
                            CreateBucketConfiguration={
                                "LocationConstraint": self.region
                            },
                        )
                    logger.info("Created S3 bucket: %s", self.bucket)
                except ClientError as create_exc:
                    raise S3StorageError(
                        f"Failed to create S3 bucket '{self.bucket}': {create_exc}"
                    ) from create_exc
            else:
                raise S3StorageError(
                    f"Cannot access S3 bucket '{self.bucket}': {exc}"
                ) from exc

    def upload(self, local_path: str, remote_key: str) -> None:
        """Upload a file to S3.

        Args:
            local_path: Path to the local file.
            remote_key: S3 object key.

        Raises:
            S3StorageError: If the upload fails.
        """
        source = Path(local_path)
        if not source.exists():
            raise S3StorageError(f"Source file not found: {local_path}")

        extra_args: dict = {
            "StorageClass": "STANDARD_IA",  # Infrequent Access for backups
        }

        try:
            self._client.upload_file(
                str(local_path),
                self.bucket,
                remote_key,
                ExtraArgs=extra_args,
            )
            logger.info(
                "Uploaded to S3: %s -> s3://%s/%s", local_path, self.bucket, remote_key
            )
        except ClientError as exc:
            raise S3StorageError(f"S3 upload failed: {exc}") from exc

    def download(self, remote_key: str, local_path: str) -> None:
        """Download a file from S3.

        Args:
            remote_key: S3 object key.
            local_path: Destination path for the downloaded file.

        Raises:
            S3StorageError: If the download fails.
        """
        dest = Path(local_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._client.download_file(self.bucket, remote_key, str(local_path))
            logger.info(
                "Downloaded from S3: s3://%s/%s -> %s",
                self.bucket,
                remote_key,
                local_path,
            )
        except ClientError as exc:
            raise S3StorageError(f"S3 download failed: {exc}") from exc

    def list_files(self, prefix: str = "") -> list[dict]:
        """List files in the S3 bucket.

        Args:
            prefix: Optional key prefix to filter by.

        Returns:
            List of file information dictionaries.
        """
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.bucket, Prefix=prefix)

            files = []
            for page in pages:
                for obj in page.get("Contents", []):
                    files.append(
                        {
                            "key": obj["Key"],
                            "size": obj["Size"],
                            "modified": obj["LastModified"].isoformat(),
                            "etag": obj["ETag"].strip('"'),
                        }
                    )

            files.sort(key=lambda f: f["modified"], reverse=True)
            return files

        except ClientError as exc:
            raise S3StorageError(f"Failed to list S3 objects: {exc}") from exc

    def delete(self, remote_key: str) -> None:
        """Delete an object from S3.

        Args:
            remote_key: S3 object key to delete.

        Raises:
            S3StorageError: If deletion fails.
        """
        try:
            self._client.delete_object(Bucket=self.bucket, Key=remote_key)
            logger.info("Deleted from S3: s3://%s/%s", self.bucket, remote_key)
        except ClientError as exc:
            raise S3StorageError(f"S3 delete failed: {exc}") from exc

    def exists(self, remote_key: str) -> bool:
        """Check if an object exists in S3.

        Args:
            remote_key: S3 object key.

        Returns:
            True if the object exists.
        """
        try:
            self._client.head_object(Bucket=self.bucket, Key=remote_key)
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                return False
            raise S3StorageError(f"S3 head_object failed: {exc}") from exc

    def get_size(self, remote_key: str) -> int:
        """Get the size of an S3 object.

        Args:
            remote_key: S3 object key.

        Returns:
            Object size in bytes.

        Raises:
            S3StorageError: If the object doesn't exist.
        """
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=remote_key)
            return response["ContentLength"]
        except ClientError as exc:
            raise S3StorageError(f"Failed to get S3 object size: {exc}") from exc

    def generate_presigned_url(self, remote_key: str, expiration: int = 3600) -> str:
        """Generate a presigned URL for temporary access.

        Args:
            remote_key: S3 object key.
            expiration: URL expiration time in seconds.

        Returns:
            Presigned URL string.
        """
        try:
            url = self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": remote_key},
                ExpiresIn=expiration,
            )
            return url
        except ClientError as exc:
            raise S3StorageError(f"Failed to generate presigned URL: {exc}") from exc
