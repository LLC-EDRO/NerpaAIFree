"""Ordered OOXML object-tree extraction for masters, layouts and slides."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from lxml import etree

from app.presentation.models import (
    ColorValue,
    GeometryModel,
    LineStyle,
    ShapeElementModel,
    StyleRefs,
    TextCapabilityModel,
    TextFrameProperties,
    TextFrameStyleLayer,
    TextStyle,
    ThemeModel,
)
from app.presentation.parser.colors import resolve_line, resolve_solid_fill
from app.presentation.parser.constants import EMU_PER_POINT
from app.presentation.parser.evidence import EvidenceStore
from app.presentation.parser.geometry import (
    TransformFrame,
    build_geometry,
    identity_frame,
    parse_xfrm,
    push_group_frame,
)
from app.presentation.parser.ids import object_id, physical_identity_key
from app.presentation.parser.relationships import RelationshipGraph
from app.presentation.parser.rich_text import parse_rich_text, raw_text
from app.presentation.parser.xml_utils import bool_attr, first_child, first_descendant, int_attr, local_name, xml_string


@dataclass(slots=True)
class ObjectParseContext:
    scope_id: str
    source_level: str
    source_part: str
    slide_id: str | None
    layout_id: str | None
    master_id: str | None
    width: int
    height: int
    theme: ThemeModel | None
    theme_colors: dict[str, str]
    color_map: dict[str, str]
    relationships: RelationshipGraph
    content_types: dict[str, str]
    evidence: EvidenceStore
    issues: list[dict[str, object]]


@dataclass(slots=True)
class _Counter:
    value: int = 0


def _text_frame(tx_body, context: ObjectParseContext, identifier: str, preset: str | None):
    if tx_body is None:
        return None
    body = first_child(tx_body, "bodyPr")
    styles = first_child(tx_body, "lstStyle")
    aliases = {}
    if context.theme:
        aliases = {"+mj-lt": context.theme.major_font or "", "+mn-lt": context.theme.minor_font or ""}
    return TextFrameProperties(
        shape_geometry=preset,
        font_aliases=aliases,
        style_layers=[TextFrameStyleLayer(
            source_part=context.source_part, source_object_id=identifier,
            body_attributes=dict(body.attrib) if body is not None else {},
            body_children=[xml_string(child) for child in body] if body is not None else [],
            paragraph_styles={local_name(child): xml_string(child) for child in styles} if styles is not None else {},
        )],
    )


def flatten_objects(objects: list[ShapeElementModel]) -> list[ShapeElementModel]:
    result: list[ShapeElementModel] = []
    seen: set[str] = set()

    def visit(items: list[ShapeElementModel]) -> None:
        for item in items:
            if item.physical_identity in seen:
                continue
            seen.add(item.physical_identity)
            result.append(item)
            visit(item.children)

    visit(objects)
    return result


def _c_nv_pr(node: etree._Element) -> etree._Element | None:
    return first_descendant(node, "cNvPr")


def _sp_pr(node: etree._Element, kind: str) -> etree._Element | None:
    target = "grpSpPr" if kind == "group" else "spPr"
    direct = next((child for child in node if local_name(child) == target), None)
    return direct if direct is not None else first_descendant(node, target)


def _placeholder(node: etree._Element) -> tuple[str | None, int | None]:
    # A group's child placeholder is not a placeholder on the group itself.
    nv = next((child for child in node if local_name(child) in {
        "nvSpPr", "nvPicPr", "nvGraphicFramePr", "nvCxnSpPr", "nvGrpSpPr",
    }), None)
    ph = first_descendant(nv, "ph") if nv is not None else None
    # CT_Placeholder defaults: type=obj, idx=0. Preserve the actual declaration
    # independently from the element kind (an inserted pic is still a ph).
    return (ph.get("type", "obj"), int_attr(ph, "idx", 0)) if ph is not None else (None, None)


def _relationship_id(node: etree._Element | None, attribute_names: tuple[str, ...]) -> str | None:
    if node is None:
        return None
    for key, value in node.attrib.items():
        if etree.QName(key).localname in attribute_names:
            return value
    return None


def _hyperlink(node: etree._Element, relationships: RelationshipGraph) -> str | None:
    c_nv = _c_nv_pr(node)
    link = first_descendant(c_nv, "hlinkClick")
    rel_id = _relationship_id(link, ("id",))
    edge = relationships.by_id.get(rel_id or "")
    return edge.target if edge and edge.target else rel_id


def _style_refs(node: etree._Element) -> StyleRefs:
    direct = next((child for child in node if local_name(child) == "style"), None)
    style = direct if direct is not None else first_descendant(node, "style")
    fill_ref, line_ref = first_child(style, "fillRef"), first_child(style, "lnRef")
    effect_ref, font_ref = first_child(style, "effectRef"), first_child(style, "fontRef")
    font_index = font_ref.get("idx") if font_ref is not None else None
    return StyleRefs(
        fill_ref_idx=int_attr(fill_ref, "idx"),
        ln_ref_idx=int_attr(line_ref, "idx"),
        effect_ref_idx=int_attr(effect_ref, "idx"),
        font_ref=font_index if font_index in {"major", "minor"} else None,
    )


def _image_crop(node: etree._Element) -> dict[str, float | None] | None:
    source_rect = first_descendant(node, "srcRect")
    if source_rect is None:
        return None

    def percent(name: str) -> float | None:
        value = int_attr(source_rect, name)
        return value / 100_000 if value is not None else None

    return {"left": percent("l"), "top": percent("t"), "right": percent("r"), "bottom": percent("b")}


def _object_kind(node: etree._Element) -> str:
    tag = local_name(node)
    if tag == "pic":
        return "picture"
    if tag == "cxnSp":
        return "connector"
    if tag == "grpSp":
        return "group"
    if tag == "graphicFrame":
        raw = xml_string(node)
        if first_descendant(node, "tbl") is not None:
            return "table"
        if first_descendant(node, "chart") is not None:
            return "chart"
        if "oleObj" in raw or "oleObject" in raw:
            return "embedded_object"
        if first_descendant(node, "relIds") is not None or "diagram" in raw or "smartArt" in raw:
            return "smartart"
        return "graphicFrame"
    if tag == "sp":
        preset = first_descendant(node, "prstGeom")
        value = preset.get("prst") if preset is not None else None
        if value and (value == "line" or "connector" in value.lower()):
            return "line"
        return "shape"
    return "shape"


def _role_hint(placeholder_type: str | None, name: str | None) -> str | None:
    if placeholder_type:
        return placeholder_type.lower()
    lowered = (name or "").lower()
    if "title" in lowered:
        return "title"
    if "footer" in lowered:
        return "footer"
    return None


def _fallback_font(placeholder_type: str | None, style_refs: StyleRefs, theme: ThemeModel | None) -> str | None:
    if theme is None:
        return None
    if style_refs.font_ref == "major" or (placeholder_type and "title" in placeholder_type.lower()):
        return theme.major_font
    return theme.minor_font


def _shape_identity(child: ShapeElementModel, parent: ShapeElementModel) -> None:
    child.parent_object_id = parent.object_id
    child.parent_group_path = (
        f"{parent.parent_group_path}/{parent.object_id}" if parent.parent_group_path else parent.object_id
    )
    child.physical_identity = physical_identity_key(
        child.source_level, child.source_part, child.object_id, child.parent_group_path
    )
    for nested in child.children:
        _shape_identity(nested, child)


def _record_evidence(context: ObjectParseContext, shape: ShapeElementModel) -> None:
    role = _role_hint(shape.placeholder_type, shape.name)
    area = shape.geometry_full.painted_area_ratio if shape.geometry_full else None
    if shape.fill and shape.fill.color:
        context.evidence.add(
            property="shape.fill",
            value=shape.fill.color,
            scope=context.source_level,
            slide_id=context.slide_id,
            layout_id=context.layout_id,
            master_id=context.master_id,
            object_id=shape.object_id,
            source_part=context.source_part,
            role_hint=role,
            source=f"scheme:{shape.fill.theme_color_ref}" if shape.fill.theme_color_ref else "explicit_srgb",
            inherited=False,
            area=area,
            characters=None,
            z_index=shape.z_index,
        )
    if shape.line and shape.line.color:
        context.evidence.add(
            property="shape.line",
            value=shape.line.color,
            scope=context.source_level,
            slide_id=context.slide_id,
            layout_id=context.layout_id,
            master_id=context.master_id,
            object_id=shape.object_id,
            source_part=context.source_part,
            role_hint=role,
            source=f"scheme:{shape.line.theme_color_ref}" if shape.line.theme_color_ref else "explicit_srgb",
            inherited=False,
            area=area,
            characters=None,
            z_index=shape.z_index,
        )
    if shape.rich_text:
        for paragraph in shape.rich_text.paragraphs:
            for run in paragraph.runs:
                if run.text and run.style.color:
                    context.evidence.add(
                        property="text.color",
                        value=run.style.color,
                        scope=context.source_level,
                        slide_id=context.slide_id,
                        layout_id=context.layout_id,
                        master_id=context.master_id,
                        object_id=shape.object_id,
                        source_part=context.source_part,
                        role_hint=role,
                        source=f"scheme:{run.style.theme_color_ref}" if run.style.theme_color_ref else "explicit_srgb",
                        inherited=bool(run.provenance.get("color") and run.provenance["color"].inherited),
                        area=None,
                        characters=len(run.text),
                        z_index=shape.z_index,
                    )


def _build_object(
    node: etree._Element,
    context: ObjectParseContext,
    parent_frame: TransformFrame,
    chain: list[TransformFrame],
    counter: _Counter,
) -> ShapeElementModel:
    counter.value += 1
    z_index = counter.value
    kind = _object_kind(node)
    c_nv_pr = _c_nv_pr(node)
    shape_id = int_attr(c_nv_pr, "id")
    name = c_nv_pr.get("name") if c_nv_pr is not None else None
    alt_text = (c_nv_pr.get("descr") or c_nv_pr.get("title")) if c_nv_pr is not None else None
    placeholder_type, placeholder_idx = _placeholder(node)
    identifier = object_id(context.scope_id, shape_id) if shape_id is not None else f"{context.scope_id}_obj_{z_index}"
    sp_pr = _sp_pr(node, kind)
    xfrm = parse_xfrm(first_descendant(sp_pr if sp_pr is not None else node, "xfrm"))
    source_frame_bbox = None
    table_extent_used = False
    if kind == "table" and xfrm is not None:
        source_frame_bbox = GeometryModel(x_emu=xfrm.off_x, y_emu=xfrm.off_y,
                                         width_emu=xfrm.ext_cx, height_emu=xfrm.ext_cy)
        table = first_descendant(node, "tbl")
        grid = first_child(table, "tblGrid")
        widths = [int_attr(child, "w") for child in (grid if grid is not None else [])
                  if local_name(child) == "gridCol"]
        heights = [int_attr(child, "h") for child in (table if table is not None else [])
                   if local_name(child) == "tr"]
        # Exporters can leave stale graphicFrame extents. DrawingML's table
        # grid supplies its intrinsic size, in the same local coordinate space.
        grid_width = sum(widths) if widths and all(w is not None and w > 0 for w in widths) else xfrm.ext_cx
        grid_height = sum(heights) if heights and all(h is not None and h > 0 for h in heights) else xfrm.ext_cy
        table_extent_used = grid_width != xfrm.ext_cx or grid_height != xfrm.ext_cy
        xfrm = replace(xfrm, ext_cx=grid_width, ext_cy=grid_height)
    preset_node = first_descendant(sp_pr, "prstGeom")
    # Compatibility with the existing DOM analyzer: when a group has no own
    # preset geometry it observes the first descendant preset.
    if preset_node is None:
        preset_node = first_descendant(node, "prstGeom")
    preset = preset_node.get("prst") if preset_node is not None else None
    fill = resolve_solid_fill(sp_pr, context.theme_colors, context.color_map)
    line_color, width_pt = resolve_line(sp_pr, context.theme_colors, context.color_map)
    stroke_width = round(width_pt * EMU_PER_POINT) if width_pt is not None else 0
    geometry_full = build_geometry(
        xfrm,
        parent_frame,
        z_index,
        context.width,
        context.height,
        chain,
        kind=kind,
        preset=preset,
        stroke_width_emu=stroke_width,
    )
    geometry_full.source_frame_bbox = source_frame_bbox
    if table_extent_used:
        geometry_full.extent_source = "table_grid"
    geometry = geometry_full.absolute_bbox
    refs = _style_refs(node)
    skip_text = kind in {
        "picture",
        "connector",
        "line",
        "group",
        "graphicFrame",
        "embedded_object",
        "table",
        "chart",
        "smartart",
    }
    tx_body = next((child for child in node if local_name(child) == "txBody"), None) if not skip_text else None
    fallback_font = _fallback_font(placeholder_type, refs, context.theme)
    rich = (
        parse_rich_text(
            tx_body,
            theme=context.theme,
            theme_colors=context.theme_colors,
            color_map=context.color_map,
            relationships=context.relationships,
            fallback_font=fallback_font,
        )
        if tx_body is not None
        else None
    )
    text_value = raw_text(rich)

    relationship_id: str | None = None
    media_id: str | None = None
    embedding = None
    preview = None
    if kind == "picture":
        relationship_id = _relationship_id(first_descendant(node, "blip"), ("embed", "link"))
        edge = context.relationships.by_id.get(relationship_id or "")
        media_id = edge.target if edge else relationship_id
    elif kind == "chart":
        relationship_id = _relationship_id(first_descendant(node, "chart"), ("id",))
        edge = context.relationships.by_id.get(relationship_id or "")
        media_id = edge.target if edge else None
    elif kind == "smartart":
        rel_ids = first_descendant(node, "relIds")
        relationship_id = _relationship_id(rel_ids, ("dm", "lo", "id"))
        edge = context.relationships.by_id.get(relationship_id or "")
        media_id = edge.target if edge else None
    elif kind == "embedded_object":
        from app.presentation.models import EmbeddingModel, PreviewModel

        ole = first_descendant(node, {"oleObj", "oleObject"})
        relationship_id = _relationship_id(ole, ("id",))
        edge = context.relationships.by_id.get(relationship_id or "")
        prog_id = ole.get("progId") if ole is not None else None
        content_type = context.content_types.get(edge.target or "") if edge else None
        excel = bool(
            (prog_id and "excel" in prog_id.lower()) or (content_type and "spreadsheetml" in content_type.lower())
        )
        embedding = EmbeddingModel(
            kind="embedded_excel" if excel else "ole" if prog_id or edge else "unknown",
            part=edge.target if edge else None,
            content_type=content_type,
            prog_id=prog_id,
        )
        preview_id = _relationship_id(first_descendant(node, "blip"), ("embed", "link"))
        preview_edge = context.relationships.by_id.get(preview_id or "")
        preview = PreviewModel(
            relationship_id=preview_id,
            media_part=preview_edge.target if preview_edge else None,
            media_id=preview_edge.target if preview_edge else None,
        )
        media_id = preview.media_id

    explicit_run = (
        next((run for paragraph in rich.paragraphs for run in paragraph.runs if run.text), None) if rich else None
    )
    text_style = None
    text_capability = None
    if text_value and rich:
        dominant = rich.dominant
        text_style = TextStyle(
            font_family=dominant.font_family,
            font_size_pt=dominant.font_size_pt,
            font_weight=dominant.font_weight,
            bold=dominant.bold,
            italic=dominant.italic,
            underline=dominant.underline,
            language=dominant.language,
            baseline=dominant.baseline,
            character_spacing=dominant.character_spacing,
            color=dominant.color,
            theme_color_ref=dominant.theme_color_ref,
            hyperlink=dominant.hyperlink,
            source="theme" if dominant.theme_color_ref else "resolved_usage",
        )
    elif rich:
        text_capability = TextCapabilityModel(default_style=rich.dominant)

    raw_xml = xml_string(node)
    parser_support = (
        "opaque"
        if kind in {"graphicFrame", "embedded_object", "smartart"}
        else "partial"
        if kind in {"table", "chart"}
        else "full"
    )
    shape = ShapeElementModel(
        object_id=identifier,
        shape_id=shape_id,
        placeholder_idx=placeholder_idx,
        placeholder_type=placeholder_type,
        type=kind if kind != "shape" else (placeholder_type or "shape"),
        name=name,
        source_level=context.source_level,
        source_part=context.source_part,
        source_object_id=identifier,
        physical_identity=physical_identity_key(context.source_level, context.source_part, identifier, None),
        object_kind=kind,
        parser_support=parser_support,
        z_index=z_index,
        media_id=media_id,
        relationship_id=relationship_id,
        hyperlink=_hyperlink(node, context.relationships),
        hidden=bool_attr(c_nv_pr, "hidden"),
        has_text_content=bool(text_value),
        rich_text_error="supported_text_container_not_parsed" if text_value and rich is None else None,
        text_style=text_style,
        text_capability=text_capability,
        text_frame=_text_frame(tx_body, context, identifier,
            "custom" if first_child(sp_pr, "custGeom") is not None else preset),
        fill=ColorValue(color=fill.value, theme_color_ref=fill.theme_color_ref)
        if fill.value or fill.theme_color_ref
        else None,
        line=LineStyle(color=line_color.value, theme_color_ref=line_color.theme_color_ref, width_pt=width_pt)
        if line_color.value or line_color.theme_color_ref
        else None,
        geometry=geometry,
        geometry_full=geometry_full,
        rich_text=rich,
        explicit_style={
            "font_size": explicit_run.raw_style.font_size_pt if explicit_run else None,
            "color": explicit_run.raw_style.color if explicit_run else None,
            "fill": fill.value if fill.source == "srgb" else None,
            "line": line_color.value if line_color.source == "srgb" else None,
        },
        effective_style={
            "font_family": rich.dominant.font_family if rich and text_value else None,
            "font_size": rich.dominant.font_size_pt if rich and text_value else None,
            "color": rich.dominant.color if rich and text_value else None,
            "fill": fill.value,
            "line": line_color.value,
        },
        provenance={
            "font_family": explicit_run.provenance.get("font_family").model_dump(mode="json")
            if explicit_run and explicit_run.provenance.get("font_family")
            else None,
            "font_size": explicit_run.provenance.get("font_size_pt").model_dump(mode="json")
            if explicit_run and explicit_run.provenance.get("font_size_pt")
            else None,
            "color": explicit_run.provenance.get("color").model_dump(mode="json")
            if explicit_run and explicit_run.provenance.get("color")
            else None,
            "fill": {
                "value": fill.value,
                "source": context.source_level,
                "source_part": context.source_part,
                "source_object_id": identifier,
                "inherited": False,
                "raw": fill.raw,
            }
            if fill.value
            else None,
            "line": {
                "value": line_color.value,
                "source": context.source_level,
                "source_part": context.source_part,
                "source_object_id": identifier,
                "inherited": False,
                "raw": line_color.raw,
            }
            if line_color.value
            else None,
        },
        style_refs=refs,
        raw_xml_ref={"sha256": hashlib.sha256(raw_xml.encode()).hexdigest(), "length": len(raw_xml)},
        embedding=embedding,
        preview=preview,
        image_crop=_image_crop(node) if kind == "picture" else None,
        alt_text=alt_text,
    )
    _record_evidence(context, shape)
    return shape


def _walk(
    nodes: list[etree._Element],
    context: ObjectParseContext,
    parent_frame: TransformFrame,
    chain: list[TransformFrame],
    counter: _Counter,
) -> list[ShapeElementModel]:
    result: list[ShapeElementModel] = []
    accepted = {"sp", "pic", "cxnSp", "grpSp", "graphicFrame"}
    for node in nodes:
        if local_name(node) not in accepted:
            continue
        shape = _build_object(node, context, parent_frame, chain, counter)
        if shape.object_kind == "group":
            group_transform = parse_xfrm(first_descendant(_sp_pr(node, "group"), "xfrm"))
            frame = push_group_frame(parent_frame, group_transform) if group_transform else parent_frame
            child_nodes = [child for child in node if local_name(child) in accepted]
            shape.children = _walk(child_nodes, context, frame, [*chain, frame], counter)
            for child in shape.children:
                _shape_identity(child, shape)
        result.append(shape)
    return result


def extract_objects(root: etree._Element, context: ObjectParseContext) -> list[ShapeElementModel]:
    sp_tree = first_descendant(root, "spTree")
    if sp_tree is None:
        return []
    return _walk(list(sp_tree), context, identity_frame(context.width, context.height), [], _Counter())
