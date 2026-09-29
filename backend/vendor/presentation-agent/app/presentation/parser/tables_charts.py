"""Native DrawingML table and chart extraction."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from app.presentation.models import (
    ChartDataSource,
    ChartModel,
    ChartSeriesModel,
    ShapeElementModel,
    TableBorder,
    TableCellModel,
    TableCellStyle,
    TableModel,
    TableProperties,
    TableRowModel,
    TextStyle,
    ThemeModel,
)
from app.presentation.parser.colors import resolve_color, resolve_solid_fill, resolve_typeface
from app.presentation.parser.relationships import RelationshipGraph
from app.presentation.parser.rich_text import parse_rich_text, raw_text
from app.presentation.parser.xml_utils import bool_attr, first_child, first_descendant, int_attr, local_name


def _raw_attributes(node: etree._Element | None) -> dict[str, Any]:
    if node is None:
        return {}
    result: dict[str, Any] = {}
    for name, raw in node.attrib.items():
        key = etree.QName(name).localname
        if raw.lstrip("-").isdigit():
            result[key] = int(raw)
        elif raw.lower() in {"true", "false", "on", "off"}:
            result[key] = raw.lower() in {"true", "on"}
        elif raw in {"0", "1"}:
            result[key] = raw == "1"
        else:
            result[key] = raw
    return result


def _text_style(
    node: etree._Element | None,
    theme: ThemeModel | None,
    theme_colors: dict[str, str],
    color_map: dict[str, str],
) -> TableCellStyle:
    run = (
        node
        if node is not None and local_name(node) in {"rPr", "defRPr", "endParaRPr", "tcTxStyle"}
        else first_descendant(node, {"rPr", "defRPr", "endParaRPr", "tcTxStyle"})
    )
    latin = first_descendant(run, "latin")
    raw_font = latin.get("typeface") if latin is not None else None
    font_ref = first_descendant(run, "fontRef")
    if not raw_font and font_ref is not None:
        raw_font = "+mj-lt" if font_ref.get("idx") == "major" else "+mn-lt"
    size = int_attr(run, "sz")
    bold = run.get("b") if run is not None else None
    direct_color = next((child for child in (run if run is not None else [])
                         if local_name(child) in {"srgbClr", "schemeClr", "sysClr", "scrgbClr", "prstClr"}), None)
    color = resolve_color(direct_color if direct_color is not None else first_child(run, "solidFill"), theme_colors, color_map)
    return TableCellStyle(
        fill=None,
        italic=bool_attr(run, "i") if run is not None and run.get("i") is not None else None,
        text_color=color.value,
        font_family=resolve_typeface(raw_font, theme),
        font_size_pt=size / 100 if size is not None else None,
        font_weight=700 if bold in {"1", "true", "on"} else 400 if bold in {"0", "false", "off"} else None,
    )


def _merge_cell_style(base: TableCellStyle, overlay: TableCellStyle) -> TableCellStyle:
    result = TableCellStyle(
        **{
            name: getattr(overlay, name) if getattr(overlay, name) is not None else getattr(base, name)
            for name in TableCellStyle.model_fields
        }
    )
    if overlay.fill_type is not None:
        result.fill = overlay.fill
        result.fill_alpha = overlay.fill_alpha
    return result


@dataclass(slots=True)
class TableStyleDefinition:
    style_id: str
    style_name: str | None
    regions: dict[str, TableCellStyle] = field(default_factory=dict)
    borders: dict[str, dict[str, TableBorder]] = field(default_factory=dict)


def parse_table_styles(
    root: etree._Element | None,
    theme: ThemeModel | None,
    theme_colors: dict[str, str],
    color_map: dict[str, str],
) -> dict[str, TableStyleDefinition]:
    catalog: dict[str, TableStyleDefinition] = {}
    if root is None:
        return catalog
    for style in [item for item in root if local_name(item) == "tblStyle"]:
        style_id = style.get("styleId") or ""
        if not style_id:
            continue
        definition = TableStyleDefinition(style_id=style_id, style_name=style.get("styleName"))
        for region in style:
            region_name = local_name(region)
            text = _text_style(first_child(region, "tcTxStyle"), theme, theme_colors, color_map)
            cell_style = first_child(region, "tcStyle")
            fill_node = first_child(cell_style, "fill")
            definition.regions[region_name] = text.model_copy(update=_fill_style(fill_node, theme_colors, color_map))
            border_node = first_child(cell_style, "tcBdr")
            definition.borders[region_name] = {
                local_name(side): _line_border(first_child(side, "ln"), theme_colors, color_map)
                for side in (border_node if border_node is not None else [])
                if first_child(side, "ln") is not None
            }
        catalog[style_id] = definition
    return catalog


def _regions(properties: TableProperties, row: int, col: int, row_count: int, col_count: int) -> list[str]:
    result = ["wholeTbl"]
    if properties.banded_rows and not (properties.first_row and row == 0 or properties.last_row and row == row_count - 1):
        result.append("band1H" if (row - int(properties.first_row)) % 2 == 0 else "band2H")
    if properties.banded_columns and not (properties.first_column and col == 0 or properties.last_column and col == col_count - 1):
        result.append("band1V" if (col - int(properties.first_column)) % 2 == 0 else "band2V")
    if properties.first_row and row == 0:
        result.append("firstRow")
    if properties.last_row and row == row_count - 1:
        result.append("lastRow")
    if properties.first_column and col == 0:
        result.append("firstCol")
    if properties.last_column and col == col_count - 1:
        result.append("lastCol")
    if properties.first_row and properties.first_column and row == 0 and col == 0:
        result.append("nwCell")
    if properties.first_row and properties.last_column and row == 0 and col == col_count - 1:
        result.append("neCell")
    if properties.last_row and properties.first_column and row == row_count - 1 and col == 0:
        result.append("swCell")
    if properties.last_row and properties.last_column and row == row_count - 1 and col == col_count - 1:
        result.append("seCell")
    return result


def _cell_style(
    cell: etree._Element,
    theme: ThemeModel | None,
    theme_colors: dict[str, str],
    color_map: dict[str, str],
) -> TableCellStyle:
    tc_pr = first_child(cell, "tcPr")
    text = TableCellStyle()
    body = first_child(cell, "txBody")
    paragraph = first_child(body, "p")
    for node in (first_child(first_child(paragraph, "pPr"), "defRPr"),
                 first_child(first_child(paragraph, "r"), "rPr")):
        text = _merge_cell_style(text, _text_style(node, theme, theme_colors, color_map))
    return text.model_copy(update=_fill_style(tc_pr, theme_colors, color_map))


def _border(
    cell: etree._Element, side: str, theme_colors: dict[str, str], color_map: dict[str, str]
) -> TableBorder | None:
    tc_pr = first_child(cell, "tcPr")
    line = first_child(tc_pr, side)
    if line is None:
        return None
    return _line_border(line, theme_colors, color_map)


def _line_border(line, theme_colors, color_map) -> TableBorder:
    color = resolve_color(first_child(line, "solidFill"), theme_colors, color_map)
    width = int_attr(line, "w")
    dash = first_child(line, "prstDash")
    return TableBorder(color=color.value, width_pt=width / 12_700 if width is not None else None,
                       visible=first_child(line, "noFill") is None,
                       dash=dash.get("val") if dash is not None else None, alpha=color.alpha)


def _fill_style(node, theme_colors, color_map) -> dict[str, Any]:
    # Only a direct fill belongs to this cell; descendant fills may belong to borders.
    fill = next((child for child in (node if node is not None else [])
                 if local_name(child) in {"solidFill", "noFill", "gradFill", "pattFill", "blipFill"}), None)
    if fill is None:
        return {}
    color = resolve_color(fill, theme_colors, color_map) if local_name(fill) == "solidFill" else None
    return {"fill": color.value if color else None,
            "fill_alpha": color.alpha if color else 0.0 if local_name(fill) == "noFill" else None,
            "fill_type": local_name(fill)}


def _cell_paragraphs(cell: etree._Element) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    tx_body = first_child(cell, "txBody")
    for paragraph in [child for child in (list(tx_body) if tx_body is not None else []) if local_name(child) == "p"]:
        runs: list[dict[str, str]] = []
        for inline in paragraph:
            if local_name(inline) not in {"r", "fld"}:
                continue
            text = first_descendant(inline, "t")
            runs.append({"text": text.text or "" if text is not None else ""})
        result.append({"runs": runs})
    return result


def _relationship_ids(node: etree._Element) -> list[str]:
    result: list[str] = []
    for descendant in node.iter():
        for key, value in descendant.attrib.items():
            if etree.QName(key).localname == "id" and value.startswith("rId") and value not in result:
                result.append(value)
    return result


def _dominant(cells: list[TableCellModel]) -> TableCellStyle | None:
    if not cells:
        return None
    payload: dict[str, Any] = {}
    for field_name in TableCellStyle.model_fields:
        values = [
            getattr(cell.effective_style, field_name)
            for cell in cells
            if getattr(cell.effective_style, field_name) is not None
        ]
        payload[field_name] = Counter(values).most_common(1)[0][0] if values else None
    return TableCellStyle(**payload)


def parse_tables(
    root: etree._Element,
    *,
    scope_id: str,
    source_level: str,
    source_part: str,
    theme: ThemeModel | None,
    theme_colors: dict[str, str],
    color_map: dict[str, str],
    catalog: dict[str, TableStyleDefinition],
    relationships: RelationshipGraph,
    default_style_id: str | None = None,
) -> list[TableModel]:
    result: list[TableModel] = []
    for table_index, table in enumerate([item for item in root.iter() if local_name(item) == "tbl"], 1):
        tbl_pr = first_child(table, "tblPr")
        style_node = first_child(tbl_pr, "tableStyleId")
        if style_node is None:  # Compatibility with older nonstandard exports.
            style_node = first_child(table, "tableStyleId")
        style_id = (style_node.text or "").strip() if style_node is not None else default_style_id
        definition = catalog.get(style_id or "")
        properties = TableProperties(
            first_row=bool_attr(tbl_pr, "firstRow"),
            first_column=bool_attr(tbl_pr, "firstCol"),
            last_row=bool_attr(tbl_pr, "lastRow"),
            last_column=bool_attr(tbl_pr, "lastCol"),
            banded_rows=bool_attr(tbl_pr, "bandRow"),
            banded_columns=bool_attr(tbl_pr, "bandCol"),
        )
        grid = first_child(table, "tblGrid")
        widths = [
            int_attr(column, "w")
            for column in (list(grid) if grid is not None else [])
            if local_name(column) == "gridCol"
        ]
        row_nodes = [child for child in table if local_name(child) == "tr"]
        physical_width = max(
            (sum(int_attr(cell, "gridSpan", 1) or 1 for cell in row if local_name(cell) == "tc") for row in row_nodes),
            default=0,
        )
        column_count = len(widths) or physical_width
        rows: list[TableRowModel] = []
        for row_index, row in enumerate(row_nodes):
            cell_nodes = [child for child in row if local_name(child) == "tc"]
            cells: list[TableCellModel] = []
            col = 0
            for cell_index, cell in enumerate(cell_nodes):
                col_span = int_attr(cell, "gridSpan", 1) or 1
                row_span = int_attr(cell, "rowSpan", 1) or 1
                h_merge, v_merge = bool_attr(cell, "hMerge"), bool_attr(cell, "vMerge")
                explicit = _cell_style(cell, theme, theme_colors, color_map)
                effective = TableCellStyle()
                regions = _regions(properties, row_index, col, len(row_nodes), column_count)
                provenance: dict[str, Any] = {}
                borders: dict[str, TableBorder | None] = {}
                if definition:
                    for region in regions:
                        region_borders = definition.borders.get(region, {})
                        for side in ("top", "right", "bottom", "left"):
                            key = side
                            if region == "wholeTbl":
                                if side == "top" and row_index > 0 or side == "bottom" and row_index + row_span < len(row_nodes):
                                    key = "insideH"
                                elif side == "left" and col > 0 or side == "right" and col + col_span < column_count:
                                    key = "insideV"
                            if key in region_borders:
                                borders[side] = region_borders[key]
                        styled = definition.regions.get(region)
                        if styled:
                            effective = _merge_cell_style(effective, styled)
                            for field_name in TableCellStyle.model_fields:
                                if getattr(styled, field_name) is not None:
                                    provenance[field_name] = {
                                        "source": "table_style",
                                        "table_style_id": style_id,
                                        "table_style_name": definition.style_name,
                                        "region": region,
                                    }
                effective = _merge_cell_style(effective, explicit)
                for field_name in TableCellStyle.model_fields:
                    if getattr(explicit, field_name) is not None:
                        provenance[field_name] = {
                            "source": "local_tcPr" if field_name == "fill" else "cell_text",
                            "table_style_id": style_id,
                        }
                text_body = first_child(cell, "txBody")
                rich = (
                    parse_rich_text(
                        text_body,
                        theme=theme,
                        theme_colors=theme_colors,
                        color_map=color_map,
                        relationships=relationships,
                        fallback_font=theme.minor_font if theme else None,
                    )
                    if text_body is not None
                    else None
                )
                text = raw_text(rich) or ""
                tc_pr = first_child(cell, "tcPr")
                merge_state = (
                    "combined"
                    if h_merge and v_merge
                    else "horizontal_continuation"
                    if h_merge
                    else "vertical_continuation"
                    if v_merge
                    else "horizontal_origin"
                    if col_span > 1
                    else "vertical_origin"
                    if row_span > 1
                    else "none"
                )
                cells.append(
                    TableCellModel(
                        row=row_index,
                        col=col,
                        row_span=row_span,
                        col_span=1 if h_merge else col_span,
                        h_merge=h_merge,
                        v_merge=v_merge,
                        merge_state=merge_state,
                        text=text,
                        rich_text=rich,
                        tc_pr=_raw_attributes(tc_pr),
                        margins={
                            "left_emu": int_attr(tc_pr, "marL"),
                            "right_emu": int_attr(tc_pr, "marR"),
                            "top_emu": int_attr(tc_pr, "marT"),
                            "bottom_emu": int_attr(tc_pr, "marB"),
                        },
                        vertical_alignment=tc_pr.get("anchor") if tc_pr is not None else None,
                        paragraphs=_cell_paragraphs(cell),
                        effective_regions=regions,
                        explicit_style=explicit,
                        effective_style=effective,
                        provenance=provenance,
                        style=effective,
                        borders={
                            "top": _border(cell, "lnT", theme_colors, color_map) or borders.get("top"),
                            "right": _border(cell, "lnR", theme_colors, color_map) or borders.get("right"),
                            "bottom": _border(cell, "lnB", theme_colors, color_map) or borders.get("bottom"),
                            "left": _border(cell, "lnL", theme_colors, color_map) or borders.get("left"),
                        },
                    )
                )
                if h_merge:
                    col += 1
                else:
                    following = 0
                    for candidate in cell_nodes[cell_index + 1 : cell_index + col_span]:
                        if not bool_attr(candidate, "hMerge"):
                            break
                        following += 1
                    col += 1 if following else col_span
            rows.append(TableRowModel(index=row_index, height_emu=int_attr(row, "h"), cells=cells))
        all_cells = [cell for row in rows for cell in row.cells]
        header_cells = rows[0].cells if rows and properties.first_row else []
        body_cells = [cell for row in (rows[1:] if properties.first_row else rows) for cell in row.cells]
        rel_ids = _relationship_ids(table)
        table_relationships = [edge for rel_id in rel_ids if (edge := relationships.by_id.get(rel_id))]
        result.append(
            TableModel(
                table_id=f"{scope_id}_table_{table_index}",
                source_level=source_level,
                source_part=source_part,
                parent_scope_id=scope_id,
                relationship_ids=rel_ids,
                relationships=table_relationships,
                table_style_id=style_id,
                table_style_name=definition.style_name if definition else None,
                style_resolved=definition is not None,
                properties=properties,
                tbl_pr=_raw_attributes(tbl_pr),
                row_count=len(rows),
                grid_column_count=column_count,
                grid_column_widths_emu=widths,
                logical_visible_column_count=max(
                    (len([cell for cell in row.cells if not cell.h_merge and not cell.v_merge]) for row in rows),
                    default=0,
                ),
                col_count=column_count,
                rows=rows,
                header=_dominant(header_cells),
                body=_dominant(body_cells or all_cells),
                border_color=next(iter(Counter(
                    border.color for cell in all_cells for border in cell.borders.values()
                    if border and border.visible and border.color
                ).most_common(1)), (None, 0))[0],
                border_width_pt=next(iter(Counter(
                    border.width_pt for cell in all_cells for border in cell.borders.values()
                    if border and border.visible and border.width_pt is not None
                ).most_common(1)), (None, 0))[0],
                banded_rows=properties.banded_rows,
            )
        )
    return result


def link_tables(tables: list[TableModel], objects: list[ShapeElementModel]) -> None:
    by_part: dict[str, list[str]] = {}
    for shape in objects:
        if shape.object_kind == "table":
            by_part.setdefault(shape.source_part, []).append(shape.object_id)
    used: dict[str, int] = {}
    for table in tables:
        index = used.get(table.source_part, 0)
        candidates = by_part.get(table.source_part, [])
        table.linked_object_id = candidates[index] if index < len(candidates) else None
        used[table.source_part] = index + 1


CHART_TYPES = {
    "barChart": "bar",
    "bar3DChart": "bar_3d",
    "lineChart": "line",
    "line3DChart": "line_3d",
    "pieChart": "pie",
    "pie3DChart": "pie_3d",
    "doughnutChart": "doughnut",
    "areaChart": "area",
    "scatterChart": "scatter",
    "bubbleChart": "bubble",
    "radarChart": "radar",
    "surfaceChart": "surface",
    "stockChart": "stock",
}


def _series_name(series: etree._Element) -> str | None:
    tx = first_child(series, "tx")
    values = [item.text or "" for item in (tx.iter() if tx is not None else []) if local_name(item) == "v"]
    return next((value for value in values if value), None)


def _data_source(node: etree._Element) -> ChartDataSource:
    formula = first_descendant(node, "f")
    count = first_descendant(node, "ptCount")
    fmt = first_descendant(node, "formatCode")
    points = {}
    for point in node.iter():
        if local_name(point) != "pt":
            continue
        index = int_attr(point, "idx")
        value = first_child(point, "v")
        if index is not None and index >= 0:
            points[index] = value.text if value is not None else None
    return ChartDataSource(
        formula=formula.text if formula is not None else None,
        point_count=max(int_attr(count, "val", 0) or 0, max(points, default=-1) + 1),
        points=points,
        format_code=fmt.text if fmt is not None else None,
    )


def _numeric_values(source: ChartDataSource | None) -> list[float | None]:
    if source is None:
        return []
    result = []
    for index in range(source.point_count):
        try:
            value = float(source.points[index])
            result.append(value if math.isfinite(value) else None)
        except (KeyError, TypeError, ValueError):
            result.append(None)
    return result


def parse_chart(
    root: etree._Element,
    *,
    chart_id: str,
    linked_object_id: str,
    source_level: str,
    source_part: str,
    parent_scope_id: str,
    theme: ThemeModel | None,
    theme_colors: dict[str, str],
    color_map: dict[str, str],
) -> ChartModel:
    chart_node = next((item for item in root.iter() if local_name(item) in CHART_TYPES), None)
    chart_type = CHART_TYPES.get(local_name(chart_node)) if chart_node is not None else None
    grouping = first_descendant(chart_node, "grouping")
    subtype = grouping.get("val") if grouping is not None else None
    bar_dir = first_descendant(chart_node, "barDir")
    if chart_type == "bar" and bar_dir is not None and bar_dir.get("val") == "col":
        chart_type = "clustered_column" if subtype == "clustered" else "column"
    elif chart_type == "bar" and subtype == "clustered":
        chart_type = "clustered_bar"
    series_models: list[ChartSeriesModel] = []
    colors: list[str] = []
    for ordinal, series in enumerate(
        [item for item in root.iter() if local_name(item) == "ser"]
    ):
        index_node = first_child(series, "idx")
        index = int_attr(index_node, "val", ordinal) or ordinal
        color = resolve_solid_fill(first_child(series, "spPr"), theme_colors, color_map).value
        if color and color not in colors:
            colors.append(color)
        sources = {local_name(node): _data_source(node) for node in series
                   if local_name(node) in {"cat", "val", "xVal", "yVal", "bubbleSize"}}
        categories = sources.get("cat")
        series_models.append(ChartSeriesModel(
            index=index, name=_series_name(series), color=color,
            categories=[categories.points.get(i) for i in range(categories.point_count)] if categories else [],
            values=_numeric_values(sources.get("val") or sources.get("yVal")),
            x_values=_numeric_values(sources.get("xVal")),
            bubble_sizes=_numeric_values(sources.get("bubbleSize")),
            data_sources=sources,
        ))
    title = first_descendant(root, "title")
    title_run = first_descendant(title, {"rPr", "defRPr"})
    title_size = int_attr(title_run, "sz")
    title_color = resolve_color(title_run, theme_colors, color_map)
    latin = first_descendant(title_run, "latin")
    title_style = (
        TextStyle(
            font_family=resolve_typeface(latin.get("typeface") if latin is not None else None, theme),
            font_size_pt=title_size / 100 if title_size is not None else None,
            font_weight=700 if title_run is not None and title_run.get("b") in {"1", "true", "on"} else None,
            color=title_color.value,
            theme_color_ref=title_color.theme_color_ref,
            source="theme" if title_color.theme_color_ref else "resolved_usage",
        )
        if title_run is not None
        else None
    )
    return ChartModel(
        chart_id=chart_id,
        linked_object_id=linked_object_id,
        source_level=source_level,
        source_part=source_part,
        parent_scope_id=parent_scope_id,
        chart_type=chart_type,
        subtype=subtype,
        series_count=len(series_models),
        actual_series=series_models,
        available_style_palette=colors,
        series_colors=colors,
        title_style=title_style,
        axis_label_style=None,
    )
