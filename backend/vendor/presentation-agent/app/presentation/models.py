"""Strict public contract produced by the deterministic PPTX Parser Agent."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class SourceFileModel(ContractModel):
    filename: str | None = None
    size_bytes: int
    sha256: str


class ParserIdentity(ContractModel):
    agent_id: str = "presentation_parser"
    agent_version: str = "1.1.0"
    engine: str = "python-ooxml-dom"
    engine_version: str = "1"
    deterministic: bool = True
    uses_llm: bool = False


class PresentationDimensions(ContractModel):
    width_emu: int
    height_emu: int
    aspect_ratio: float
    source: Literal["presentation.sldSz", "fallback"]


class GeometryModel(ContractModel):
    x_emu: int | None = None
    y_emu: int | None = None
    width_emu: int | None = None
    height_emu: int | None = None


class NormalizedBBox(ContractModel):
    x: float
    y: float
    width: float
    height: float


class GroupTransformStep(ContractModel):
    x_emu: float
    y_emu: float
    width_emu: float
    height_emu: float
    rotation_deg: float
    flip_h: bool
    flip_v: bool


class GeometryFullModel(ContractModel):
    source_frame_bbox: GeometryModel | None = None
    extent_source: str = "xfrm"
    local_bbox: GeometryModel | None = None
    absolute_bbox: GeometryModel | None = None
    normalized_bbox: NormalizedBBox | None = None
    rotation_deg: float = 0.0
    flip_h: bool = False
    flip_v: bool = False
    z_index: int
    painted_area_ratio: float = 0.0
    group_transform_chain: list[GroupTransformStep] = Field(default_factory=list)
    nesting_depth: int = 0
    off_canvas_class: (
        Literal["intentional_bleed", "partially_visible", "fully_off_canvas", "suspicious_off_canvas"] | None
    ) = None
    clipped: bool = False
    painted_bbox: GeometryModel | None = None
    painted_normalized_bbox: NormalizedBBox | None = None
    painted_intersects_canvas: bool = False
    visibility_geometry_type: Literal["segment", "area"] = "area"


class ColorValue(ContractModel):
    color: str | None = None
    theme_color_ref: str | None = None


class LineStyle(ColorValue):
    width_pt: float | None = None


class TextStyle(ContractModel):
    font_family: str | None = None
    font_size_pt: float | None = None
    font_weight: int | None = None
    bold: bool | None = None
    italic: bool | None = None
    underline: str | None = None
    language: str | None = None
    baseline: float | None = None
    character_spacing: float | None = None
    color: str | None = None
    theme_color_ref: str | None = None
    hyperlink: str | None = None
    source: Literal["resolved_usage", "theme"] | None = None


class PropertyProvenance(ContractModel):
    value: Any = None
    raw_value: Any = None
    source: str | None = None
    source_part: str | None = None
    source_object_id: str | None = None
    inherited: bool = False
    resolution_confidence: float | None = None
    trace: list[dict[str, Any]] = Field(default_factory=list)
    raw: dict[str, Any] | None = None


class TextRunModel(ContractModel):
    text: str
    raw_style: TextStyle
    effective_style: TextStyle
    style: TextStyle
    provenance: dict[str, PropertyProvenance | None] = Field(default_factory=dict)
    is_line_break: bool = False
    raw_properties_xml: str | None = None
    is_field: bool = False


class BulletModel(ContractModel):
    type: Literal["none", "char", "autoNum", "blip"] | None = None
    char: str | None = None
    font: str | None = None
    auto_num_type: str | None = None


class ParagraphProperties(ContractModel):
    alignment: str | None = None
    level: int = 0
    margin_left_emu: int | None = None
    margin_right_emu: int | None = None
    indent_emu: int | None = None
    line_spacing: float | None = None
    space_before: float | None = None
    space_after: float | None = None
    line_spacing_unit: Literal["percent", "points"] | None = None
    space_before_unit: Literal["percent", "points"] | None = None
    space_after_unit: Literal["percent", "points"] | None = None
    raw_xml: str | None = None


class TextParagraphModel(ContractModel):
    alignment: str | None = None
    level: int = 0
    properties: ParagraphProperties
    bullet: BulletModel | None = None
    runs: list[TextRunModel] = Field(default_factory=list)
    explicit_default_font_size_pt: float | None = None
    explicit_end_para_font_size_pt: float | None = None
    default_style: TextStyle | None = None
    end_style: TextStyle | None = None
    end_properties_xml: str | None = None


class RichTextModel(ContractModel):
    paragraphs: list[TextParagraphModel] = Field(default_factory=list)
    dominant: TextStyle


class StyleRefs(ContractModel):
    fill_ref_idx: int | None = None
    ln_ref_idx: int | None = None
    effect_ref_idx: int | None = None
    font_ref: Literal["major", "minor"] | None = None


class EmbeddingModel(ContractModel):
    kind: Literal["ole", "embedded_excel", "package", "unknown"] = "unknown"
    part: str | None = None
    content_type: str | None = None
    prog_id: str | None = None


class PreviewModel(ContractModel):
    relationship_id: str | None = None
    media_part: str | None = None
    media_id: str | None = None


class TextCapabilityModel(ContractModel):
    default_style: TextStyle | None = None


class TextFrameStyleLayer(ContractModel):
    """Observed formatting only; no source slide payload is copied here."""

    source_part: str
    source_object_id: str | None = None
    body_attributes: dict[str, str] = Field(default_factory=dict)
    body_children: list[str] = Field(default_factory=list)
    paragraph_styles: dict[str, str] = Field(default_factory=dict)


class TextFrameProperties(ContractModel):
    shape_geometry: str | None = None
    font_aliases: dict[str, str] = Field(default_factory=dict)
    # Low-to-high precedence; OOXML defaults are applied only by the consumer.
    style_layers: list[TextFrameStyleLayer] = Field(default_factory=list)


class ShapeElementModel(ContractModel):
    object_id: str
    shape_id: int | None = None
    placeholder_idx: int | None = None
    placeholder_type: str | None = None
    type: str
    name: str | None = None
    source_level: Literal["master", "layout", "slide"]
    source_part: str
    source_object_id: str
    physical_identity: str
    parent_object_id: str | None = None
    parent_group_path: str | None = None
    object_kind: Literal[
        "shape",
        "picture",
        "connector",
        "line",
        "group",
        "graphicFrame",
        "embedded_object",
        "table",
        "chart",
        "smartart",
    ]
    parser_support: Literal["full", "partial", "opaque"]
    z_index: int
    media_id: str | None = None
    relationship_id: str | None = None
    hyperlink: str | None = None
    hidden: bool = False
    has_text_content: bool = False
    rich_text_error: str | None = None
    text_style: TextStyle | None = None
    text_capability: TextCapabilityModel | None = None
    text_frame: TextFrameProperties | None = None
    fill: ColorValue | None = None
    line: LineStyle | None = None
    geometry: GeometryModel | None = None
    geometry_full: GeometryFullModel | None = None
    rich_text: RichTextModel | None = None
    effective_text_runs: list[dict[str, Any]] | None = None
    explicit_style: dict[str, Any] = Field(default_factory=dict)
    effective_style: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    style_refs: StyleRefs | None = None
    raw_xml_ref: dict[str, Any] | None = None
    embedding: EmbeddingModel | None = None
    preview: PreviewModel | None = None
    image_crop: dict[str, float | None] | None = None
    alt_text: str | None = None
    children: list[ShapeElementModel] = Field(default_factory=list)


class BackgroundModel(ColorValue):
    source: Literal["slide", "layout", "master", "theme", "unresolved"]


class MasterModel(ContractModel):
    master_id: str
    source_part: str
    background: BackgroundModel | None = None
    placeholders: list[ShapeElementModel] = Field(default_factory=list)
    text_styles: dict[str, Any] = Field(default_factory=dict)


class LayoutModel(ContractModel):
    layout_id: str
    source_part: str
    name: str | None = None
    master_id: str | None = None
    background: BackgroundModel | None = None
    placeholders: list[ShapeElementModel] = Field(default_factory=list)
    show_master_sp: bool = True


class SlideModel(ContractModel):
    slide_id: str
    source_part: str
    slide_index: int
    layout_id: str | None = None
    background: BackgroundModel | None = None
    objects: list[ShapeElementModel] = Field(default_factory=list)
    show_master_sp: bool = True


class RelationshipUsage(ContractModel):
    source_part: str
    relationship_id: str


class AssetModel(ContractModel):
    asset_id: str
    media_id: str
    source_part: str
    extension: str | None = None
    mime_type: str | None = None
    size_bytes: int
    sha256: str
    storage_uri: str | None = None
    relationship_usage: list[RelationshipUsage] = Field(default_factory=list)
    raster_width_px: int | None = None
    raster_height_px: int | None = None


class RelationshipModel(ContractModel):
    source_part: str
    relationship_id: str
    relationship_type: str | None = None
    target: str | None = None
    target_mode: str | None = None
    broken: bool = False


class ThemeFontFace(ContractModel):
    latin: str | None = None
    ea: str | None = None
    cs: str | None = None


class ThemeFontScheme(ContractModel):
    major: ThemeFontFace
    minor: ThemeFontFace


class FmtSchemeStyle(ContractModel):
    idx: int
    kind: Literal["fill", "line", "effect", "bgFill"]
    xml: str


class FmtSchemeModel(ContractModel):
    name: str | None = None
    fill_styles: list[FmtSchemeStyle] = Field(default_factory=list)
    line_styles: list[FmtSchemeStyle] = Field(default_factory=list)
    effect_styles: list[FmtSchemeStyle] = Field(default_factory=list)
    bg_fill_styles: list[FmtSchemeStyle] = Field(default_factory=list)


class ThemeModel(ContractModel):
    theme_id: str
    source_part: str
    color_scheme: list[str] = Field(default_factory=list)
    fonts: list[str] = Field(default_factory=list)
    theme_colors: dict[str, str] = Field(default_factory=dict)
    major_font: str | None = None
    minor_font: str | None = None
    major_font_ea: str | None = None
    minor_font_ea: str | None = None
    major_font_cs: str | None = None
    minor_font_cs: str | None = None
    font_scheme: ThemeFontScheme
    fmt_scheme: FmtSchemeModel | None = None


class TableCellStyle(ContractModel):
    fill: str | None = None
    fill_alpha: float | None = None
    fill_type: str | None = None
    italic: bool | None = None
    text_color: str | None = None
    font_family: str | None = None
    font_size_pt: float | None = None
    font_weight: int | None = None


class TableBorder(ContractModel):
    color: str | None = None
    width_pt: float | None = None
    visible: bool = True
    dash: str | None = None
    alpha: float | None = None


class TableCellModel(ContractModel):
    row: int
    col: int
    row_span: int = 1
    col_span: int = 1
    h_merge: bool = False
    v_merge: bool = False
    merge_state: str = "none"
    text: str = ""
    rich_text: RichTextModel | None = None
    tc_pr: dict[str, Any] = Field(default_factory=dict)
    margins: dict[str, int | None] = Field(default_factory=dict)
    vertical_alignment: str | None = None
    paragraphs: list[dict[str, Any]] = Field(default_factory=list)
    effective_regions: list[str] = Field(default_factory=list)
    explicit_style: TableCellStyle
    effective_style: TableCellStyle
    provenance: dict[str, Any] = Field(default_factory=dict)
    style: TableCellStyle
    borders: dict[str, TableBorder | None] = Field(default_factory=dict)


class TableRowModel(ContractModel):
    index: int
    height_emu: int | None = None
    cells: list[TableCellModel] = Field(default_factory=list)


class TableProperties(ContractModel):
    first_row: bool = False
    first_column: bool = False
    last_row: bool = False
    last_column: bool = False
    banded_rows: bool = False
    banded_columns: bool = False


class TableModel(ContractModel):
    table_id: str
    source_level: Literal["master", "layout", "slide"]
    source_part: str
    parent_scope_id: str
    linked_object_id: str | None = None
    relationship_ids: list[str] = Field(default_factory=list)
    relationships: list[RelationshipModel] = Field(default_factory=list)
    table_style_id: str | None = None
    table_style_name: str | None = None
    style_resolved: bool = False
    properties: TableProperties
    tbl_pr: dict[str, Any] = Field(default_factory=dict)
    row_count: int
    grid_column_count: int
    grid_column_widths_emu: list[int | None] = Field(default_factory=list)
    logical_visible_column_count: int
    col_count: int
    rows: list[TableRowModel] = Field(default_factory=list)
    header: TableCellStyle | None = None
    body: TableCellStyle | None = None
    border_color: str | None = None
    border_width_pt: float | None = None
    banded_rows: bool = False


class ChartDataSource(ContractModel):
    formula: str | None = None
    point_count: int = 0
    points: dict[int, str | None] = Field(default_factory=dict)
    format_code: str | None = None


class ChartSeriesModel(ContractModel):
    index: int
    name: str | None = None
    color: str | None = None
    categories: list[str | None] = Field(default_factory=list)
    values: list[float | None] = Field(default_factory=list)
    x_values: list[float | None] = Field(default_factory=list)
    bubble_sizes: list[float | None] = Field(default_factory=list)
    data_sources: dict[str, ChartDataSource] = Field(default_factory=dict)


class ChartModel(ContractModel):
    chart_id: str
    linked_object_id: str | None = None
    source_level: Literal["master", "layout", "slide"]
    source_part: str
    parent_scope_id: str
    chart_type: str | None = None
    subtype: str | None = None
    series_count: int = 0
    actual_series: list[ChartSeriesModel] = Field(default_factory=list)
    available_style_palette: list[str] = Field(default_factory=list)
    series_colors: list[str] = Field(default_factory=list)
    title_style: TextStyle | None = None
    axis_label_style: TextStyle | None = None


class CountBreakdown(ContractModel):
    detected: int = 0
    parsed: int = 0
    unsupported: int = 0


class ObjectSummary(ContractModel):
    shapes: int = 0
    ordinary_shapes: int = 0
    pictures: int = 0
    charts: CountBreakdown = Field(default_factory=CountBreakdown)
    tables: CountBreakdown = Field(default_factory=CountBreakdown)
    connectors: int = 0
    groups: int = 0
    top_level_groups: int = 0
    total_group_nodes: int = 0
    embedded_objects: int = 0
    unknown_objects: int = 0
    total: int = 0


class UsageStatistics(ContractModel):
    object_count_by_level: dict[Literal["master", "layout", "slide"], int]
    layout_count: int
    master_count: int
    slide_count: int


class ParseIssue(ContractModel):
    code: str
    message: str
    severity: Literal["warning", "error"] = "warning"
    source_part: str | None = None
    object_id: str | None = None


class EvidenceRecord(ContractModel):
    evidence_id: str
    property: str
    value: str | int | float | bool | None = None
    scope: Literal["master", "layout", "slide"]
    slide_id: str | None = None
    layout_id: str | None = None
    master_id: str | None = None
    object_id: str | None = None
    source_part: str | None = None
    role_hint: str | None = None
    source: str
    inherited: bool = False
    area: float | None = None
    characters: int | None = None
    z_index: int | None = None


class ColorUsageMetric(ContractModel):
    value: str
    occurrences: int
    unique_slides: int
    unique_layouts: int
    concentration: float
    line_count: int
    fill_count: int
    text_count: int
    approx_visual_area: float
    slide_coverage: float
    coverage_score: float
    concentration_penalty: float


class NotePartModel(ContractModel):
    source_part: str
    relationship_target: str | None = None
    unused: bool = True


class RawSceneObject(ContractModel):
    source_level: Literal["master", "layout", "slide"]
    source_part: str
    source_object_id: str
    physical_identity: str
    parent_group_path: str | None = None
    object_kind: str
    local_bbox: NormalizedBBox | None = None
    absolute_bbox: NormalizedBBox | None = None
    painted_bbox: NormalizedBBox | None = None


class RawSlideScene(ContractModel):
    slide_id: str
    slide_index: int
    layout_id: str | None = None
    master_id: str | None = None
    canvas: dict[str, int]
    raw_objects: list[RawSceneObject] = Field(default_factory=list)


class InheritanceStep(ContractModel):
    level: Literal["master", "layout", "slide"]
    object_id: str
    source_part: str | None = None
    role: Literal["self", "replaced", "inherited"]


class EffectiveObject(ContractModel):
    object_id: str
    canonical_object_id: str
    physical_identity: str
    source_level: Literal["master", "layout", "slide"]
    source_part: str
    source_object_id: str
    parent_group_path: str | None = None
    object_kind: str
    local_bbox: NormalizedBBox | None = None
    absolute_bbox: NormalizedBBox | None = None
    normalized_bbox: NormalizedBBox | None = None
    painted_bbox: NormalizedBBox | None = None
    painted_intersects_canvas: bool
    visibility_geometry_type: Literal["segment", "area"]
    rotation_deg: float
    z_index: int
    effective_z_index: int
    explicit_style: dict[str, Any]
    effective_style: dict[str, Any]
    render_visible: bool
    semantic_visible: bool
    visibility_reason: str | None = None
    off_canvas: bool
    off_canvas_class: str | None = None
    clipped: bool
    inherited: bool
    is_placeholder_override: bool
    replaces_object_id: str | None = None
    replaced: bool
    replaced_by_object_id: str | None = None
    resolution_trace: list[InheritanceStep]
    inheritance_trace: list[InheritanceStep]
    generation_content: bool
    has_text: bool
    raw_text: str | None = None
    content_role: str
    semantic_content_eligible: bool
    generation_content_eligible: bool
    style_evidence_eligible: bool
    style_evidence_scope: str
    style_capability_evidence: dict[str, Any] | None = None
    utility_placeholder: bool
    media_id: str | None = None
    placeholder_type: str | None = None
    text_capability: TextCapabilityModel | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    analyzer_object_id: str
    source_content: str | None = None
    content_origin: Literal["slide", "layout", "master", "unknown"] = "unknown"
    parser_content_hint: Literal[
        "slide_level_content",
        "placeholder_sample",
        "formatting_capability",
        "decorative_text",
        "field_value",
        "unknown",
    ] = "unknown"
    has_slide_level_content: bool = False


class EffectiveSceneInvariants(ContractModel):
    duplicate_physical_identity_count: int = 0
    replacement_chain_invalid_count: int = 0
    missing_source_trace_count: int = 0
    visible_without_geometry_count: int = 0


class EffectiveSlideScene(ContractModel):
    slide_id: str
    slide_index: int
    layout_id: str | None = None
    master_id: str | None = None
    canvas: dict[str, int]
    layers: dict[str, list[EffectiveObject]]
    effective_objects: list[EffectiveObject]
    invariants: EffectiveSceneInvariants


class ParserQa(ContractModel):
    duplicate_effective_leaf_count: int = 0
    text_bearing_objects_with_missing_rich_text: int = 0
    non_text_objects_with_observed_text_style: int = 0
    negative_geometry_extents: int = 0
    unresolved_deterministic_theme_aliases: int = 0
    broken_relationships: int = 0
    native_charts_without_chart_part: int = 0
    table_objects_without_model: int = 0
    chart_objects_without_model: int = 0
    table_grid_count_mismatch: int = 0
    duplicate_raw_object_id_count: int = 0
    duplicate_physical_identity_count: int = 0
    raw_object_count: int = 0
    tables_missing_linked_object: int = 0
    inherited_value_missing_trace: int = 0
    missing_physical_identity_count: int = 0
    visible_connector_inside_canvas_marked_off_canvas: int = 0
    painted_inside_canvas_render_invisible_without_reason: int = 0
    zero_width_connector_invalid: int = 0
    zero_height_connector_invalid: int = 0
    replacement_chain_invalid_count: int = 0
    missing_effective_source_trace_count: int = 0
    visible_without_geometry_count: int = 0
    passed: bool
    checks: dict[str, Any] = Field(default_factory=dict)


class ParseDiagnostics(ContractModel):
    status: Literal["success", "warning", "failed"]
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    issues: list[ParseIssue] = Field(default_factory=list)
    unsupported_elements: dict[str, int] = Field(default_factory=dict)
    counts_by_element_type: dict[str, int] = Field(default_factory=dict)
    qa: ParserQa


class PresentationModel(ContractModel):
    schema_version: str = "1.1.0"
    presentation_id: str
    template_id: str
    passed: bool
    source: SourceFileModel
    parser: ParserIdentity
    dimensions: PresentationDimensions
    theme: ThemeModel | None = None
    masters: list[MasterModel] = Field(default_factory=list)
    layouts: list[LayoutModel] = Field(default_factory=list)
    slides: list[SlideModel] = Field(default_factory=list)
    assets: list[AssetModel] = Field(default_factory=list)
    relationships: list[RelationshipModel] = Field(default_factory=list)
    tables: list[TableModel] = Field(default_factory=list)
    charts: list[ChartModel] = Field(default_factory=list)
    native_charts: list[ChartModel] = Field(default_factory=list)
    chart_styles: list[dict[str, Any]] = Field(default_factory=list)
    chart_palette: list[str] = Field(default_factory=list)
    notes: list[NotePartModel] = Field(default_factory=list)
    effective_text_runs: list[dict[str, Any]] = Field(default_factory=list)
    raw_scenes: list[RawSlideScene] = Field(default_factory=list)
    effective_scenes: list[EffectiveSlideScene] = Field(default_factory=list)
    embedded_objects: list[ShapeElementModel] = Field(default_factory=list)
    unknown_objects: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    color_usage_metrics: list[ColorUsageMetric] = Field(default_factory=list)
    usage_statistics: UsageStatistics
    summary: ObjectSummary
    diagnostics: ParseDiagnostics


ShapeElementModel.model_rebuild()
