"""Placeholder inheritance and raw/effective slide-scene composition."""

from __future__ import annotations

from collections.abc import Iterable

from app.presentation.models import (
    EffectiveObject,
    EffectiveSceneInvariants,
    EffectiveSlideScene,
    InheritanceStep,
    LayoutModel,
    MasterModel,
    NormalizedBBox,
    RawSceneObject,
    RawSlideScene,
    ShapeElementModel,
    SlideModel,
    TextFrameStyleLayer,
    TextStyle,
)
from app.presentation.parser.objects import flatten_objects
from app.presentation.parser.rich_text import raw_text


def placeholder_family(value: str) -> str:
    lowered = value.lower()
    if lowered in {"title", "ctrtitle"}:
        return "title"
    if lowered in {"subtitle"}:
        return "subtitle"
    if lowered == "body" or "body" in lowered:
        return "body"
    if lowered in {"dt", "ftr"}:
        return "footer"
    if lowered == "sldnum":
        return "slide_number"
    return lowered


def is_placeholder(shape: ShapeElementModel) -> bool:
    return shape.placeholder_idx is not None or placeholder_family(shape.placeholder_type or shape.type) in {
        "title",
        "subtitle",
        "body",
        "footer",
        "slide_number",
    }


def _center(shape: ShapeElementModel) -> tuple[float, float] | None:
    geometry = shape.geometry
    if geometry is None or None in (geometry.x_emu, geometry.y_emu, geometry.width_emu, geometry.height_emu):
        return None
    return (geometry.x_emu + geometry.width_emu / 2, geometry.y_emu + geometry.height_emu / 2)


def _score(target: ShapeElementModel, candidate: ShapeElementModel) -> float:
    score = 100 if target.name and target.name == candidate.name else 0
    score += 50 if target.type.lower() == candidate.type.lower() else 0
    a, b = _center(target), _center(candidate)
    if a and b:
        score -= (abs(a[0] - b[0]) + abs(a[1] - b[1])) / 100_000
    return score


def match_placeholder(target: ShapeElementModel, candidates: Iterable[ShapeElementModel]) -> ShapeElementModel | None:
    choices = [candidate for candidate in candidates if is_placeholder(candidate)]
    # A slide inherits its layout placeholder by idx, even after a content
    # placeholder becomes a picture/table/chart. Element kind/name is not an ID.
    layout_choices = [candidate for candidate in choices if candidate.source_level == "layout"]
    if target.source_level == "slide" and layout_choices and target.placeholder_idx is not None:
        exact = [candidate for candidate in layout_choices if candidate.placeholder_idx == target.placeholder_idx]
        return exact[0] if len(exact) == 1 else None
    # Layout -> master inheritance is by semantic type, not the layout's idx.
    # Content/image/chart/subtitle placeholders inherit the master's body style.
    master_choices = [candidate for candidate in choices if candidate.source_level == "master"]
    if master_choices:
        family = placeholder_family(target.placeholder_type or target.type)
        if family in {"obj", "pic", "chart", "tbl", "dgm", "media", "clipart", "subtitle"}:
            family = "body"
        same = [candidate for candidate in master_choices if placeholder_family(candidate.placeholder_type or candidate.type) == family]
        return max(same, key=lambda candidate: _score(target, candidate)) if same else None
    if target.placeholder_idx is not None:
        exact = next(
            (
                candidate
                for candidate in choices
                if candidate.placeholder_idx == target.placeholder_idx
                and placeholder_family(candidate.placeholder_type or candidate.type) == placeholder_family(target.placeholder_type or target.type)
            ),
            None,
        )
        if exact:
            return exact
    same = [candidate for candidate in choices if candidate.type.lower() == target.type.lower()]
    if same:
        return max(same, key=lambda candidate: _score(target, candidate))
    family = [
        candidate for candidate in choices if placeholder_family(candidate.type) == placeholder_family(target.type)
    ]
    return max(family, key=lambda candidate: _score(target, candidate)) if family else None


def _inherit(target: ShapeElementModel, source: ShapeElementModel, source_name: str) -> None:
    if target.text_frame is not None and source.text_frame is not None:
        layers = [layer.model_copy(deep=True) for layer in source.text_frame.style_layers]
        # Parent paragraph defaults are inherited by level, not replacement ordinal.
        inherited = {}
        for paragraph in source.rich_text.paragraphs if source.rich_text else []:
            if paragraph.properties.raw_xml:
                inherited.setdefault(f"lvl{paragraph.level + 1}pPr", paragraph.properties.raw_xml)
        if inherited:
            layers.append(TextFrameStyleLayer(
                source_part=source.source_part, source_object_id=source.object_id, paragraph_styles=inherited,
            ))
        target.text_frame.style_layers = [*layers, *target.text_frame.style_layers]
        if target.text_frame.shape_geometry is None:
            target.text_frame.shape_geometry = source.text_frame.shape_geometry
        target.text_frame.font_aliases = {**source.text_frame.font_aliases, **target.text_frame.font_aliases}
    if target.geometry is None and source.geometry is not None:
        target.geometry = source.geometry.model_copy(deep=True)
        target.geometry_full = source.geometry_full.model_copy(deep=True) if source.geometry_full else None
        target.provenance["geometry"] = {
            "source": source_name,
            "source_part": source.source_part,
            "source_object_id": source.object_id,
            "inherited": True,
        }
    if target.fill is None and source.fill is not None:
        target.effective_style["fill"] = source.fill.color
        target.provenance["fill"] = {
            "value": source.fill.color,
            "source": source.source_level,
            "source_part": source.source_part,
            "source_object_id": source.object_id,
            "inherited": True,
            "trace": [{"step": "placeholder_inheritance", "detail": source_name, "value": source.fill.color}],
        }
    if target.line is None and source.line is not None:
        target.effective_style["line"] = source.line.color
        target.provenance["line"] = {
            "value": source.line.color,
            "source": source.source_level,
            "source_part": source.source_part,
            "source_object_id": source.object_id,
            "inherited": True,
            "trace": [{"step": "placeholder_inheritance", "detail": source_name, "value": source.line.color}],
        }
    if target.has_text_content:
        source_style = source.text_style or (source.text_capability.default_style if source.text_capability else None)
        if source_style:
            if target.text_style is None:
                target.text_style = source_style.model_copy(deep=True)
            else:
                updates = {
                    field: getattr(target.text_style, field)
                    if getattr(target.text_style, field) is not None
                    else getattr(source_style, field)
                    for field in TextStyle.model_fields
                }
                target.text_style = TextStyle(**updates)
            target.effective_style.update(
                {
                    "font_family": target.text_style.font_family,
                    "font_size": target.text_style.font_size_pt,
                    "color": target.text_style.color,
                }
            )
            if target.rich_text:
                for paragraph in target.rich_text.paragraphs:
                    for run in paragraph.runs:
                        effective = run.effective_style.model_copy(
                            update={
                                "font_family": run.effective_style.font_family or source_style.font_family,
                                "font_size_pt": run.effective_style.font_size_pt or source_style.font_size_pt,
                                "color": run.effective_style.color or source_style.color,
                            }
                        )
                        run.effective_style = effective
                        run.style = effective
                target.rich_text.dominant = target.text_style.model_copy()
            target.provenance["font_family"] = {
                "value": target.text_style.font_family,
                "source": source.source_level,
                "source_part": source.source_part,
                "source_object_id": source.object_id,
                "inherited": True,
                "trace": [{"step": "placeholder_inheritance", "detail": source_name}],
            }
    target.provenance["inherited_placeholder"] = {
        "source": source_name,
        "object_id": source.object_id,
        "source_part": source.source_part,
    }


def apply_placeholder_inheritance(
    masters: list[MasterModel], layouts: list[LayoutModel], slides: list[SlideModel]
) -> None:
    masters_by_id = {master.master_id: master for master in masters}
    layouts_by_id = {layout.layout_id: layout for layout in layouts}
    for layout in layouts:
        master = masters_by_id.get(layout.master_id or "")
        if master:
            for shape in layout.placeholders:
                if is_placeholder(shape) and (matched := match_placeholder(shape, master.placeholders)):
                    _inherit(shape, matched, "master_placeholder")
    for slide in slides:
        layout = layouts_by_id.get(slide.layout_id or "")
        master = masters_by_id.get(layout.master_id or "") if layout else None
        for shape in slide.objects:
            if not is_placeholder(shape):
                continue
            matched = match_placeholder(shape, layout.placeholders) if layout else None
            if matched:
                _inherit(shape, matched, "layout_placeholder")
            elif master and (matched := match_placeholder(shape, master.placeholders)):
                _inherit(shape, matched, "master_placeholder")


def _norm(box, width: int, height: int) -> NormalizedBBox | None:
    if box is None:
        return None
    return NormalizedBBox(
        x=round((box.x_emu or 0) / width, 4),
        y=round((box.y_emu or 0) / height, 4),
        width=round((box.width_emu or 0) / width, 4),
        height=round((box.height_emu or 0) / height, 4),
    )


def compose_raw_scenes(
    slides: list[SlideModel],
    layouts: list[LayoutModel],
    masters: list[MasterModel],
    width: int,
    height: int,
) -> list[RawSlideScene]:
    layouts_by_id = {layout.layout_id: layout for layout in layouts}
    masters_by_id = {master.master_id: master for master in masters}
    scenes: list[RawSlideScene] = []
    for slide in slides:
        layout = layouts_by_id.get(slide.layout_id or "")
        master = masters_by_id.get(layout.master_id or "") if layout else None
        refs: list[RawSceneObject] = []
        for level, source_part, roots in (
            ("master", master.source_part if master else None, master.placeholders if master else []),
            ("layout", layout.source_part if layout else None, layout.placeholders if layout else []),
            ("slide", slide.source_part, slide.objects),
        ):
            for shape in flatten_objects(roots):
                full = shape.geometry_full
                refs.append(
                    RawSceneObject(
                        source_level=level,
                        source_part=source_part or shape.source_part,
                        source_object_id=shape.source_object_id,
                        physical_identity=shape.physical_identity,
                        parent_group_path=shape.parent_group_path,
                        object_kind=shape.object_kind,
                        local_bbox=_norm(full.local_bbox, width, height) if full else None,
                        absolute_bbox=_norm(full.absolute_bbox, width, height) if full else None,
                        painted_bbox=_norm(full.painted_bbox, width, height) if full else None,
                    )
                )
        scenes.append(
            RawSlideScene(
                slide_id=slide.slide_id,
                slide_index=slide.slide_index,
                layout_id=slide.layout_id,
                master_id=master.master_id if master else None,
                canvas={"width_emu": width, "height_emu": height},
                raw_objects=refs,
            )
        )
    return scenes


def _visible_pixels(shape: ShapeElementModel) -> bool:
    if shape.object_kind == "group":
        return False
    if raw_text(shape.rich_text):
        return True
    if shape.media_id or shape.fill and shape.fill.color or shape.line and shape.line.color:
        return True
    return shape.object_kind in {"table", "chart", "embedded_object"} and bool(
        shape.geometry and (shape.geometry.width_emu or 0) > 0 and (shape.geometry.height_emu or 0) > 0
    )


def _content_role(shape: ShapeElementModel, level: str, text: str | None) -> tuple[str, bool, bool, str]:
    family = placeholder_family(shape.placeholder_type or shape.type)
    utility = family in {"footer", "slide_number"}
    if utility:
        return (
            "field_value",
            False,
            bool(text or is_placeholder(shape)),
            "declared_placeholder_style" if level != "slide" else "resolved_used_style",
        )
    if level in {"master", "layout"} and is_placeholder(shape):
        return ("placeholder_sample" if text else "formatting_capability"), False, True, "declared_placeholder_style"
    if level in {"master", "layout"} and text:
        return "decorative_text", False, True, "resolved_used_style"
    if text:
        # Parser records the physical origin of text only.  Whether it is user
        # content, an authoring instruction, a sample, or a placeholder label
        # is deliberately resolved by Template Analyzer.
        return "slide_level_content", True, True, "observed_slide_style" if level == "slide" else "resolved_used_style"
    return "unknown", True, False, "none"


def _effective(
    shape: ShapeElementModel,
    *,
    level: str,
    z_base: int,
    width: int,
    height: int,
    inherited: bool,
    replaced: bool,
    replaced_by: str | None,
    replaces: str | None,
    suppressed: bool,
    trace: list[InheritanceStep],
) -> EffectiveObject:
    full = shape.geometry_full
    absolute = _norm(full.absolute_bbox, width, height) if full else _norm(shape.geometry, width, height)
    local = _norm(full.local_bbox, width, height) if full else absolute
    painted = _norm(full.painted_bbox, width, height) if full else absolute
    off_class = full.off_canvas_class if full else None
    off_canvas = off_class in {"fully_off_canvas", "suspicious_off_canvas"}
    clipped = bool(full and full.clipped)
    contributes = _visible_pixels(shape)
    render = contributes and not shape.hidden and not off_canvas and not replaced and not suppressed
    if replaced:
        reason = "replaced_placeholder"
    elif suppressed:
        reason = "master_shapes_suppressed"
    elif shape.hidden:
        reason = "hidden"
    elif off_canvas:
        reason = off_class
    elif not render:
        reason = "group_container" if shape.object_kind == "group" else "no_visible_content"
    elif clipped:
        reason = off_class
    else:
        reason = None
    text = raw_text(shape.rich_text)
    role, semantic_eligible, style_eligible, style_scope = _content_role(shape, level, text)
    # Placeholder prompts stored in a master/layout (for example PowerPoint's
    # "Click to edit Master title style") describe authoring capability.  They
    # are visible in the template editor, but are not painted slide payload and
    # must never enter semantic residue/readiness checks as user-facing text.
    if level in {"master", "layout"} and role == "placeholder_sample":
        render = False
        reason = "placeholder_formatting_sample"
    generation = render and shape.object_kind != "group" and semantic_eligible
    canonical = f"{level}:{shape.object_id}"
    text_style = shape.text_style
    utility = placeholder_family(shape.placeholder_type or shape.type) in {"footer", "slide_number"}
    provenance = {
        **shape.provenance,
        "source_level": level,
        "inherited": inherited,
        "replaces_object_id": replaces,
        "replaced": replaced,
        "replaced_by_object_id": replaced_by,
        "resolution_trace": [item.model_dump(mode="json") for item in trace],
        "visibility_reason": reason,
        "off_canvas": off_canvas,
        "off_canvas_class": off_class,
        "clipped": clipped,
        "physical_identity": shape.physical_identity,
        "parent_group_path": shape.parent_group_path,
        "generation_content": generation,
        "has_text": bool(text),
        "raw_text": text,
        "content_role": role,
    }
    return EffectiveObject(
        object_id=canonical,
        canonical_object_id=canonical,
        physical_identity=shape.physical_identity,
        source_level=level,
        source_part=shape.source_part,
        source_object_id=shape.object_id,
        parent_group_path=shape.parent_group_path,
        object_kind=shape.object_kind,
        local_bbox=local,
        absolute_bbox=absolute,
        normalized_bbox=absolute,
        painted_bbox=painted,
        painted_intersects_canvas=bool(full and full.painted_intersects_canvas),
        visibility_geometry_type=full.visibility_geometry_type if full else "area",
        rotation_deg=full.rotation_deg if full else 0,
        z_index=shape.z_index,
        effective_z_index=z_base + shape.z_index,
        explicit_style={
            "font_family": text_style.font_family if text_style else None,
            "font_size_pt": text_style.font_size_pt if text_style else None,
            "color": text_style.color if text_style else None,
            "fill": shape.fill.color if shape.fill else None,
            "line": shape.line.color if shape.line else None,
        },
        effective_style={
            "font_family": shape.effective_style.get("font_family"),
            "font_size_pt": shape.effective_style.get("font_size"),
            "color": shape.effective_style.get("color"),
            "fill": shape.effective_style.get("fill"),
            "line": shape.effective_style.get("line"),
        },
        render_visible=render,
        semantic_visible=render,
        visibility_reason=reason,
        off_canvas=off_canvas,
        off_canvas_class=off_class,
        clipped=clipped,
        inherited=inherited,
        is_placeholder_override=replaces is not None and not replaced,
        replaces_object_id=replaces,
        replaced=replaced,
        replaced_by_object_id=replaced_by,
        resolution_trace=trace,
        inheritance_trace=trace,
        generation_content=generation,
        has_text=bool(text),
        raw_text=text,
        content_role=role,
        semantic_content_eligible=generation,
        generation_content_eligible=generation,
        style_evidence_eligible=style_eligible,
        style_evidence_scope=style_scope,
        style_capability_evidence={"scope": style_scope, "source_level": level, "source_object_id": shape.object_id}
        if style_eligible
        else None,
        utility_placeholder=utility,
        media_id=shape.media_id,
        placeholder_type=shape.placeholder_type or shape.type if is_placeholder(shape) else None,
        text_capability=shape.text_capability,
        provenance=provenance,
        analyzer_object_id=shape.object_id,
        source_content=text,
        content_origin=level,
        parser_content_hint=role,
        has_slide_level_content=bool(text and level == "slide"),
    )


def compose_effective_scenes(
    slides: list[SlideModel],
    layouts: list[LayoutModel],
    masters: list[MasterModel],
    width: int,
    height: int,
) -> list[EffectiveSlideScene]:
    layouts_by_id = {layout.layout_id: layout for layout in layouts}
    masters_by_id = {master.master_id: master for master in masters}
    scenes: list[EffectiveSlideScene] = []
    for slide in slides:
        layout = layouts_by_id.get(slide.layout_id or "")
        master = masters_by_id.get(layout.master_id or "") if layout else None
        master_shapes = flatten_objects(master.placeholders) if master else []
        layout_shapes = flatten_objects(layout.placeholders) if layout else []
        slide_shapes = flatten_objects(slide.objects)
        replaced_by: dict[str, str] = {}
        replaces: dict[str, str] = {}
        for shape in slide_shapes:
            if not is_placeholder(shape):
                continue
            winner = f"slide:{shape.object_id}"
            layout_match = match_placeholder(shape, layout_shapes) if layout else None
            if layout_match:
                victim = f"layout:{layout_match.object_id}"
                replaced_by[victim], replaces[winner] = winner, victim
                if master and (master_match := match_placeholder(layout_match, master_shapes)):
                    replaced_by[f"master:{master_match.object_id}"] = winner
            elif master and (master_match := match_placeholder(shape, master_shapes)):
                victim = f"master:{master_match.object_id}"
                replaced_by[victim], replaces[winner] = winner, victim
        for shape in layout_shapes:
            key = f"layout:{shape.object_id}"
            if key in replaced_by or not is_placeholder(shape) or not master:
                continue
            if matched := match_placeholder(shape, master_shapes):
                victim = f"master:{matched.object_id}"
                replaced_by[victim], replaces[key] = key, victim

        layers: dict[str, list[EffectiveObject]] = {"master": [], "layout": [], "slide": []}
        show_master = slide.show_master_sp if slide else (layout.show_master_sp if layout else True)
        for level, shapes, base, inherited in (
            ("master", master_shapes, 0, True),
            ("layout", layout_shapes, 10_000, True),
            ("slide", slide_shapes, 20_000, False),
        ):
            for shape in shapes:
                key = f"{level}:{shape.object_id}"
                trace: list[InheritanceStep] = []
                if level == "slide" and is_placeholder(shape):
                    layout_match = match_placeholder(shape, layout_shapes) if layout else None
                    master_match = match_placeholder(layout_match or shape, master_shapes) if master else None
                    if master_match:
                        trace.append(
                            InheritanceStep(
                                level="master",
                                object_id=master_match.object_id,
                                source_part=master_match.source_part,
                                role="replaced",
                            )
                        )
                    if layout_match:
                        trace.append(
                            InheritanceStep(
                                level="layout",
                                object_id=layout_match.object_id,
                                source_part=layout_match.source_part,
                                role="replaced",
                            )
                        )
                elif level == "layout" and is_placeholder(shape) and master:
                    if matched := match_placeholder(shape, master_shapes):
                        trace.append(
                            InheritanceStep(
                                level="master",
                                object_id=matched.object_id,
                                source_part=matched.source_part,
                                role="replaced",
                            )
                        )
                trace.append(
                    InheritanceStep(
                        level=level,
                        object_id=shape.object_id,
                        source_part=shape.source_part,
                        role="replaced" if key in replaced_by else "self",
                    )
                )
                layers[level].append(
                    _effective(
                        shape,
                        level=level,
                        z_base=base,
                        width=width,
                        height=height,
                        inherited=inherited,
                        replaced=key in replaced_by,
                        replaced_by=replaced_by.get(key),
                        replaces=replaces.get(key),
                        suppressed=level == "master" and not is_placeholder(shape) and not show_master,
                        trace=trace,
                    )
                )
        merged = sorted(
            [*layers["master"], *layers["layout"], *layers["slide"]], key=lambda item: item.effective_z_index
        )
        unique: list[EffectiveObject] = []
        seen: set[str] = set()
        duplicates = 0
        for item in merged:
            if item.physical_identity in seen:
                duplicates += 1
                continue
            seen.add(item.physical_identity)
            unique.append(item)
        canonical = {item.canonical_object_id: item for item in unique}
        replacement_invalid = sum(
            1
            for item in unique
            if (
                item.replaces_object_id
                and (item.replaces_object_id not in canonical or not canonical[item.replaces_object_id].replaced)
            )
            or (item.replaced and not item.replaced_by_object_id)
        )
        missing_trace = sum(
            1 for item in unique if not item.physical_identity or not item.source_part or not item.resolution_trace
        )
        visible_without_geometry = sum(
            1 for item in unique if item.generation_content and not item.normalized_bbox and not item.painted_bbox
        )
        scenes.append(
            EffectiveSlideScene(
                slide_id=slide.slide_id,
                slide_index=slide.slide_index,
                layout_id=slide.layout_id,
                master_id=master.master_id if master else None,
                canvas={"width_emu": width, "height_emu": height},
                layers=layers,
                effective_objects=unique,
                invariants=EffectiveSceneInvariants(
                    duplicate_physical_identity_count=duplicates,
                    replacement_chain_invalid_count=replacement_invalid,
                    missing_source_trace_count=missing_trace,
                    visible_without_geometry_count=visible_without_geometry,
                ),
            )
        )
    return scenes


def effective_text_run_store(slides: list[SlideModel]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for slide in slides:
        for shape in flatten_objects(slide.objects):
            if not shape.rich_text:
                continue
            for paragraph_index, paragraph in enumerate(shape.rich_text.paragraphs):
                for run_index, run in enumerate(paragraph.runs):
                    result.append(
                        {
                            "slide_id": slide.slide_id,
                            "object_id": shape.object_id,
                            "source_part": shape.source_part,
                            "paragraph_index": paragraph_index,
                            "run_index": run_index,
                            "text": run.text,
                            "font_family": run.style.font_family,
                            "font_size_pt": run.style.font_size_pt,
                            "color": run.style.color,
                            "bold": run.style.bold,
                            "italic": run.style.italic,
                            "hyperlink": run.style.hyperlink,
                            "explicit_style": run.raw_style.model_dump(mode="json"),
                            "effective_style": run.effective_style.model_dump(mode="json"),
                            "bbox_context": {
                                "absolute_bbox": shape.geometry_full.absolute_bbox.model_dump(mode="json")
                                if shape.geometry_full and shape.geometry_full.absolute_bbox
                                else None,
                                "normalized_bbox": shape.geometry_full.normalized_bbox.model_dump(mode="json")
                                if shape.geometry_full and shape.geometry_full.normalized_bbox
                                else None,
                            },
                        }
                    )
    return result
