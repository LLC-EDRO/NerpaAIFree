"""Stable identifiers compatible with the original Node.js analyzer."""

from __future__ import annotations

import re
import hashlib


def _part_id(prefix: str, part: str, token: str) -> str:
    match = re.search(rf"{re.escape(token)}(\d+)\.xml$", part, re.IGNORECASE)
    return f"{prefix}_{match.group(1)}" if match else f"{prefix}_{hashlib.sha256(part.encode()).hexdigest()[:12]}"


def slide_id_from_part(part: str) -> str:
    return _part_id("slide", part, "slide")


def layout_id_from_part(part: str) -> str:
    return _part_id("layout", part, "slideLayout")


def master_id_from_part(part: str) -> str:
    return _part_id("master", part, "slideMaster")


def theme_id_from_part(part: str) -> str:
    return _part_id("theme", part, "theme")


def media_id_from_part(part: str) -> str:
    match = re.search(r"(?:image|media)(\d+)", part, re.IGNORECASE)
    if match:
        return f"media_{match.group(1)}"
    stem = part.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return f"media_{stem}"


def object_id(scope_id: str, shape_id: int) -> str:
    return f"{scope_id}_obj_{shape_id}"


def physical_identity_key(
    source_level: str,
    source_part: str | None,
    source_object_id: str,
    parent_group_path: str | None,
) -> str:
    return "|".join((source_level, source_part or "-", source_object_id, parent_group_path or "-"))
