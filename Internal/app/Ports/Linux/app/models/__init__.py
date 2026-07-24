#!/usr/bin/env python3
"""Model infrastructure for Mumble — discover, manage, and recommend on-device models.

This package provides:
  - ModelManager: singleton that discovers GGUF files, tracks status, manages load/unload
  - ModelRegistry: hardware-tier → recommended model mapping with metadata
  - ModelDownloader: HuggingFace Hub download with resume, SHA256, progress
  - ModelCache: LRU disk cache with configurable size limit
  - ModelProcessManager: persistent llama-cli subprocess lifecycle management

Pure stdlib + project modules (branding). No heavy dependencies.
"""

from models.manager import ModelManager  # noqa: F401
from models.registry import ModelRegistry  # noqa: F401
from models.downloader import (  # noqa: F401
    ModelDownloader,
    cancel_active_downloads,
    active_download_count,
)
from models.cache import ModelCache  # noqa: F401
from models.backend import ModelProcessManager  # noqa: F401
