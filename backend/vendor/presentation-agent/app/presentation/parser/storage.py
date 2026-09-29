"""Replaceable storage boundary for extracted binary assets."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol


class AssetStorage(Protocol):
    def put(self, *, asset_id: str, filename: str, payload: bytes, mime_type: str | None) -> str | None: ...


class NullAssetStorage:
    def put(self, *, asset_id: str, filename: str, payload: bytes, mime_type: str | None) -> str | None:
        return None


class FileSystemAssetStorage:
    """Local implementation whose public contract can later be backed by S3/MinIO."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, *, asset_id: str, filename: str, payload: bytes, mime_type: str | None) -> str:
        safe_name = Path(filename).name
        target_dir = self.root / asset_id
        target_dir.mkdir(parents=True, exist_ok=True)
        target = (target_dir / safe_name).resolve()
        if os.path.commonpath((str(self.root), str(target))) != str(self.root):
            raise ValueError("Unsafe asset path")
        target.write_bytes(payload)
        return target.as_uri()
