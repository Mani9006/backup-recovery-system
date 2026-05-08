# Architecture Documentation

## Automated Backup & Recovery System - Architecture Overview

## System Design

The Automated Backup & Recovery System is a modular Python application designed for reliable, configurable, and extensible backup operations. It follows a layered architecture with clear separation of concerns.

```
+-----------------------------------------------------------------+
|                         CLI Layer                                |
|  (commands: backup, restore, schedule, validate, list-jobs)     |
+-----------------------------------------------------------------+
|                       Core Engine                                |
|  +----------+  +----------+  +----------+  +----------+         |
|  | Scheduler|  | Config   |  | Restore  |  | Retention|         |
|  |          |  | Manager  |  | Manager  |  | Manager  |         |
|  +----------+  +----------+  +----------+  +----------+         |
+-----------------------------------------------------------------+
|                      Backup Providers                            |
|  +--------------+  +----------------+  +-----------------+      |
|  | File Backup  |  | Database Backup|  | Cloud Backup    |      |
|  |              |  |                |  |                 |      |
|  | - Filesystem |  | - PostgreSQL   |  | - S3            |      |
|  | - Tar/Zip    |  | - MySQL        |  | - GCS           |      |
|  | - Exclusions |  | - SQLite       |  | - Azure         |      |
|  +--------------+  +----------------+  +-----------------+      |
+-----------------------------------------------------------------+
|                      Storage Backends                            |
|  +----------+  +----------+  +----------+  +----------+         |
|  | Local    |  | AWS S3   |  | GCS      |  | Azure    |         |
|  | Filesys  |  | Bucket   |  | Bucket   |  | Blob     |         |
|  +----------+  +----------+  +----------+  +----------+         |
+-----------------------------------------------------------------+
|                      Utility Modules                             |
|  +-----------+  +-----------+  +-------------+  +-----------+   |
|  | Compress  |  | Encrypt   |  | Integrity   |  | Notify    |   |
|  | (gzip/zip)|  | (AES-256) |  | (SHA-256)   |  | (Webhook/ |   |
|  |           |  |           |  |             |  |  Email)   |   |
|  +-----------+  +-----------+  +-------------+  +-----------+   |
+-----------------------------------------------------------------+
```

## Component Descriptions

### CLI Layer

The command-line interface (`cli.py`) serves as the entry point, providing commands for:

- **backup**: Execute one or all backup jobs
- **restore**: Recover data from a backup archive
- **schedule**: Run the scheduler daemon with cron-based job execution
- **validate**: Validate configuration file syntax and semantics
- **list-jobs**: Display all configured jobs with status
- **verify**: Verify archive integrity using checksums
- **init**: Generate a default configuration file

### Core Engine

**Scheduler** (`scheduler.py`)
- Uses cron expressions via the `croniter` library
- Polling-based execution with configurable interval
- Supports runtime job addition/removal
- Graceful shutdown with signal handling
- Thread-safe operations with locking

**Config Manager** (`config.py`)
- YAML-based configuration with environment variable substitution
- Schema validation for backends, compression, and logging
- Dataclass-based configuration objects
- Default configuration generation

**Retention Manager** (`retention.py`)
- Grandfather-father-son rotation scheme
- Classification by modification time (daily/weekly/monthly)
- Configurable retention periods per job
- Automatic cleanup of expired backups

**Restore Manager** (`restore.py`)
- Multi-stage restoration pipeline
- Automatic format detection and decompression
- Cloud-to-local download support
- Optional integrity verification before restore
- Path traversal protection for tar extraction

### Backup Providers

**File Backup** (`backup/file_backup.py`)
- Recursive directory traversal with glob-based exclusions
- Hidden file skipping option
- Tar archive creation with gzip/bzip2 compression
- Single-file and multi-file backup support

**Database Backup** (`backup/database_backup.py`)
- Native database dump utilities (pg_dump, mysqldump)
- SQLite file copy with integrity verification
- Support for PostgreSQL, MySQL/MariaDB, SQLite
- Custom dump arguments support

**Cloud Backup** (`backup/cloud_backup.py`)
- Metadata collection (object listings, sizes, timestamps)
- Full object download mode for complete copies
- Support for S3, GCS, Azure, and SFTP sources
- JSON metadata export for inventory tracking

### Storage Backends

Each storage backend implements a consistent interface:

- **upload**: Store a file
- **download**: Retrieve a file
- **list_files**: List stored files
- **delete**: Remove a file
- **exists**: Check existence
- **get_size**: Get file size

**Local** (`storage/local.py`): Filesystem-based storage with path management
**S3** (`storage/s3.py`): AWS S3 with automatic bucket creation, presigned URLs
**GCS** (`storage/gcs.py`): Google Cloud Storage with bucket lifecycle
**Azure** (`storage/azure.py`): Azure Blob Storage with SAS token generation

### Utility Modules

**Compression** (`compression.py`)
- Gzip, Bzip2, and Zip support
- Streaming compression for large files
- Automatic format detection by extension and magic number

**Encryption** (`encryption.py`)
- AES-128-CBC with HMAC via Fernet
- PBKDF2 key derivation with SHA-256
- Random salt per encryption operation
- Password hashing utilities

**Integrity** (`integrity.py`)
- SHA-256, SHA-512, and MD5 checksum support
- Checksum file generation and verification
- Tar and zip structural integrity checking
- Batch checksum computation

**Notifications** (`notifications.py`)
- Webhook notifications (HTTP POST with JSON payload)
- Email notifications via SMTP
- Event types: backup.success, backup.failure
- Custom webhook support

## Data Flow

### Backup Flow

```
Source Data
    |
    v
[Collect/Export] --> Archive Creation --> Compression --> Encryption
                                                          |
                                                          v
                                                  Checksum Generation
                                                          |
                                                          v
                                                  Storage Backend
                                                          |
                                                          v
                                                  Retention Cleanup
```

### Restore Flow

```
Backup Archive
    |
    v
[Locate/Download] --> Integrity Verify --> Decryption --> Decompression
                                                            |
                                                            v
                                                     Extraction to Destination
```

### Scheduled Execution Flow

```
Cron Expression (croniter)
    |
    v
Next Run Time Calculation
    |
    v
Polling Loop (configurable interval)
    |
    v
Job Due? --> Execute Backup Pipeline --> Notify Result
    |                                         |
    No                                        v
    |                                    Update Next Run Time
    v
    Sleep
```

## Security Considerations

1. **Encryption**: AES-128-CBC with HMAC authentication via Fernet
   - PBKDF2 key derivation with 600,000 iterations
   - Random 16-byte salt per encryption
   - Separate salt storage in encrypted file format

2. **Path Traversal**: Tar extraction validates member paths against destination

3. **Credential Management**: Environment variable substitution in configuration
   - No credentials stored in plain text in config files
   - Support for cloud provider credential chains

4. **Temporary Files**: All operations use temporary directories with automatic cleanup

## Extensibility

Adding new backup types:
1. Create a new class in `backup/` implementing the `run()` method
2. Register the type in `cli.py` `_execute_backup_job()`

Adding new storage backends:
1. Create a new class in `storage/` implementing the standard interface
2. Register the backend name in `ConfigLoader._validate()`

Adding new compression formats:
1. Add an entry to the `CompressionMethod` enum
2. Implement compress/decompress methods in `CompressionManager`

Adding new notification channels:
1. Add a new `_send_*` method in `NotificationManager`
2. Call it from the `_send()` dispatcher
