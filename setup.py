"""Setup script for the Automated Backup & Recovery System."""

from setuptools import find_packages, setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="automated-backup-recovery",
    version="1.0.0",
    author="Mani",
    author_email="myfamily9006@gmail.com",
    description="A comprehensive Python-based backup solution",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/example/backup-recovery",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: System :: Archiving :: Backup",
    ],
    python_requires=">=3.9",
    install_requires=[
        "pyyaml>=6.0",
        "cryptography>=41.0.0",
        "croniter>=1.4.0",
    ],
    extras_require={
        "s3": ["boto3>=1.28.0"],
        "gcs": ["google-cloud-storage>=2.10.0"],
        "azure": ["azure-storage-blob>=12.17.0"],
        "sftp": ["paramiko>=3.3.0"],
        "webhook": ["requests>=2.31.0"],
        "all": [
            "boto3>=1.28.0",
            "google-cloud-storage>=2.10.0",
            "azure-storage-blob>=12.17.0",
            "paramiko>=3.3.0",
            "requests>=2.31.0",
        ],
        "dev": [
            "pytest>=7.4.0",
            "pytest-cov>=4.1.0",
            "black>=23.7.0",
            "flake8>=6.1.0",
            "mypy>=1.5.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "backup-recovery=src.cli:main",
        ],
    },
)
