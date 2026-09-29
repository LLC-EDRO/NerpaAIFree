"""Convert parser scenes into normalized deterministic analysis observations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.presentation.models import EffectiveObject, PresentationModel, ShapeElementModel
from app.presentation.template.confidence import score_confidence
from app.presentation.template.features import evidence, normalized_geometry, source_geometry
from app.presentation.template.models import (
    ConfidenceScore,
    ElementRole,
    NormalizedGeometry,
    RuleEvidence,
    SourceGeometry,
)


@dataclass(slots=True)
class ElementObservation:
    slide_id: str
    slide_index: int
    layout_id: str | None
    master_id: str | None
    element_id: str
    source_object_id: str
    parent_group_path: str | None
    object_kind: str
    parser_support: str
    source_level: str
    source_part: str
    source_open_xml_shape_id: int | None
    source_element_fingerprint: str | None
    source_relationship_id: str | None
    bbox: NormalizedGeometry
    source_bbox: SourceGeometry | None
    placeholder_type: str | None
    placeholder_idx: int | None
    text: str | None
    text_fragments: list[dict[str, Any]]
    parser_content_hint: str
    media_id: str | None
    asset_hash: str | None
    inherited: bool
    effective_z_index: int
    style: dict[str, Any]
    text_style: dict[str, Any]
    fill: str | None
    fill_theme_ref: str | None
    line: str | None
    line_theme_ref: str | None
    alignment: str | None
    line_spacing: float | None
    paragraph_space_before: float | None
    paragraph_space_after: float | None
    character_spacing: float | None
    image_crop: dict[str, float | None] | None
    role: ElementRole
    role_confidence: ConfidenceScore
    role_evidence: list[RuleEvidence] = field(default_factory=list)
    capacity_facts: dict[str, Any] = field(default_factory=dict)
    typography_ref: str | None = None
    color_refs: list[str] = field(default_factory=list)
    recurring_pattern_id: str | None = None
    layout_slot_id: str | None = None
    slot_occurrence_id: str | None = None
    slot_member_role: str = "protected"


def _flatten(items: list[ShapeElementModel]) -> list[ShapeElementModel]:
    flattened: list[ShapeElementModel] = []
    for item in items:
        flattened.append(item)
        flattened.extend(_flatten(item.children))
    return flattened


def _raw_object_map(presentation: PresentationModel) -> dict[tuple[str, str], ShapeElementModel]:
    mapping: dict[tuple[str, str], ShapeElementModel] = {}
    for master in presentation.masters:
        for item in _flatten(master.placeholders):
            mapping[("master", item.object_id)] = item
    for layout in presentation.layouts:
        for item in _flatten(layout.placeholders):
            mapping[("layout", item.object_id)] = item
    for slide in presentation.slides:
        for item in _flatten(slide.objects):
            mapping[("slide", item.object_id)] = item
    return mapping


def _placeholder_role(value: str | None) -> ElementRole | None:
    normalized = (value or "").lower().replace("_", "").replace("-", "")
    if normalized in {"title", "ctrtitle", "centertitle"}:
        return "title"
    if normalized in {"subtitle", "subTitle".lower()}:
        return "subtitle"
    if normalized in {"body", "obj", "object", "text"}:
        return "body"
    if normalized in {"ftr", "footer", "date", "dt"}:
        return "footer"
    if normalized in {"sldnum", "slidenumber"}:
        return "page_number"
    return None


def _infer_role(
    effective: EffectiveObject,
    bbox: NormalizedGeometry,
    raw: ShapeElementModel | None,
    slide_id: str,
) -> tuple[ElementRole, ConfidenceScore, list[RuleEvidence]]:
    placeholder_role = _placeholder_role(effective.placeholder_type)
    direct_role: ElementRole | None = placeholder_role
    reason = "explicit_placeholder"
    if direct_role is None and effective.object_kind == "picture":
        direct_role, reason = "image", "parser_object_kind"
    elif direct_role is None and effective.object_kind == "table":
        direct_role, reason = "table", "parser_object_kind"
    elif direct_role is None and effective.object_kind == "chart":
        direct_role, reason = "chart", "parser_object_kind"
    elif direct_role is None and effective.object_kind in {"connector", "line"}:
        direct_role, reason = "decorative", "parser_object_kind"

    if direct_role is not None:
        rule_evidence = evidence(
            category="element_role",
            source_type="parser_data",
            slide_ids=[slide_id],
            element_ids=[effective.object_id],
            sample_count=1,
            support_ratio=1.0,
            details={
                "reason": reason,
                "placeholder_type": effective.placeholder_type,
                "object_kind": effective.object_kind,
            },
        )
        return (
            direct_role,
            score_confidence(
                sample_size=1,
                support_ratio=1.0,
                variance=0.0,
                consistency=1.0,
                direct_evidence=True,
            ),
            [rule_evidence],
        )

    text = (effective.raw_text or "").strip()
    area = bbox.width * bbox.height
    if text:
        style = raw.text_style if raw else None
        font_size = style.font_size_pt if style else None
        if bbox.y <= 0.35 and (font_size or 0) >= 24:
            role, reason, consistency = "title", "large_text_in_top_region", 0.72
        elif bbox.y >= 0.82 and len(text) <= 120:
            role, reason, consistency = "footer", "short_text_in_footer_region", 0.67
        elif len(text) <= 24 and area <= 0.10:
            role, reason, consistency = "label", "short_text_in_small_region", 0.62
        else:
            role, reason, consistency = "body", "visible_text_region", 0.66
    elif effective.object_kind == "shape":
        if bbox.x <= 0.02 and bbox.y <= 0.02 and bbox.width >= 0.96 and bbox.height >= 0.96:
            role, reason, consistency = "background", "full_slide_shape", 0.78
        else:
            role, reason, consistency = "decorative", "non_text_shape", 0.58
    else:
        role, reason, consistency = "unknown", "ambiguous_parser_object", 0.35

    rule_evidence = evidence(
        category="element_role",
        source_type="statistical_pattern",
        slide_ids=[slide_id],
        element_ids=[effective.object_id],
        sample_count=1,
        support_ratio=consistency,
        details={
            "reason": reason,
            "object_kind": effective.object_kind,
            "text_length": len(text),
            "bbox": bbox.model_dump(mode="json"),
        },
    )
    return (
        role,
        score_confidence(
            sample_size=1,
            support_ratio=consistency,
            variance=0.0,
            consistency=consistency,
            direct_evidence=False,
        ),
        [rule_evidence],
    )


def _role_refinement_evidence(
    item: ElementObservation,
    *,
    role: ElementRole,
    reason: str,
    signals: list[str],
) -> RuleEvidence:
    return evidence(
        category="element_role_refinement",
        source_type="statistical_pattern",
        slide_ids=[item.slide_id],
        element_ids=[item.element_id],
        sample_count=1,
        support_ratio=0.92,
        details={"role": role, "reason": reason, "signals": signals},
    )


def _set_refined_role(
    item: ElementObservation,
    role: ElementRole,
    *,
    reason: str,
    signals: list[str],
    consistency: float = 1.0,
) -> None:
    item.role = role
    item.role_confidence = score_confidence(
        sample_size=1,
        support_ratio=consistency,
        variance=0.0,
        consistency=consistency,
        direct_evidence=True,
    )
    item.role_evidence.append(
        _role_refinement_evidence(item, role=role, reason=reason, signals=signals)
    )


def _contains(outer: NormalizedGeometry, inner: NormalizedGeometry, tolerance: float = 0.015) -> bool:
    return (
        outer.x <= inner.x + tolerance
        and outer.y <= inner.y + tolerance
        and outer.x + outer.width + tolerance >= inner.x + inner.width
        and outer.y + outer.height + tolerance >= inner.y + inner.height
    )


def _refine_slide_roles(observations: list[ElementObservation]) -> None:
    """Add deterministic multi-signal roles without making geometry authoritative.

    Parser kind remains the payload authority.  Geometry and typography only
    strengthen a semantic label when at least one additional structural signal
    is present.
    """

    slide_text = [
        item
        for item in observations
        if item.source_level == "slide" and item.object_kind == "shape" and (item.text or "").strip()
    ]
    font_sizes = {
        item.element_id: float(item.text_style.get("font_size_pt") or item.style.get("font_size_pt") or 0.0)
        for item in slide_text
    }
    ordered_sizes = sorted(font_sizes.values(), reverse=True)
    second = ordered_sizes[1] if len(ordered_sizes) > 1 else 0.0

    strong_title: ElementObservation | None = None
    for item in slide_text:
        size = font_sizes[item.element_id]
        if item.role == "title" and size >= 24 and item.bbox.y <= 0.42 and (
            not second or size >= second * 1.35
        ):
            _set_refined_role(
                item,
                "title",
                reason="typography_hierarchy_and_top_region",
                signals=["text_payload", "largest_font", "top_region", "title_heuristic"],
            )
            strong_title = item
            break

    if strong_title is not None:
        subtitle_candidates = [
            item
            for item in slide_text
            if item is not strong_title
            and item.bbox.y >= strong_title.bbox.y + strong_title.bbox.height * 0.75
            and abs(item.bbox.x - strong_title.bbox.x) <= 0.06
            and abs(item.bbox.width - strong_title.bbox.width) <= 0.10
            and len((item.text or "").strip()) <= 140
            and (item.alignment or "").lower() in {"ctr", "center"}
        ]
        if len(subtitle_candidates) == 1:
            _set_refined_role(
                subtitle_candidates[0],
                "subtitle",
                reason="single_aligned_text_below_strong_title",
                signals=["text_payload", "title_relationship", "shared_axis", "center_alignment"],
                consistency=1.0,
            )

    median_size = sorted(font_sizes.values())[len(font_sizes) // 2] if font_sizes else 0.0
    for item in slide_text:
        if item.role != "body":
            continue
        size = font_sizes[item.element_id]
        text_length = len((item.text or "").strip())
        if text_length >= 60 and size and size <= max(median_size, 18.0):
            _set_refined_role(
                item,
                "body",
                reason="long_text_payload_with_body_typography",
                signals=["text_payload", "long_copy", "body_font_scale"],
                consistency=1.0,
            )

    for item in observations:
        if item.object_kind != "picture":
            continue
        area = item.bbox.width * item.bbox.height
        full_slide = (
            item.bbox.x <= 0.02
            and item.bbox.y <= 0.02
            and item.bbox.width >= 0.96
            and item.bbox.height >= 0.96
        )
        if full_slide:
            _set_refined_role(
                item,
                "background_image",
                reason="full_slide_picture",
                signals=["picture_payload", "full_slide_extent"],
            )
        elif item.source_level != "slide":
            _set_refined_role(
                item,
                "decorative_image",
                reason="inherited_picture_furniture",
                signals=["picture_payload", "inherited_source_level"],
            )
        elif area <= 0.018:
            _set_refined_role(
                item,
                "icon",
                reason="small_slide_local_picture",
                signals=["picture_payload", "small_area"],
                consistency=0.88,
            )
        else:
            _set_refined_role(
                item,
                "photo",
                reason="slide_local_picture_payload",
                signals=["picture_payload", "slide_local_source"],
            )

    pictures = [item for item in observations if item.object_kind == "picture"]
    for item in observations:
        if item.source_level != "slide" or item.object_kind != "shape" or (item.text or "").strip():
            continue
        contained = [picture for picture in pictures if _contains(item.bbox, picture.bbox)]
        if contained and item.role not in {"background", "background_image"}:
            _set_refined_role(
                item,
                "container",
                reason="non_payload_shape_contains_picture_payload",
                signals=["shape_without_payload", "contains_picture"],
                consistency=0.9,
            )
def normalized_text(value: str | None) -> str:
    if not value:
        return ""
    compact = " ".join(value.casefold().split())
    return re.sub(r"\d+", "{number}", compact)


def _capacity_facts(raw, geometry, style, table):
    if table is not None:
        return {"max_rows": table.row_count, "max_columns": table.grid_column_count,
                "measurement_source": "source_table_grid", "confidence": 1.0}
    font = style.get("font_size_pt")
    if not geometry or not font or float(font) <= 0:
        return {"measurement_source": "unavailable_geometry_or_font", "confidence": 0.0}
    body = {}
    if raw and raw.text_frame:
        for layer in raw.text_frame.style_layers:
            body.update(layer.body_attributes)
    # OOXML default insets; EMU -> points. This is a conservative geometric
    # estimate, not a measured PowerPoint line layout or a fit guarantee.
    width = max(0, (geometry.width_emu or 0) - int(body.get("lIns", 91440)) - int(body.get("rIns", 91440))) / 12700
    height = max(0, (geometry.height_emu or 0) - int(body.get("tIns", 45720)) - int(body.get("bIns", 45720))) / 12700
    lines = int(height / (float(font) * 1.25))
    columns = int(width / (float(font) * 0.6))
    return {"max_chars": lines * columns, "max_lines": lines,
            "min_font_size": float(font), "max_font_size": float(font),
            "measurement_source": "geometry_font_estimate", "confidence": 0.5}


def extract_observations(presentation: PresentationModel) -> dict[str, list[ElementObservation]]:
    raw_map = _raw_object_map(presentation)
    tables = {(item.source_part, item.linked_object_id): item for item in presentation.tables}
    assets_by_part = {asset.source_part: asset.sha256 for asset in presentation.assets}
    assets_by_media = {asset.media_id: asset.sha256 for asset in presentation.assets}
    result: dict[str, list[ElementObservation]] = {}
    for scene in sorted(presentation.effective_scenes, key=lambda item: item.slide_index):
        observations: list[ElementObservation] = []
        for effective in sorted(scene.effective_objects, key=lambda item: item.effective_z_index):
            if not effective.render_visible or effective.normalized_bbox is None:
                continue
            bbox = normalized_geometry(effective.normalized_bbox)
            if bbox is None:
                continue
            raw = raw_map.get((effective.source_level, effective.source_object_id))
            source_value = None
            if raw and raw.geometry_full:
                source_value = raw.geometry_full.absolute_bbox or raw.geometry_full.local_bbox
            if source_value is None and raw:
                source_value = raw.geometry
            source_bbox = source_geometry(source_value)
            role, confidence, role_evidence = _infer_role(effective, bbox, raw, scene.slide_id)
            raw_style = raw.text_style.model_dump(mode="json") if raw and raw.text_style else {}
            effective_style = {key: value for key, value in effective.effective_style.items() if value is not None}
            text_style = {**effective_style, **{key: value for key, value in raw_style.items() if value is not None}}
            alignment = None
            line_spacing = None
            paragraph_space_before = None
            paragraph_space_after = None
            if raw and raw.rich_text and raw.rich_text.paragraphs:
                paragraph = raw.rich_text.paragraphs[0]
                alignment = paragraph.alignment or paragraph.properties.alignment
                line_spacing = paragraph.properties.line_spacing
                paragraph_space_before = paragraph.properties.space_before
                paragraph_space_after = paragraph.properties.space_after
            fill = raw.fill.color if raw and raw.fill else effective.effective_style.get("fill")
            line = raw.line.color if raw and raw.line else effective.effective_style.get("line")
            media_id = effective.media_id
            text_fragments: list[dict[str, Any]] = []
            if raw and raw.rich_text:
                for paragraph_index, paragraph in enumerate(raw.rich_text.paragraphs):
                    run_texts = [run.text or "" for run in paragraph.runs]
                    paragraph_text = "".join(run_texts).strip()
                    if paragraph_text:
                        text_fragments.append(
                            {
                                "paragraph_index": paragraph_index,
                                "run_indices": list(range(len(paragraph.runs))),
                                "text": paragraph_text,
                            }
                        )
            if not text_fragments and effective.raw_text:
                text_fragments = [
                    {"paragraph_index": 0, "run_indices": [], "text": effective.raw_text.strip()}
                ]
            observations.append(
                ElementObservation(
                    slide_id=scene.slide_id,
                    slide_index=scene.slide_index,
                    layout_id=scene.layout_id,
                    master_id=scene.master_id,
                    element_id=effective.object_id,
                    source_object_id=effective.source_object_id,
                    parent_group_path=effective.parent_group_path,
                    object_kind=effective.object_kind,
                    parser_support=raw.parser_support if raw else "opaque",
                    source_level=effective.source_level,
                    source_part=effective.source_part,
                    source_open_xml_shape_id=raw.shape_id if raw else None,
                    source_element_fingerprint=(raw.raw_xml_ref or {}).get("sha256") if raw else None,
                    source_relationship_id=raw.relationship_id if raw else None,
                    bbox=bbox,
                    source_bbox=source_bbox,
                    placeholder_type=effective.placeholder_type,
                    placeholder_idx=raw.placeholder_idx if raw else None,
                    text=effective.raw_text,
                    text_fragments=text_fragments,
                    parser_content_hint=getattr(effective, "parser_content_hint", effective.content_role),
                    media_id=media_id,
                    asset_hash=assets_by_media.get(media_id or "") or assets_by_part.get(media_id or ""),
                    inherited=effective.inherited,
                    effective_z_index=effective.effective_z_index,
                    style=effective_style,
                    text_style=text_style,
                    fill=fill,
                    fill_theme_ref=raw.fill.theme_color_ref if raw and raw.fill else None,
                    line=line,
                    line_theme_ref=raw.line.theme_color_ref if raw and raw.line else None,
                    alignment=alignment,
                    line_spacing=line_spacing,
                    paragraph_space_before=paragraph_space_before,
                    paragraph_space_after=paragraph_space_after,
                    character_spacing=text_style.get("character_spacing"),
                    image_crop=raw.image_crop if raw else None,
                    role=role,
                    role_confidence=confidence,
                    role_evidence=role_evidence,
                    capacity_facts=_capacity_facts(raw, source_bbox, text_style, tables.get((effective.source_part, effective.source_object_id))),
                )
            )
        _refine_slide_roles(observations)
        result[scene.slide_id] = observations
    return result
