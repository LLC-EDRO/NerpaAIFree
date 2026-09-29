"""Safe read-only access to an OPC/PPTX archive."""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
import zlib
from collections.abc import Iterable
from pathlib import Path

from lxml import etree

from app.presentation.parser.constants import NS

MAX_ARCHIVE_ENTRIES = 20_000
MAX_UNCOMPRESSED_BYTES = 1_000_000_000
MAX_COMPRESSION_RATIO = 2_000


class InvalidPresentationError(ValueError):
    """Raised when input is not a safe, readable PPTX package."""


class PptxArchive:
    def __init__(self, payload: bytes) -> None:
        self._buffer = io.BytesIO(payload)
        self.zip: zipfile.ZipFile | None = None
        self.unreadable_parts: set[str] = set()
        try:
            self.zip = zipfile.ZipFile(self._buffer, "r")
        except (zipfile.BadZipFile, EOFError, OSError, RuntimeError, zlib.error) as exc:
            if self.zip is not None:
                self.zip.close()
            self._buffer.close()
            raise InvalidPresentationError("Unable to read PPTX archive") from exc
        self._validate_limits()

    def _validate_limits(self) -> None:
        infos = self.zip.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise InvalidPresentationError("PPTX contains too many archive entries")
        total = sum(info.file_size for info in infos)
        if total > MAX_UNCOMPRESSED_BYTES:
            raise InvalidPresentationError("PPTX uncompressed size exceeds safety limit")
        for info in infos:
            normalized = posixpath.normpath(info.filename)
            if normalized.startswith("../") or normalized.startswith("/"):
                raise InvalidPresentationError(f"Unsafe archive path: {info.filename}")
            compressed = max(info.compress_size, 1)
            if info.file_size / compressed > MAX_COMPRESSION_RATIO:
                raise InvalidPresentationError(f"Suspicious compression ratio: {info.filename}")

    @property
    def names(self) -> set[str]:
        return set(self.zip.namelist())

    def exists(self, part: str) -> bool:
        return part.lstrip("/") in self.names

    def read_bytes(self, part: str) -> bytes | None:
        normalized = part.lstrip("/")
        try:
            return self.zip.read(normalized)
        except KeyError:
            return None
        except (zipfile.BadZipFile, EOFError, OSError, RuntimeError, zlib.error):
            # One damaged optional member (commonly a theme, thumbnail, or
            # media object) must not discard every readable slide. Callers
            # already handle a missing part and the parser reports degradation.
            self.unreadable_parts.add(normalized)
            return None

    def read_text(self, part: str) -> str | None:
        data = self.read_bytes(part)
        if data is None:
            return None
        for encoding in ("utf-8-sig", "utf-8", "utf-16"):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                continue
        return data.decode("utf-8", errors="replace")

    def xml(self, part: str) -> etree._Element | None:
        data = self.read_bytes(part)
        if data is None:
            return None
        parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False, huge_tree=False)
        try:
            return etree.fromstring(data, parser=parser)
        except etree.XMLSyntaxError:
            return None

    def entries(self, prefix: str) -> list[str]:
        return sorted(
            (name for name in self.zip.namelist() if name.startswith(prefix) and not name.endswith("/")),
            key=_natural_part_key,
        )

    def close(self) -> None:
        self.zip.close()
        self._buffer.close()

    def __enter__(self) -> PptxArchive:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _natural_part_key(value: str) -> tuple[object, ...]:
    return tuple(int(token) if token.isdigit() else token.lower() for token in re.split(r"(\d+)", value))


def relationship_part(source_part: str) -> str:
    directory, filename = posixpath.split(source_part)
    return posixpath.join(directory, "_rels", f"{filename}.rels")


def resolve_target(source_part: str, target: str) -> str:
    if not target:
        return ""
    if target.startswith("/"):
        return posixpath.normpath(target.lstrip("/"))
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def parse_content_types(archive: PptxArchive) -> tuple[dict[str, str], dict[str, str]]:
    root = archive.xml("[Content_Types].xml")
    overrides: dict[str, str] = {}
    defaults: dict[str, str] = {}
    if root is None:
        return overrides, defaults
    for node in root.xpath("./ct:Override", namespaces=NS):
        name = str(node.get("PartName") or "").lstrip("/")
        content_type = str(node.get("ContentType") or "")
        if name:
            overrides[name] = content_type
    for node in root.xpath("./ct:Default", namespaces=NS):
        extension = str(node.get("Extension") or "").lower()
        content_type = str(node.get("ContentType") or "")
        if extension:
            defaults[extension] = content_type
    return overrides, defaults


def content_type_for(part: str, overrides: dict[str, str], defaults: dict[str, str]) -> str | None:
    direct = overrides.get(part.lstrip("/"))
    if direct:
        return direct
    suffix = Path(part).suffix.lstrip(".").lower()
    return defaults.get(suffix)


def choose_alternate_content(root: etree._Element) -> etree._Element:
    """Replace mc:AlternateContent with the first supported Choice, else Fallback."""
    for alternate in list(root.xpath(".//mc:AlternateContent", namespaces=NS)):
        choices = alternate.xpath("./mc:Choice", namespaces=NS)
        selected = None
        for choice in choices:
            required = set(str(choice.get("Requires") or "").split())
            if not required or required.issubset({"a", "a14", "a16", "c", "dgm", "p", "pic", "r"}):
                selected = choice
                break
        if selected is None:
            fallback = alternate.xpath("./mc:Fallback", namespaces=NS)
            selected = fallback[0] if fallback else None
        parent = alternate.getparent()
        if parent is None:
            continue
        index = parent.index(alternate)
        if selected is not None:
            for child in list(selected):
                parent.insert(index, child)
                index += 1
        parent.remove(alternate)
    return root


def iter_existing(archive: PptxArchive, parts: Iterable[str]) -> Iterable[str]:
    return (part for part in parts if archive.exists(part))
