"""Conservative physical fit checks for unmodified DrawingML text frames.

This is an installed-font preflight, not a PowerPoint rendering oracle. Unknown
formatting and font substitution never produce a passing result. No source
geometry, font, line spacing, or replacement payload is modified.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from functools import lru_cache
from typing import Literal

import pymupdf
from lxml import etree
from PIL import ImageFont
from pydantic import BaseModel, ConfigDict, Field

from app.presentation.models import ShapeElementModel
from app.text.measurement import FontResolution, FontResolver, TextMeasurementService
from app.text.replacement import replacement_paragraph_sources

EMU_PER_POINT = 12_700
# ECMA-376 DrawingML bodyPr defaults (0.1 inch horizontal, 0.05 vertical).
DEFAULT_HORIZONTAL_INSET = 91_440
DEFAULT_VERTICAL_INSET = 45_720


class NativeContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NativeParagraph(NativeContract):
    has_text: bool
    font_family: str | None = None
    font_size_pt: float | None = None
    bold: bool = False
    italic: bool = False
    margin_left_emu: int = 0
    margin_right_emu: int = 0
    indent_emu: int = 0
    line_spacing: tuple[Literal["percent", "points"], float] = ("percent", 100.0)
    space_before: tuple[Literal["percent", "points"], float] = ("points", 0.0)
    space_after: tuple[Literal["percent", "points"], float] = ("points", 0.0)
    reason_codes: list[str] = Field(default_factory=list)


class NativeTextFrame(NativeContract):
    source_element_id: str
    source_fingerprint: str
    width_emu: int = 0
    height_emu: int = 0
    left_emu: int = DEFAULT_HORIZONTAL_INSET
    right_emu: int = DEFAULT_HORIZONTAL_INSET
    top_emu: int = DEFAULT_VERTICAL_INSET
    bottom_emu: int = DEFAULT_VERTICAL_INSET
    wrap: bool = True
    font_override: str | None = None
    paragraphs: list[NativeParagraph] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class NativeParagraphMeasurement(NativeContract):
    source_paragraph_index: int
    font_family: str
    font_size_pt: float
    font_path: str
    line_count: int
    width_emu: int
    height_emu: int


class NativeTextFitReport(NativeContract):
    status: Literal["fits", "overflow", "unverifiable"]
    source_element_id: str
    source_fingerprint: str
    payload_sha256: str
    font_override: str | None = None
    frame_sha256: str
    method: str = "native_installed_font_preflight_v1"
    safety_margin: float
    available_width_emu: int = 0
    available_height_emu: int = 0
    measured_width_emu: int = 0
    measured_height_emu: int = 0
    paragraphs: list[NativeParagraphMeasurement] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


def payload_sha256(paragraphs: list[str]) -> str:
    return hashlib.sha256(json.dumps(paragraphs or [""], ensure_ascii=False).encode()).hexdigest()


def _xml(value: str) -> etree._Element:
    return etree.fromstring(value.encode(), parser=etree.XMLParser(resolve_entities=False, no_network=True))


def _name(node: etree._Element) -> str:
    return etree.QName(node).localname


def _merge(target: etree._Element, source: etree._Element) -> None:
    """Partial paragraph/default-run properties inherit, not whole containers."""
    target.attrib.update(source.attrib)
    bullet_groups = ({"buNone", "buChar", "buAutoNum", "buBlip"}, {"buFont", "buFontTx"},
                     {"buSzPct", "buSzPts", "buSzTx"})
    for child in source:
        tag = _name(child)
        conflicts = next((group for group in bullet_groups if tag in group), {tag})
        existing = next((item for item in target if _name(item) == tag), None)
        if existing is not None and tag == "defRPr":
            _merge(existing, child)
            continue
        for item in list(target):
            if _name(item) in conflicts:
                target.remove(item)
        target.append(_xml(etree.tostring(child).decode()))


def _spacing(properties: etree._Element, name: str, default):
    node = next((child for child in properties if _name(child) == name), None)
    if node is None:
        return default
    if len(node) != 1 or _name(node[0]) not in {"spcPct", "spcPts"}:
        raise ValueError("Unsupported paragraph spacing")
    child = node[0]
    return ("percent", float(child.get("val")) / 1000) if _name(child) == "spcPct" else (
        "points", float(child.get("val")) / 100)


def _font_from_properties(properties: etree._Element) -> dict:
    default = next((child for child in properties if _name(child) == "defRPr"), None)
    if default is None:
        return {}
    values = {}
    for key, field in (("sz", "font_size_pt"), ("spc", "character_spacing"), ("baseline", "baseline")):
        if key in default.attrib:
            values[field] = float(default.get(key)) / (1000 if key == "baseline" else 100)
    for key, field in (("b", "bold"), ("i", "italic")):
        if key in default.attrib:
            values[field] = default.get(key) in {"true", "1", "on"}
    latin = next((child for child in default if _name(child) == "latin"), None)
    if latin is not None:
        values["font_family"] = latin.get("typeface")
    return values


def source_text_frame(shape: ShapeElementModel, *, font_override: str | None = None) -> NativeTextFrame:
    frame = NativeTextFrame(source_element_id=shape.source_object_id,
                            source_fingerprint=str((shape.raw_xml_ref or {}).get("sha256") or ""), font_override=font_override)
    if shape.text_frame is None or shape.rich_text is None:
        frame.reason_codes.append("source_text_frame_missing")
        return frame
    geometry = shape.geometry_full.local_bbox if shape.geometry_full else shape.geometry
    if geometry is None or not geometry.width_emu or not geometry.height_emu:
        frame.reason_codes.append("source_text_geometry_missing")
    else:
        frame.width_emu, frame.height_emu = geometry.width_emu, geometry.height_emu
    full = shape.geometry_full
    if full and (full.rotation_deg or full.flip_h or full.flip_v or full.group_transform_chain):
        frame.reason_codes.append("transformed_text_frame_unsupported")
    if shape.text_frame.shape_geometry not in {None, "rect"}:
        frame.reason_codes.append("nonrectangular_text_frame_unsupported")
    body = {}
    children = {}
    try:
        for layer in shape.text_frame.style_layers:
            body.update(layer.body_attributes)
            for value in layer.body_children:
                child = _xml(value)
                name = _name(child)
                if name in {"noAutofit", "normAutofit", "spAutoFit"}:
                    for key in {"noAutofit", "normAutofit", "spAutoFit"}:
                        children.pop(key, None)
                children[name] = child
        for attr, field in (("lIns", "left_emu"), ("rIns", "right_emu"), ("tIns", "top_emu"), ("bIns", "bottom_emu")):
            if attr in body:
                setattr(frame, field, int(body[attr]))
        if min(frame.left_emu, frame.right_emu, frame.top_emu, frame.bottom_emu) < 0:
            frame.reason_codes.append("negative_text_insets_unsupported")
        frame.wrap = body.get("wrap", "square") != "none"
        if body.get("wrap", "square") not in {"square", "none"}:
            frame.reason_codes.append("text_wrap_mode_unsupported")
        if int(body.get("numCol", "1")) != 1:
            frame.reason_codes.append("multicolumn_text_unsupported")
        if body.get("vert", "horz") != "horz" or int(body.get("rot", "0")):
            frame.reason_codes.append("vertical_text_unsupported")
        if body.get("anchor", "t") not in {"t", "ctr", "b"}:
            frame.reason_codes.append("distributed_text_anchor_unsupported")
        if body.get("fromWordArt", "0") in {"1", "true"}:
            frame.reason_codes.append("wordart_unsupported")
        if "normAutofit" in children or "spAutoFit" in children:
            frame.reason_codes.append("dynamic_autofit_unsupported")
        if any(name not in {"noAutofit", "normAutofit", "spAutoFit"} for name in children):
            frame.reason_codes.append("text_body_effects_unsupported")
        for paragraph in shape.rich_text.paragraphs:
            properties = etree.Element("properties")
            for layer in shape.text_frame.style_layers:
                for key in ("defPPr", f"lvl{paragraph.level + 1}pPr"):
                    if value := layer.paragraph_styles.get(key):
                        _merge(properties, _xml(value))
            if paragraph.properties.raw_xml:
                _merge(properties, _xml(paragraph.properties.raw_xml))
            reasons = []
            runs = [run for run in paragraph.runs if not run.is_line_break]
            if any(run.is_line_break for run in paragraph.runs):
                reasons.append("retained_source_line_break_unsupported")
            # Patcher writes all payload to the first a:t and empties subsequent
            # text nodes; when absent it creates an a:r from endParaRPr.
            run = runs[0] if runs else None
            if any(run.is_field for run in runs):
                reasons.append("dynamic_text_field_unsupported")
            for source_run in runs:
                if source_run.raw_properties_xml:
                    raw_props = _xml(source_run.raw_properties_xml)
                    if (raw_props.get("cap", "none") != "none" or raw_props.get("normalizeH", "0") in {"1", "true"}
                        or any(_name(child) in {"effectLst", "effectDag"} for child in raw_props)):
                        reasons.append("custom_character_metrics_unsupported")
            values = _font_from_properties(properties)
            if run:
                values.update(run.raw_style.model_dump(exclude_none=True))
            elif paragraph.end_properties_xml:
                end = _xml(paragraph.end_properties_xml)
                end.tag = "defRPr"
                container = etree.Element("properties")
                container.append(end)
                values.update(_font_from_properties(container))
            family = values.get("font_family")
            family = font_override or shape.text_frame.font_aliases.get(family, family)
            if not family or family.startswith("+") or not values.get("font_size_pt"):
                reasons.append("source_font_properties_missing")
            if values.get("character_spacing") or values.get("baseline"):
                reasons.append("custom_character_metrics_unsupported")
            for other in runs[1:]:
                if other.effective_style.font_size_pt and other.effective_style.font_size_pt != values.get("font_size_pt"):
                    reasons.append("retained_mixed_run_metrics_unsupported")
            if paragraph.end_style and paragraph.end_style.font_size_pt and paragraph.end_style.font_size_pt != values.get("font_size_pt"):
                reasons.append("retained_mixed_run_metrics_unsupported")
            if any(_name(child) in {"buChar", "buAutoNum", "buBlip", "tabLst"} for child in properties):
                reasons.append("paragraph_bullets_or_tabs_unsupported")
            if properties.get("algn", "l") not in {"l", "ctr", "r"} or properties.get("rtl", "0") in {"1", "true"}:
                reasons.append("paragraph_alignment_unsupported")
            for prop in properties.iter():
                if prop.get("cap", "none") != "none" or prop.get("normalizeH", "0") in {"1", "true"}:
                    reasons.append("custom_character_metrics_unsupported")
            frame.paragraphs.append(NativeParagraph(
                has_text=bool("".join(run.text for run in runs).strip()),
                font_family=family, font_size_pt=values.get("font_size_pt"),
                bold=bool(values.get("bold", False)), italic=bool(values.get("italic", False)),
                margin_left_emu=int(properties.get("marL", "0")),
                margin_right_emu=int(properties.get("marR", "0")), indent_emu=int(properties.get("indent", "0")),
                line_spacing=_spacing(properties, "lnSpc", ("percent", 100.0)),
                space_before=_spacing(properties, "spcBef", ("points", 0.0)),
                space_after=_spacing(properties, "spcAft", ("points", 0.0)),
                reason_codes=list(dict.fromkeys(reasons)),
            ))
    except (ValueError, TypeError, etree.XMLSyntaxError):
        frame.reason_codes.append("source_text_properties_invalid")
    if not frame.paragraphs:
        frame.reason_codes.append("source_paragraphs_missing")
    return frame


@lru_cache(maxsize=512)
def _font_face(path: str):
    font = ImageFont.truetype(path, 32)
    family, style = font.getname()
    return family, style.lower()


class NativeFontResolver(FontResolver):
    """Verify font metadata: Arial Narrow / Arial Unicode are not Arial."""

    def resolve(self, family, *, bold=False, italic=False):
        requested = (family or "").strip()
        target = self._normalize(requested)
        for key, paths in self._font_index.items():
            if not target or not (key.startswith(target) or target.startswith(key)):
                continue
            for path in paths:
                try:
                    actual, style = _font_face(str(path))
                except (OSError, ValueError):
                    continue
                if self._normalize(actual) != target:
                    continue
                is_bold = any(token in style for token in ("bold", "demi", "black", "heavy"))
                is_italic = any(token in style for token in ("italic", "oblique"))
                if is_bold == bold and is_italic == italic:
                    return FontResolution(requested, actual, path, "exact")
        return FontResolution(requested, requested, None, "unavailable", "Exact source font face is unavailable.")


@lru_cache(maxsize=128)
def _font_coverage(path: str) -> frozenset[int]:
    font = pymupdf.Font(fontfile=path)
    return frozenset(font.valid_codepoints())


class NativeTextFitter:
    def __init__(self, *, dpi: int = 144, safety_margin: float = 0.06):
        if dpi <= 0 or not 0 <= safety_margin < 1:
            raise ValueError("Invalid native text measurement configuration")
        self.measurement = TextMeasurementService(resolver=NativeFontResolver(), dpi=dpi, safety_margin=safety_margin)

    def fit(self, frame: NativeTextFrame, paragraphs: list[str]) -> NativeTextFitReport:
        desired = paragraphs or [""]
        safe = 1 - self.measurement.safety_margin
        report = NativeTextFitReport(
            status="unverifiable", source_element_id=frame.source_element_id, font_override=frame.font_override,
            source_fingerprint=frame.source_fingerprint, payload_sha256=payload_sha256(desired),
            frame_sha256=hashlib.sha256(frame.model_dump_json().encode()).hexdigest(),
            safety_margin=self.measurement.safety_margin,
            available_width_emu=max(0, math.floor((frame.width_emu - frame.left_emu - frame.right_emu) * safe)),
            available_height_emu=max(0, math.floor((frame.height_emu - frame.top_emu - frame.bottom_emu) * safe)),
            reason_codes=list(frame.reason_codes),
        )
        if report.reason_codes:
            return report
        indices = replacement_paragraph_sources([p.has_text for p in frame.paragraphs], desired)
        for text, index in zip(desired, indices, strict=True):
            paragraph = frame.paragraphs[index]
            report.reason_codes.extend(paragraph.reason_codes)
            if report.reason_codes:
                continue
            if paragraph.margin_left_emu < 0 or paragraph.margin_right_emu < 0 or paragraph.margin_left_emu + paragraph.indent_emu < 0:
                report.reason_codes.append("negative_text_origin_unsupported")
                continue
            # Complex scripts need script-specific font routing and line breaking.
            if any(unicodedata.bidirectional(c) in {"R", "AL", "AN"} or
                   (ord(c) >= 0x900 and ord(c) < 0x2000) or ord(c) >= 0x2E80 or
                   unicodedata.category(c) in {"Cc", "Cf", "Mn", "Mc", "Me"}
                   for c in text if c != "\n"):
                report.reason_codes.append("text_script_or_control_unsupported")
                continue
            try:
                record = self._paragraph(paragraph, text, index, frame, report.available_width_emu)
            except (OSError, ValueError, RuntimeError) as exc:
                report.reason_codes.append(str(exc) if str(exc).startswith("native_") else "font_measurement_failed")
                continue
            report.paragraphs.append(record)
            report.measured_width_emu = max(report.measured_width_emu, record.width_emu)
            report.measured_height_emu += record.height_emu
        report.reason_codes = list(dict.fromkeys(report.reason_codes))
        if not report.reason_codes:
            overflow = (report.measured_width_emu > report.available_width_emu or
                        report.measured_height_emu > report.available_height_emu)
            report.status = "overflow" if overflow else "fits"
            if overflow:
                report.reason_codes.append("source_text_frame_overflow")
        return report

    def _paragraph(self, paragraph, text, index, frame, width):
        family, size = paragraph.font_family, paragraph.font_size_pt
        if not family or not size or size <= 0:
            raise ValueError("native_source_font_properties_missing")
        resolution = self.measurement.resolver.resolve(family, bold=paragraph.bold, italic=paragraph.italic)
        if resolution.status != "exact" or resolution.path is None:
            raise ValueError("native_source_font_unavailable")
        if any(ord(c) not in _font_coverage(str(resolution.path)) for c in text if c not in "\n"):
            raise ValueError("native_source_font_missing_glyph")
        reserved = paragraph.margin_left_emu + paragraph.margin_right_emu + max(0, paragraph.indent_emu)
        available = max(0, width - reserved)

        def measure(value):
            return self.measurement.measure(value, family=family, font_size_pt=size,
                available_width_emu=available, bold=paragraph.bold, italic=paragraph.italic, wrap=False)[0]

        # Preserve whitespace. An unbreakable word that exceeds the frame fails
        # conservatively; no unobserved hyphenation / PowerPoint word breaking.
        lines = []
        for line in text.split("\n"):
            if not frame.wrap:
                lines.append(line)
                continue
            current = ""
            for token in re.findall(r"[^ ]+| +", line):
                if current and measure(current + token).width_emu > available:
                    lines.append(current)
                    current = token
                else:
                    current += token
            lines.append(current)
        measured = measure("\n".join(lines))
        base_height = measure("Mg").height_emu

        def spacing(spec):
            unit, value = spec
            if value < 0:
                raise ValueError("native_negative_paragraph_spacing")
            return value * (base_height / 100 if unit == "percent" else EMU_PER_POINT)

        pitch = spacing(paragraph.line_spacing)
        height = base_height + max(0, len(lines) - 1) * pitch
        height += spacing(paragraph.space_before) + spacing(paragraph.space_after)
        return NativeParagraphMeasurement(source_paragraph_index=index, font_family=family,
            font_size_pt=size, font_path=str(resolution.path), line_count=len(lines),
            width_emu=measured.width_emu + reserved, height_emu=math.ceil(height))
