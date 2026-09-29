"""OOXML namespace and geometry constants."""

from __future__ import annotations

NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "ct": "http://schemas.openxmlformats.org/package/2006/content-types",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

R_ID = f"{{{NS['r']}}}id"
R_EMBED = f"{{{NS['r']}}}embed"
R_LINK = f"{{{NS['r']}}}link"

DEFAULT_SLIDE_WIDTH_EMU = 12_192_000
DEFAULT_SLIDE_HEIGHT_EMU = 6_858_000
EMU_PER_POINT = 12_700
DEFAULT_LINE_WIDTH_EMU = 12_700

REL_TYPE_SLIDE = "/slide"
REL_TYPE_LAYOUT = "/slideLayout"
REL_TYPE_MASTER = "/slideMaster"
REL_TYPE_THEME = "/theme"

SUPPORTED_MC_PREFIXES = {"a", "a14", "a16", "c", "dgm", "p", "pic", "r"}
