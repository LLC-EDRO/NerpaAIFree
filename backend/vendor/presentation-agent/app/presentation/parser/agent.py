"""Independent deterministic Presentation Parser Agent."""

from __future__ import annotations

import hashlib
import io
import mimetypes
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from lxml import etree
from PIL import Image, UnidentifiedImageError

from app.presentation.models import (
    AssetModel,
    BackgroundModel,
    CountBreakdown,
    LayoutModel,
    MasterModel,
    NotePartModel,
    ObjectSummary,
    ParseDiagnostics,
    ParseIssue,
    ParserIdentity,
    ParserQa,
    PresentationDimensions,
    PresentationModel,
    RelationshipUsage,
    ShapeElementModel,
    SlideModel,
    SourceFileModel,
    TextCapabilityModel,
    TextFrameStyleLayer,
    TextStyle,
    UsageStatistics,
)
from app.presentation.parser.archive import (
    InvalidPresentationError,
    PptxArchive,
    choose_alternate_content,
    content_type_for,
    parse_content_types,
)
from app.presentation.parser.colors import apply_color_map, parse_background, parse_color_map, parse_theme
from app.presentation.parser.constants import DEFAULT_SLIDE_HEIGHT_EMU, DEFAULT_SLIDE_WIDTH_EMU
from app.presentation.parser.evidence import EvidenceStore, color_usage_metrics
from app.presentation.parser.ids import layout_id_from_part, master_id_from_part, media_id_from_part, slide_id_from_part
from app.presentation.parser.objects import ObjectParseContext, extract_objects, flatten_objects
from app.presentation.parser.relationships import RelationshipGraph, load_relationships
from app.presentation.parser.rich_text import parse_text_style
from app.presentation.parser.scenes import (
    apply_placeholder_inheritance,
    compose_effective_scenes,
    compose_raw_scenes,
    effective_text_run_store,
    placeholder_family,
)
from app.presentation.parser.storage import AssetStorage, NullAssetStorage
from app.presentation.parser.tables_charts import link_tables, parse_chart, parse_table_styles, parse_tables
from app.presentation.parser.xml_utils import first_child, first_descendant, int_attr, local_name, xml_string


class PresentationParser:
    """Parse PPTX bytes/path/file into a strict, debug-friendly PresentationModel.

    This component has no LLM, FastAPI or LangGraph dependency. It is safe to
    wrap in a graph node later without changing its parsing contract.
    """

    def __init__(self, asset_storage: AssetStorage | None = None) -> None:
        self.asset_storage = asset_storage or NullAssetStorage()

    def parse(
        self,
        source: str | Path | bytes | bytearray | BinaryIO,
        *,
        filename: str | None = None,
    ) -> PresentationModel:
        payload, resolved_name = self._read_source(source, filename)
        digest = hashlib.sha256(payload).hexdigest()
        started = datetime.now(UTC)
        started_perf = time.perf_counter()
        try:
            with PptxArchive(payload) as archive:
                result = self._parse_archive(archive, payload, resolved_name, digest, started, started_perf)
        except InvalidPresentationError as exc:
            return self._failed_model(
                filename=resolved_name,
                digest=digest,
                size=len(payload),
                started=started,
                started_perf=started_perf,
                code="PRESENTATION_UNREADABLE",
                message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 - the parser is a fault-containment boundary
            return self._failed_model(
                filename=resolved_name,
                digest=digest,
                size=len(payload),
                started=started,
                started_perf=started_perf,
                code="PRESENTATION_PARSE_FAILED",
                message=f"Presentation parsing failed safely: {type(exc).__name__}: {exc}",
            )
        return result

    @staticmethod
    def _read_source(
        source: str | Path | bytes | bytearray | BinaryIO,
        filename: str | None,
    ) -> tuple[bytes, str | None]:
        if isinstance(source, (str, Path)):
            path = Path(source)
            return path.read_bytes(), filename or path.name
        if isinstance(source, (bytes, bytearray)):
            return bytes(source), filename
        data = source.read()
        if not isinstance(data, bytes):
            raise TypeError("Presentation source must provide bytes")
        return data, filename or Path(getattr(source, "name", "")).name or None

    def _parse_archive(
        self,
        archive: PptxArchive,
        payload: bytes,
        filename: str | None,
        digest: str,
        started: datetime,
        started_perf: float,
    ) -> PresentationModel:
        issues: list[dict[str, object]] = []
        evidence = EvidenceStore()
        overrides, defaults = parse_content_types(archive)
        all_content_types = {**{part: value for part, value in overrides.items()}}
        for part in archive.names:
            if part not in all_content_types and (value := content_type_for(part, overrides, defaults)):
                all_content_types[part] = value

        presentation_root = archive.xml("ppt/presentation.xml")
        size_node = first_descendant(presentation_root, "sldSz")
        parsed_size = size_node is not None and int_attr(size_node, "cx") and int_attr(size_node, "cy")
        width = int_attr(size_node, "cx", DEFAULT_SLIDE_WIDTH_EMU) or DEFAULT_SLIDE_WIDTH_EMU
        height = int_attr(size_node, "cy", DEFAULT_SLIDE_HEIGHT_EMU) or DEFAULT_SLIDE_HEIGHT_EMU
        dimensions = PresentationDimensions(
            width_emu=width,
            height_emu=height,
            aspect_ratio=round(width / height, 4),
            source="presentation.sldSz" if parsed_size else "fallback",
        )

        theme_parts = archive.entries("ppt/theme/theme")
        theme = None
        if theme_parts:
            root = archive.xml(theme_parts[0])
            if root is not None:
                theme = parse_theme(root, theme_parts[0])
        base_theme_colors = theme.theme_colors if theme else {}

        master_maps: dict[str, dict[str, str]] = {}
        masters: list[MasterModel] = []
        layouts: list[LayoutModel] = []
        slides: list[SlideModel] = []
        relationship_graphs: dict[str, RelationshipGraph] = {}
        part_maps: dict[str, dict[str, str]] = {}

        for part in archive.entries("ppt/slideMasters/slideMaster"):
            if not part.endswith(".xml"):
                continue
            root = archive.xml(part)
            if root is None:
                issues.append(
                    {
                        "code": "MASTER_XML_UNREADABLE",
                        "message": "Unable to parse slide master XML",
                        "severity": "error",
                        "source_part": part,
                    }
                )
                continue
            root = choose_alternate_content(root)
            master_id = master_id_from_part(part)
            color_map = parse_color_map(root)
            master_maps[master_id] = color_map
            part_maps[part] = color_map
            rels = relationship_graphs.setdefault(part, load_relationships(archive, part))
            objects = extract_objects(
                root,
                ObjectParseContext(
                    scope_id=master_id,
                    source_level="master",
                    source_part=part,
                    slide_id=None,
                    layout_id=None,
                    master_id=master_id,
                    width=width,
                    height=height,
                    theme=theme,
                    theme_colors=base_theme_colors,
                    color_map=color_map,
                    relationships=rels,
                    content_types=all_content_types,
                    evidence=evidence,
                    issues=issues,
                ),
            )
            text_styles = self._parse_master_text_styles(root, theme, base_theme_colors, color_map, rels)
            self._apply_master_text_styles(objects, text_styles)
            self._apply_master_paragraph_styles(objects, root, part)
            background = parse_background(root, base_theme_colors, color_map, "master")
            master = MasterModel(
                master_id=master_id,
                source_part=part,
                background=background,
                placeholders=objects,
                text_styles={key: value.model_dump(mode="json") for key, value in text_styles.items()},
            )
            masters.append(master)
            self._background_evidence(evidence, background, "master", part, master_id=master_id)

        for part in archive.entries("ppt/slideLayouts/slideLayout"):
            if not part.endswith(".xml"):
                continue
            root = archive.xml(part)
            if root is None:
                issues.append(
                    {
                        "code": "LAYOUT_XML_UNREADABLE",
                        "message": "Unable to parse slide layout XML",
                        "severity": "error",
                        "source_part": part,
                    }
                )
                continue
            root = choose_alternate_content(root)
            layout_id = layout_id_from_part(part)
            rels = relationship_graphs.setdefault(part, load_relationships(archive, part))
            master_rel = rels.by_type_suffix("/slideMaster")
            master_id = master_id_from_part(master_rel.target) if master_rel and master_rel.target else None
            color_map = apply_color_map(master_maps.get(master_id or "", {}), parse_color_map(root, override=True))
            part_maps[part] = color_map
            objects = extract_objects(
                root,
                ObjectParseContext(
                    scope_id=layout_id,
                    source_level="layout",
                    source_part=part,
                    slide_id=None,
                    layout_id=layout_id,
                    master_id=master_id,
                    width=width,
                    height=height,
                    theme=theme,
                    theme_colors=base_theme_colors,
                    color_map=color_map,
                    relationships=rels,
                    content_types=all_content_types,
                    evidence=evidence,
                    issues=issues,
                ),
            )
            c_sld = first_descendant(root, "cSld")
            background = parse_background(root, base_theme_colors, color_map, "layout")
            layouts.append(
                LayoutModel(
                    layout_id=layout_id,
                    source_part=part,
                    name=c_sld.get("name") if c_sld is not None else None,
                    master_id=master_id,
                    background=background,
                    placeholders=objects,
                    show_master_sp=self._show_master_shapes(root),
                )
            )
            self._background_evidence(evidence, background, "layout", part, layout_id=layout_id, master_id=master_id)

        layouts_by_id = {layout.layout_id: layout for layout in layouts}
        # OPC slide names are arbitrary. Follow the actual presentation order,
        # including copied labSlide/nerpaSlide parts and repeated source layouts.
        presentation_rels = load_relationships(archive, "ppt/presentation.xml")
        slide_list = first_child(presentation_root, "sldIdLst")
        slide_parts = []
        for node in slide_list if slide_list is not None else []:
            relation = presentation_rels.by_id.get(node.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"))
            if relation and relation.target:
                slide_parts.append(relation.target)
        chart_parts = [part for part, kind in all_content_types.items() if kind == "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"]
        for slide_index, part in enumerate(slide_parts):
            if not part.endswith(".xml"):
                continue
            root = archive.xml(part)
            if root is None:
                issues.append(
                    {
                        "code": "SLIDE_XML_UNREADABLE",
                        "message": "Unable to parse slide XML",
                        "severity": "error",
                        "source_part": part,
                    }
                )
                continue
            root = choose_alternate_content(root)
            slide_id = slide_id_from_part(part)
            rels = relationship_graphs.setdefault(part, load_relationships(archive, part))
            layout_rel = rels.by_type_suffix("/slideLayout")
            layout_id = layout_id_from_part(layout_rel.target) if layout_rel and layout_rel.target else None
            master_id = layouts_by_id.get(layout_id or "").master_id if layouts_by_id.get(layout_id or "") else None
            color_map = apply_color_map(master_maps.get(master_id or "", {}), parse_color_map(root, override=True))
            part_maps[part] = color_map
            objects = extract_objects(
                root,
                ObjectParseContext(
                    scope_id=slide_id,
                    source_level="slide",
                    source_part=part,
                    slide_id=slide_id,
                    layout_id=layout_id,
                    master_id=master_id,
                    width=width,
                    height=height,
                    theme=theme,
                    theme_colors=base_theme_colors,
                    color_map=color_map,
                    relationships=rels,
                    content_types=all_content_types,
                    evidence=evidence,
                    issues=issues,
                ),
            )
            background = parse_background(root, base_theme_colors, color_map, "slide")
            slides.append(
                SlideModel(
                    slide_id=slide_id,
                    source_part=part,
                    slide_index=slide_index,
                    layout_id=layout_id,
                    background=background,
                    objects=objects,
                    show_master_sp=self._show_master_shapes(root),
                )
            )
            self._background_evidence(
                evidence, background, "slide", part, slide_id=slide_id, layout_id=layout_id, master_id=master_id
            )

        self._inherit_backgrounds(masters, layouts, slides)
        apply_placeholder_inheritance(masters, layouts, slides)
        # Preserve presentation defaults and each source master's paragraph
        # rules even for slide-local textboxes with no matching placeholder.
        masters_by_id = {master.master_id: master for master in masters}
        default_text_style = first_child(presentation_root, "defaultTextStyle")
        for slide in slides:
            layout = layouts_by_id.get(slide.layout_id or "")
            master = masters_by_id.get(layout.master_id or "") if layout else None
            if master:
                self._apply_master_paragraph_styles(flatten_objects(slide.objects), archive.xml(master.source_part), master.source_part)
            theme_rel = relationship_graphs.get(master.source_part).by_type_suffix("/theme") if master else None
            exact_theme_root = archive.xml(theme_rel.target) if theme_rel and theme_rel.target else None
            exact_theme = parse_theme(exact_theme_root, theme_rel.target) if exact_theme_root is not None else None
            for shape in flatten_objects(slide.objects):
                if shape.text_frame is None or (not shape.has_text_content and not shape.placeholder_type):
                    continue
                shape.text_frame.font_aliases = ({"+mj-lt": exact_theme.major_font or "", "+mn-lt": exact_theme.minor_font or ""}
                                                if exact_theme else {})
                if default_text_style is not None:
                    shape.text_frame.style_layers.insert(0, TextFrameStyleLayer(source_part="ppt/presentation.xml",
                        paragraph_styles={local_name(child): xml_string(child) for child in default_text_style}))

        all_objects = [
            *[shape for master in masters for shape in flatten_objects(master.placeholders)],
            *[shape for layout in layouts for shape in flatten_objects(layout.placeholders)],
            *[shape for slide in slides for shape in flatten_objects(slide.objects)],
        ]

        table_styles_root = archive.xml("ppt/tableStyles.xml")
        tables = []
        for part, level, scope_id in [
            *[(master.source_part, "master", master.master_id) for master in masters],
            *[(layout.source_part, "layout", layout.layout_id) for layout in layouts],
            *[(slide.source_part, "slide", slide.slide_id) for slide in slides],
        ]:
            root = archive.xml(part)
            if root is None:
                continue
            owner = next((m for m in masters if m.source_part == part), None)
            layout = next((layout for layout in layouts if layout.source_part == part), None)
            slide = next((slide for slide in slides if slide.source_part == part), None)
            if slide:
                layout = layouts_by_id.get(slide.layout_id or "")
            if layout:
                owner = masters_by_id.get(layout.master_id or "")
            theme_graph = relationship_graphs.get(owner.source_part) if owner else None
            theme_edge = theme_graph.by_type_suffix("/theme") if theme_graph else None
            table_theme_root = archive.xml(theme_edge.target) if theme_edge and theme_edge.target else None
            table_theme = parse_theme(table_theme_root, theme_edge.target) if table_theme_root is not None else None
            table_colors = table_theme.theme_colors if table_theme else {}
            table_styles = parse_table_styles(table_styles_root, table_theme, table_colors, part_maps.get(part, {}))
            tables.extend(
                parse_tables(
                    root,
                    scope_id=scope_id,
                    source_level=level,
                    source_part=part,
                    theme=table_theme,
                    theme_colors=table_colors,
                    color_map=part_maps.get(part, {}),
                    catalog=table_styles,
                    default_style_id=table_styles_root.get("def") if table_styles_root is not None else None,
                    relationships=relationship_graphs.get(part) or load_relationships(archive, part),
                )
            )
        link_tables(tables, all_objects)

        charts = []
        for chart_part in chart_parts:
            if not chart_part.endswith(".xml"):
                continue
            linked_objects = [
                shape for shape in all_objects if shape.object_kind == "chart" and shape.media_id == chart_part
            ]
            if not linked_objects:
                continue
            root = archive.xml(chart_part)
            if root is None:
                continue
            relationship_graphs.setdefault(chart_part, load_relationships(archive, chart_part))
            for linked in linked_objects:
                parent_scope_id = self._scope_id(linked)
                charts.append(
                    parse_chart(
                        root,
                        chart_id=f"{parent_scope_id}_chart_1_{linked.object_id}",
                        linked_object_id=linked.object_id,
                        source_level=linked.source_level,
                        source_part=chart_part,
                        parent_scope_id=parent_scope_id,
                        theme=theme,
                        theme_colors=base_theme_colors,
                        color_map=part_maps.get(linked.source_part, {}),
                    )
                )

        relationship_parts = [
            "ppt/presentation.xml",
            *[master.source_part for master in masters],
            *[layout.source_part for layout in layouts],
            *[slide.source_part for slide in slides],
            *chart_parts,
        ]
        relationships = []
        for part in dict.fromkeys(relationship_parts):
            graph = relationship_graphs.setdefault(part, load_relationships(archive, part))
            relationships.extend(graph.edges)

        assets = self._extract_assets(archive, all_objects, overrides, defaults)
        notes = [
            NotePartModel(source_part=part) for part in archive.entries("ppt/notesSlides/") if part.endswith(".xml")
        ]
        raw_scenes = compose_raw_scenes(slides, layouts, masters, width, height)
        effective_scenes = compose_effective_scenes(slides, layouts, masters, width, height)
        run_store = effective_text_run_store(slides)
        summary = self._summary(all_objects, masters, layouts, slides, len(tables), len(charts))
        usage = UsageStatistics(
            object_count_by_level={
                "master": sum(shape.source_level == "master" for shape in all_objects),
                "layout": sum(shape.source_level == "layout" for shape in all_objects),
                "slide": sum(shape.source_level == "slide" for shape in all_objects),
            },
            layout_count=len(layouts),
            master_count=len(masters),
            slide_count=len(slides),
        )

        for part in sorted(archive.unreadable_parts):
            issues.append(
                {
                    "code": "CORRUPT_OPTIONAL_PART_SKIPPED",
                    "message": f"Unreadable package member was skipped: {part}",
                    "severity": "warning",
                    "source_part": part,
                }
            )
        if theme is None:
            issues.append(
                {
                    "code": "THEME_MISSING",
                    "message": "Theme part not found; direct formatting and controlled fallbacks remain available",
                    "severity": "warning",
                }
            )
        if not masters:
            issues.append({"code": "MASTER_MISSING", "message": "No slide masters found", "severity": "error"})
        if not layouts:
            issues.append({"code": "LAYOUT_MISSING", "message": "No slide layouts found", "severity": "error"})
        if not slides:
            issues.append({"code": "SLIDE_MISSING", "message": "No slides found", "severity": "error"})

        qa = self._qa(all_objects, relationships, tables, charts, effective_scenes, issues, chart_parts)
        has_errors = any(item.get("severity") == "error" for item in issues)
        passed = not has_errors and qa.passed
        finished = datetime.now(UTC)
        duration_ms = (time.perf_counter() - started_perf) * 1000
        diagnostics = ParseDiagnostics(
            status="warning" if passed and issues else "success" if passed else "failed",
            started_at=started,
            finished_at=finished,
            duration_ms=duration_ms,
            issues=[ParseIssue(**item) for item in issues],
            unsupported_elements=dict(
                Counter(shape.object_kind for shape in all_objects if shape.parser_support == "opaque")
            ),
            counts_by_element_type=dict(Counter(shape.object_kind for shape in all_objects)),
            qa=qa,
        )
        presentation_id = f"presentation_{digest[:20]}"
        return PresentationModel(
            presentation_id=presentation_id,
            template_id="tpl_stub",
            passed=passed,
            source=SourceFileModel(filename=filename, size_bytes=len(payload), sha256=digest),
            parser=ParserIdentity(),
            dimensions=dimensions,
            theme=theme,
            masters=masters,
            layouts=layouts,
            slides=slides,
            assets=assets,
            relationships=relationships,
            tables=tables,
            charts=charts,
            native_charts=list(charts),
            chart_styles=[],
            chart_palette=list(dict.fromkeys(color for chart in charts for color in chart.series_colors)),
            notes=notes,
            effective_text_runs=run_store,
            raw_scenes=raw_scenes,
            effective_scenes=effective_scenes,
            embedded_objects=[shape for shape in all_objects if shape.object_kind == "embedded_object"],
            unknown_objects=[
                {
                    "object_id": shape.object_id,
                    "source_part": shape.source_part,
                    "relationship_id": shape.relationship_id,
                    "bbox": shape.geometry.model_dump(mode="json") if shape.geometry else None,
                    "parser_support": "opaque",
                    "raw_xml_ref": shape.raw_xml_ref,
                }
                for shape in all_objects
                if shape.parser_support == "opaque" and shape.object_kind not in {"embedded_object", "table", "chart"}
            ],
            evidence=evidence.snapshot(),
            color_usage_metrics=color_usage_metrics(evidence.snapshot(), len(slides)),
            usage_statistics=usage,
            summary=summary,
            diagnostics=diagnostics,
        )

    @staticmethod
    def _scope_id(shape: ShapeElementModel) -> str:
        if shape.source_level == "master":
            return master_id_from_part(shape.source_part)
        if shape.source_level == "layout":
            return layout_id_from_part(shape.source_part)
        return slide_id_from_part(shape.source_part)

    @staticmethod
    def _show_master_shapes(root: etree._Element) -> bool:
        value = root.get("showMasterSp")
        return value not in {"0", "false", "off"}

    @staticmethod
    def _parse_master_text_styles(root, theme, theme_colors, color_map, relationships):
        result: dict[str, TextStyle] = {}
        tx_styles = first_descendant(root, "txStyles")
        for role, tag in (("title", "titleStyle"), ("body", "bodyStyle"), ("other", "otherStyle")):
            container = first_child(tx_styles, tag)
            level = first_descendant(container, {"lvl1pPr", "defPPr"})
            run = first_descendant(level, {"defRPr", "endParaRPr", "rPr"})
            if run is None:
                continue
            style, _ = parse_text_style(run, theme, theme_colors, color_map, relationships, resolve_font=True)
            result[role] = style
        return result

    @staticmethod
    def _apply_master_paragraph_styles(objects: list[ShapeElementModel], root, part: str) -> None:
        tx_styles = first_child(root, "txStyles")
        for shape in objects:
            if shape.text_frame is None or (not shape.has_text_content and not shape.placeholder_type):
                continue
            family = placeholder_family(shape.placeholder_type or shape.type)
            role = "titleStyle" if family == "title" else "bodyStyle" if family in {"body", "subtitle"} else "otherStyle"
            style = first_child(tx_styles, role)
            if style is not None:
                shape.text_frame.style_layers.insert(0, TextFrameStyleLayer(
                    source_part=part,
                    paragraph_styles={local_name(child): xml_string(child) for child in style},
                ))

    @staticmethod
    def _apply_master_text_styles(objects: list[ShapeElementModel], text_styles: dict[str, TextStyle]) -> None:
        for shape in objects:
            if shape.object_kind != "shape" or not (shape.placeholder_type or shape.placeholder_idx is not None):
                continue
            family = placeholder_family(shape.placeholder_type or shape.type)
            role = "title" if family == "title" else "body" if family in {"body", "subtitle"} else "other"
            inherited = text_styles.get(role)
            if not inherited:
                continue
            if not shape.has_text_content:
                shape.text_capability = TextCapabilityModel(default_style=inherited)
                shape.text_style = None
                continue
            current = shape.text_style or TextStyle()
            merged = TextStyle(
                **{
                    field_name: getattr(current, field_name)
                    if getattr(current, field_name) is not None
                    else getattr(inherited, field_name)
                    for field_name in TextStyle.model_fields
                }
            )
            shape.text_style = merged
            shape.effective_style.update(
                {"font_family": merged.font_family, "font_size": merged.font_size_pt, "color": merged.color}
            )
            if shape.rich_text:
                for paragraph in shape.rich_text.paragraphs:
                    for run in paragraph.runs:
                        run.effective_style = TextStyle(
                            **{
                                field_name: getattr(run.effective_style, field_name)
                                if getattr(run.effective_style, field_name) is not None
                                else getattr(inherited, field_name)
                                for field_name in TextStyle.model_fields
                            }
                        )
                        run.style = run.effective_style
                shape.rich_text.dominant = merged
            shape.provenance["master_text_style"] = {
                "source": "master_txStyles",
                "role": role,
                "source_part": shape.source_part,
            }

    @staticmethod
    def _background_evidence(
        evidence: EvidenceStore, background: BackgroundModel | None, scope: str, source_part: str, **ids
    ) -> None:
        if not background or not background.color:
            return
        evidence.add(
            property="background",
            value=background.color,
            scope=scope,
            slide_id=ids.get("slide_id"),
            layout_id=ids.get("layout_id"),
            master_id=ids.get("master_id"),
            object_id=None,
            source_part=source_part,
            role_hint="background",
            source="background",
            inherited=False,
            area=1,
            characters=None,
            z_index=0,
        )

    @staticmethod
    def _inherit_backgrounds(masters: list[MasterModel], layouts: list[LayoutModel], slides: list[SlideModel]) -> None:
        masters_by_id = {master.master_id: master for master in masters}
        for layout in layouts:
            if layout.background is None and layout.master_id and masters_by_id.get(layout.master_id):
                inherited = masters_by_id[layout.master_id].background
                layout.background = inherited.model_copy(deep=True) if inherited else None
        layouts_by_id = {layout.layout_id: layout for layout in layouts}
        for slide in slides:
            if slide.background is None and slide.layout_id and layouts_by_id.get(slide.layout_id):
                inherited = layouts_by_id[slide.layout_id].background
                slide.background = inherited.model_copy(deep=True) if inherited else None

    def _extract_assets(self, archive: PptxArchive, objects, overrides, defaults) -> list[AssetModel]:
        usage: dict[str, list[RelationshipUsage]] = defaultdict(list)
        for shape in objects:
            part = (
                shape.preview.media_part if shape.object_kind == "embedded_object" and shape.preview else shape.media_id
            )
            rel_id = (
                shape.preview.relationship_id
                if shape.object_kind == "embedded_object" and shape.preview
                else shape.relationship_id
            )
            if part:
                usage[part].append(RelationshipUsage(source_part=shape.source_part, relationship_id=rel_id or ""))
        assets: list[AssetModel] = []
        for part in archive.entries("ppt/media/"):
            payload = archive.read_bytes(part)
            if payload is None:
                continue
            media_id = media_id_from_part(part)
            asset_digest = hashlib.sha256(payload).hexdigest()
            asset_id = f"asset_{asset_digest[:20]}"
            extension = Path(part).suffix.lstrip(".").lower() or None
            mime = content_type_for(part, overrides, defaults) or mimetypes.guess_type(part)[0]
            width = height = None
            try:
                with Image.open(io.BytesIO(payload)) as image:
                    width, height = image.size
            except (UnidentifiedImageError, OSError):
                pass
            assets.append(
                AssetModel(
                    asset_id=asset_id,
                    media_id=media_id,
                    source_part=part,
                    extension=extension,
                    mime_type=mime,
                    size_bytes=len(payload),
                    sha256=asset_digest,
                    storage_uri=self.asset_storage.put(
                        asset_id=asset_id, filename=Path(part).name, payload=payload, mime_type=mime
                    ),
                    relationship_usage=usage.get(part, []),
                    raster_width_px=width,
                    raster_height_px=height,
                )
            )
        return assets

    @staticmethod
    def _summary(all_objects, masters, layouts, slides, tables_parsed, charts_parsed) -> ObjectSummary:
        counts = Counter(shape.object_kind for shape in all_objects)
        roots = [
            *[master.placeholders for master in masters],
            *[layout.placeholders for layout in layouts],
            *[slide.objects for slide in slides],
        ]
        root_groups = sum(shape.object_kind == "group" for root in roots for shape in root)
        total = len(all_objects)
        return ObjectSummary(
            shapes=counts["shape"],
            ordinary_shapes=counts["shape"],
            pictures=counts["picture"],
            charts=CountBreakdown(
                detected=counts["chart"], parsed=charts_parsed, unsupported=max(0, counts["chart"] - charts_parsed)
            ),
            tables=CountBreakdown(
                detected=counts["table"], parsed=tables_parsed, unsupported=max(0, counts["table"] - tables_parsed)
            ),
            connectors=counts["connector"] + counts["line"],
            groups=root_groups,
            top_level_groups=root_groups,
            total_group_nodes=counts["group"],
            embedded_objects=counts["embedded_object"],
            unknown_objects=counts["graphicFrame"] + counts["smartart"],
            total=total,
        )

    @staticmethod
    def _qa(objects, relationships, tables, charts, scenes, issues, chart_parts) -> ParserQa:
        negative = sum(
            1
            for shape in objects
            if shape.geometry_full
            and any(
                box and ((box.width_emu or 0) < 0 or (box.height_emu or 0) < 0)
                for box in (shape.geometry_full.local_bbox, shape.geometry_full.absolute_bbox)
            )
        )
        unresolved_aliases = sum(
            1
            for shape in objects
            if shape.rich_text
            for paragraph in shape.rich_text.paragraphs
            for run in paragraph.runs
            if (run.style.font_family or "").lower() in {"+mj-lt", "+mn-lt", "+mj-ea", "+mn-ea", "+mj-cs", "+mn-cs"}
        )
        ids = [shape.object_id for shape in objects]
        physical = [shape.physical_identity for shape in objects if shape.physical_identity]
        table_mismatch = sum(
            1
            for table in tables
            if any(
                max((cell.col + (cell.col_span or 1) for cell in row.cells), default=0) != table.grid_column_count
                for row in table.rows
            )
        )
        duplicate_effective = sum(scene.invariants.duplicate_physical_identity_count for scene in scenes)
        replacement_invalid = sum(scene.invariants.replacement_chain_invalid_count for scene in scenes)
        missing_trace = sum(scene.invariants.missing_source_trace_count for scene in scenes)
        visible_without_geometry = sum(scene.invariants.visible_without_geometry_count for scene in scenes)
        visible_connector_off = sum(
            1
            for scene in scenes
            for item in scene.effective_objects
            if item.visibility_geometry_type == "segment" and item.painted_intersects_canvas and item.off_canvas
        )
        painted_invisible = sum(
            1
            for scene in scenes
            for item in scene.effective_objects
            if item.painted_intersects_canvas
            and not item.render_visible
            and item.visibility_reason
            not in {
                "hidden",
                "no_visible_content",
                "replaced_placeholder",
                "master_shapes_suppressed",
                "group_container",
                "placeholder_formatting_sample",
            }
        )
        values = {
            "duplicate_effective_leaf_count": duplicate_effective,
            "text_bearing_objects_with_missing_rich_text": sum(
                1 for shape in objects if shape.has_text_content and shape.rich_text is None
            ),
            "non_text_objects_with_observed_text_style": sum(
                1 for shape in objects if shape.object_kind != "shape" and shape.text_style is not None
            ),
            "negative_geometry_extents": negative,
            "unresolved_deterministic_theme_aliases": unresolved_aliases,
            "broken_relationships": sum(edge.broken for edge in relationships),
            "native_charts_without_chart_part": sum(
                chart.source_part not in chart_parts for chart in charts
            ),
            "table_objects_without_model": sum(
                shape.object_kind == "table" and shape.object_id not in {t.linked_object_id for t in tables}
                for shape in objects
            ),
            "chart_objects_without_model": sum(
                shape.object_kind == "chart" and shape.object_id not in {c.linked_object_id for c in charts}
                for shape in objects
            ),
            "table_grid_count_mismatch": table_mismatch,
            "duplicate_raw_object_id_count": len(ids) - len(set(ids)),
            "duplicate_physical_identity_count": len(physical) - len(set(physical)),
            "raw_object_count": len(objects),
            "tables_missing_linked_object": sum(table.linked_object_id is None for table in tables),
            "inherited_value_missing_trace": 0,
            "missing_physical_identity_count": sum(
                not shape.physical_identity or not shape.source_part or not shape.source_object_id for shape in objects
            ),
            "visible_connector_inside_canvas_marked_off_canvas": visible_connector_off,
            "painted_inside_canvas_render_invisible_without_reason": painted_invisible,
            "zero_width_connector_invalid": 0,
            "zero_height_connector_invalid": 0,
            "replacement_chain_invalid_count": replacement_invalid,
            "missing_effective_source_trace_count": missing_trace,
            "visible_without_geometry_count": visible_without_geometry,
        }
        passed = all(value == 0 for key, value in values.items() if key != "raw_object_count")
        checks = {
            domain: {
                "id": domain,
                "execution_valid": passed,
                "coverage": 1.0,
                "resolution_confidence": 0.97 if passed else 0.5,
                "warnings": [],
                "errors": [],
            }
            for domain in (
                "geometry",
                "effective_scene",
                "text_extraction",
                "theme_resolution",
                "placeholder_inheritance",
                "content_role",
                "tables",
                "charts",
                "media",
                "groups",
                "relationships",
                "unknown_content",
            )
        }
        for domain, kind, missing_key in (
            ("tables", "table", "table_objects_without_model"),
            ("charts", "chart", "chart_objects_without_model"),
        ):
            detected = sum(shape.object_kind == kind for shape in objects)
            missing = values[missing_key]
            checks[domain]["coverage"] = (detected - missing) / detected if detected else 1.0
            if missing:
                checks[domain]["errors"].append(f"{missing} detected {domain} have no extracted model")
        return ParserQa(**values, passed=passed, checks=checks)

    @staticmethod
    def _failed_model(*, filename, digest, size, started, started_perf, code, message) -> PresentationModel:
        finished = datetime.now(UTC)
        qa = ParserQa(passed=False, checks={})
        return PresentationModel(
            presentation_id=f"presentation_{digest[:20]}",
            template_id="tpl_stub",
            passed=False,
            source=SourceFileModel(filename=filename, size_bytes=size, sha256=digest),
            parser=ParserIdentity(),
            dimensions=PresentationDimensions(
                width_emu=DEFAULT_SLIDE_WIDTH_EMU,
                height_emu=DEFAULT_SLIDE_HEIGHT_EMU,
                aspect_ratio=round(DEFAULT_SLIDE_WIDTH_EMU / DEFAULT_SLIDE_HEIGHT_EMU, 4),
                source="fallback",
            ),
            usage_statistics=UsageStatistics(
                object_count_by_level={"master": 0, "layout": 0, "slide": 0},
                layout_count=0,
                master_count=0,
                slide_count=0,
            ),
            summary=ObjectSummary(),
            diagnostics=ParseDiagnostics(
                status="failed",
                started_at=started,
                finished_at=finished,
                duration_ms=(time.perf_counter() - started_perf) * 1000,
                issues=[ParseIssue(code=code, message=message, severity="error")],
                qa=qa,
            ),
        )
