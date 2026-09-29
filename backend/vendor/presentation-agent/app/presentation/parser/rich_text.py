"""Ordered DrawingML rich-text extraction with explicit/effective style facts."""

from __future__ import annotations

from copy import deepcopy

from lxml import etree

from app.presentation.models import (
    BulletModel,
    ParagraphProperties,
    PropertyProvenance,
    RichTextModel,
    TextParagraphModel,
    TextRunModel,
    TextStyle,
    ThemeModel,
)
from app.presentation.parser.colors import resolve_color, resolve_typeface
from app.presentation.parser.relationships import RelationshipGraph
from app.presentation.parser.xml_utils import first_child, first_descendant, int_attr, local_name, xml_string


def _size_pt(node: etree._Element | None) -> float | None:
    value = int_attr(node, "sz")
    return value / 100 if value is not None else None


def _spacing_value(container: etree._Element | None, name: str) -> float | None:
    node = first_child(container, name)
    if node is None:
        return None
    percent = first_child(node, "spcPct")
    points = first_child(node, "spcPts")
    if percent is not None:
        value = int_attr(percent, "val")
        return value / 1000 if value is not None else None
    if points is not None:
        value = int_attr(points, "val")
        return value / 100 if value is not None else None
    return None


def _spacing_unit(container: etree._Element | None, name: str) -> str | None:
    node = first_child(container, name)
    if first_child(node, "spcPct") is not None:
        return "percent"
    if first_child(node, "spcPts") is not None:
        return "points"
    return None


def _hyperlink(node: etree._Element | None, relationships: RelationshipGraph | None) -> str | None:
    link = first_child(node, "hlinkClick")
    if link is None:
        return None
    rel_id = next((value for key, value in link.attrib.items() if etree.QName(key).localname == "id"), None)
    if not rel_id:
        return None
    edge = relationships.by_id.get(rel_id) if relationships else None
    return edge.target if edge and edge.target else rel_id


def parse_text_style(
    node: etree._Element | None,
    theme: ThemeModel | None,
    theme_colors: dict[str, str],
    color_map: dict[str, str],
    relationships: RelationshipGraph | None,
    *,
    resolve_font: bool,
) -> tuple[TextStyle, dict[str, PropertyProvenance | None]]:
    latin = first_child(node, "latin")
    raw_font = latin.get("typeface") if latin is not None else None
    color = resolve_color(
        first_descendant(node, {"solidFill", "srgbClr", "schemeClr", "sysClr", "prstClr", "scrgbClr"}),
        theme_colors,
        color_map,
    )
    size = _size_pt(node)
    bold_raw = node.get("b") if node is not None else None
    italic_raw = node.get("i") if node is not None else None
    baseline = int_attr(node, "baseline")
    spacing = int_attr(node, "spc")
    raw_style = TextStyle(
        font_family=resolve_typeface(raw_font, theme) if resolve_font else raw_font,
        font_size_pt=size,
        font_weight=700 if bold_raw in {"1", "true", "on"} else 400 if bold_raw in {"0", "false", "off"} else None,
        bold=(bold_raw in {"1", "true", "on"}) if bold_raw is not None else None,
        italic=(italic_raw in {"1", "true", "on"}) if italic_raw is not None else None,
        underline=node.get("u") if node is not None else None,
        language=node.get("lang") if node is not None else None,
        baseline=baseline / 1000 if baseline is not None else None,
        character_spacing=spacing / 100 if spacing is not None else None,
        color=color.value,
        theme_color_ref=color.theme_color_ref,
        hyperlink=_hyperlink(node, relationships),
    )
    provenance = {
        "font_family": PropertyProvenance(
            value=raw_style.font_family,
            raw_value=raw_font,
            source="run",
            inherited=False,
            raw={"raw_typeface": raw_font},
        )
        if raw_font
        else None,
        "font_size_pt": PropertyProvenance(
            value=size, raw_value=node.get("sz") if node is not None else None, source="run", inherited=False
        )
        if size is not None
        else None,
        "color": PropertyProvenance(
            value=color.value, raw_value=color.raw, source="run", inherited=False, trace=color.trace, raw=color.raw
        )
        if color.value or color.theme_color_ref
        else None,
    }
    return raw_style, provenance


def _merge_style(*styles: TextStyle) -> TextStyle:
    data: dict[str, object] = {}
    for field_name in TextStyle.model_fields:
        if field_name == "source":
            continue
        data[field_name] = next(
            (getattr(style, field_name) for style in reversed(styles) if getattr(style, field_name) is not None), None
        )
    return TextStyle(**data)


def _bullet(p_pr: etree._Element | None) -> BulletModel | None:
    if p_pr is None:
        return None
    if first_child(p_pr, "buNone") is not None:
        return BulletModel(type="none")
    char = first_child(p_pr, "buChar")
    auto = first_child(p_pr, "buAutoNum")
    blip = first_child(p_pr, "buBlip")
    font = first_child(p_pr, "buFont")
    if char is not None:
        return BulletModel(type="char", char=char.get("char"), font=font.get("typeface") if font is not None else None)
    if auto is not None:
        return BulletModel(
            type="autoNum", auto_num_type=auto.get("type"), font=font.get("typeface") if font is not None else None
        )
    if blip is not None:
        return BulletModel(type="blip", font=font.get("typeface") if font is not None else None)
    return None


def _text(node: etree._Element) -> str:
    text_node = first_child(node, "t")
    if text_node is None:
        text_node = first_descendant(node, "t")
    # fast-xml-parser in the source implementation trims scalar text values.
    # Retain that behavior for regression parity, even when adjacent OOXML runs
    # carried their own leading/trailing spaces.
    return "" if text_node is None else (text_node.text or "").strip()


def parse_rich_text(
    tx_body: etree._Element | None,
    *,
    theme: ThemeModel | None,
    theme_colors: dict[str, str],
    color_map: dict[str, str],
    relationships: RelationshipGraph | None,
    fallback_font: str | None,
) -> RichTextModel | None:
    if tx_body is None:
        return None
    parsed_paragraphs: list[TextParagraphModel] = []
    dominant_candidates: list[TextStyle] = []
    for paragraph in [child for child in tx_body if local_name(child) == "p"]:
        p_pr = first_child(paragraph, "pPr")
        default_rpr = first_child(p_pr, "defRPr")
        end_rpr = first_child(paragraph, "endParaRPr")
        default_style, _ = parse_text_style(
            default_rpr, theme, theme_colors, color_map, relationships, resolve_font=True
        )
        end_style, _ = parse_text_style(end_rpr, theme, theme_colors, color_map, relationships, resolve_font=True)
        base = _merge_style(TextStyle(font_family=fallback_font), end_style, default_style)
        runs: list[TextRunModel] = []
        for inline in paragraph:
            kind = local_name(inline)
            if kind not in {"r", "fld", "br"}:
                continue
            r_pr = first_child(inline, "rPr")
            raw_style, provenance = parse_text_style(
                r_pr, theme, theme_colors, color_map, relationships, resolve_font=False
            )
            resolved_raw = raw_style.model_copy(update={"font_family": resolve_typeface(raw_style.font_family, theme)})
            effective = _merge_style(base, resolved_raw)
            run_text = "\n" if kind == "br" else _text(inline)
            for key, value in (
                ("font_family", effective.font_family),
                ("font_size_pt", effective.font_size_pt),
                ("color", effective.color),
            ):
                if provenance.get(key) is None and value is not None:
                    provenance[key] = PropertyProvenance(value=value, source="paragraph", inherited=True)
            run = TextRunModel(
                text=run_text, raw_style=raw_style, effective_style=effective, style=effective,
                provenance=provenance, is_line_break=kind == "br", is_field=kind == "fld",
                raw_properties_xml=xml_string(r_pr) if r_pr is not None else "",
            )
            runs.append(run)
            if run_text:
                dominant_candidates.append(effective)
        properties = ParagraphProperties(
            alignment=p_pr.get("algn") if p_pr is not None else None,
            level=int_attr(p_pr, "lvl", 0) or 0,
            margin_left_emu=int_attr(p_pr, "marL"),
            margin_right_emu=int_attr(p_pr, "marR"),
            indent_emu=int_attr(p_pr, "indent"),
            line_spacing=_spacing_value(p_pr, "lnSpc"),
            space_before=_spacing_value(p_pr, "spcBef"),
            space_after=_spacing_value(p_pr, "spcAft"),
            line_spacing_unit=_spacing_unit(p_pr, "lnSpc"),
            space_before_unit=_spacing_unit(p_pr, "spcBef"),
            space_after_unit=_spacing_unit(p_pr, "spcAft"),
            raw_xml=xml_string(p_pr) if p_pr is not None else "",
        )
        parsed_paragraphs.append(
            TextParagraphModel(
                alignment=properties.alignment,
                level=properties.level,
                properties=properties,
                bullet=_bullet(p_pr),
                runs=runs,
                explicit_default_font_size_pt=_size_pt(default_rpr),
                explicit_end_para_font_size_pt=_size_pt(end_rpr),
                default_style=base,
                end_style=end_style,
                end_properties_xml=xml_string(end_rpr) if end_rpr is not None else "",
            )
        )
        if not runs:
            dominant_candidates.append(base)
    dominant = deepcopy(dominant_candidates[0] if dominant_candidates else TextStyle(font_family=fallback_font))
    return RichTextModel(paragraphs=parsed_paragraphs, dominant=dominant)


def raw_text(rich: RichTextModel | None) -> str | None:
    if rich is None:
        return None
    paragraphs = ["".join(run.text for run in paragraph.runs) for paragraph in rich.paragraphs]
    value = "\n".join(paragraphs).strip()
    return value or None
