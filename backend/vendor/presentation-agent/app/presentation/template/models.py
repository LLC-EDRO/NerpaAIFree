"""Strict TemplateModel contract inferred from a factual PresentationModel."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TemplateContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


EvidenceSource = Literal[
    "parser_data",
    "pptx_layout",
    "pptx_master",
    "pptx_theme",
    "statistical_pattern",
    "recurring_element",
    "semantic_vision",
]


class NormalizedGeometry(TemplateContractModel):
    x: float
    y: float
    width: float
    height: float


class SourceGeometry(TemplateContractModel):
    x_emu: int | None = None
    y_emu: int | None = None
    width_emu: int | None = None
    height_emu: int | None = None


class GeometryStatistics(TemplateContractModel):
    representative: NormalizedGeometry
    minimum: NormalizedGeometry
    maximum: NormalizedGeometry
    stddev: NormalizedGeometry
    source_examples: list[SourceGeometry] = Field(default_factory=list)


class RuleEvidence(TemplateContractModel):
    evidence_id: str
    source_type: EvidenceSource
    slide_ids: list[str] = Field(default_factory=list)
    element_ids: list[str] = Field(default_factory=list)
    layout_ids: list[str] = Field(default_factory=list)
    master_ids: list[str] = Field(default_factory=list)
    sample_count: int = 0
    support_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    variance: dict[str, float] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class ConfidenceScore(TemplateContractModel):
    score: float = Field(ge=0.0, le=1.0)
    sample_size: int = 0
    support_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    consistency: float = Field(default=0.0, ge=0.0, le=1.0)
    variance_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    conflict_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str


ElementRole = Literal[
    "title",
    "subtitle",
    "section_title",
    "body",
    "caption",
    "label",
    "quote",
    "metric",
    "image",
    "photo",
    "hero_image",
    "product_image",
    "illustration",
    "logo",
    "icon",
    "background_image",
    "decorative_image",
    "table",
    "chart",
    "footer",
    "header",
    "page_number",
    "decorative",
    "background",
    "container",
    "diagram",
    "unknown",
]


class ContentClass(StrEnum):
    TEMPLATE_INSTRUCTION = "template_instruction"
    SAMPLE_CONTENT = "sample_content"
    USER_CONTENT = "user_content"
    PLACEHOLDER_LABEL = "placeholder_label"
    DECORATIVE_TEXT = "decorative_text"
    BRAND_ELEMENT = "brand_element"
    UNKNOWN = "unknown"
    MIXED = "mixed"


class ReplacementPolicy(StrEnum):
    MUST_REPLACE = "must_replace"
    MUST_CLEAR = "must_clear"
    MUST_REPLACE_OR_CLEAR = "must_replace_or_clear"
    OPTIONAL_REPLACE = "optional_replace"
    PRESERVE = "preserve"
    PROTECTED = "protected"


class TemplateAnalysisContext(TemplateContractModel):
    source_presentation_role: Literal[
        "template", "example_presentation", "existing_user_presentation", "unknown"
    ] = "unknown"
    target_usage_mode: Literal[
        "clone_and_replace", "compose_from_slots", "generic_template_analysis"
    ] = "generic_template_analysis"
    # None permits generic reuse analysis; {} explicitly keeps every element.
    # Keys are source slide IDs, values are permitted parser element IDs.
    editable_elements: dict[str, list[str]] | None = None
    target_topic: str | None = None
    user_request_summary: str | None = None
    edit_scope_clarification: str | None = None
    preserve_original_subject_content: bool = False
    placeholder_output_allowed: bool = False
    brand_replacement_allowed: bool = False
    semantic_mode: Literal["off", "auto", "required"] = "auto"
    vision_review_mode: Literal["off", "risk_based", "all_slides"] = "risk_based"


class TextFragmentAssessment(TemplateContractModel):
    fragment_id: str
    element_id: str
    paragraph_index: int = Field(ge=0)
    run_indices: list[int] = Field(default_factory=list)
    text: str
    content_class: ContentClass
    semantic_role_hint: ElementRole | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    replacement_policy_hint: ReplacementPolicy | None = None
    warnings: list[str] = Field(default_factory=list)


class VisibleElementVisionReview(TemplateContractModel):
    element_id: str
    actually_visible: bool
    visibility: Literal["visible", "partial", "occluded", "hidden", "unknown"] = "unknown"
    visual_role: ElementRole | None = None
    content_class: ContentClass | None = None
    is_payload: bool = False
    is_container: bool = False
    is_decorative: bool = False
    component_candidate_id: str | None = None
    residue_if_unresolved: Literal["none", "low", "medium", "high", "critical"] = "none"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_summary: str = ""
    warnings: list[str] = Field(default_factory=list)


class TemplateResidueAssessment(TemplateContractModel):
    element_id: str
    content_class: ContentClass
    current_content_summary: str | None = None
    visible_if_unresolved: bool = True
    subject_specific: bool = False
    instruction_visible: bool = False
    placeholder_visible: bool = False
    sample_content_visible: bool = False
    residue_severity: Literal["none", "low", "medium", "high", "critical"] = "none"
    safe_to_leave: bool
    required_resolution: bool
    reason: str


class SemanticDecisionConflict(TemplateContractModel):
    element_id: str
    deterministic_decision: dict[str, Any]
    semantic_decision: dict[str, Any] | None = None
    vision_decision: dict[str, Any] | None = None
    resolved_decision: dict[str, Any]
    resolution_rule: str
    confidence: float = Field(ge=0.0, le=1.0)
    warning: str


class ElementSemanticDecision(TemplateContractModel):
    element_id: str
    source_object_id: str
    source_slide_id: str
    source_slide_index: int = Field(ge=0)
    source_part_uri: str | None = None
    openxml_shape_id: int | None = None
    source_level: Literal["master", "layout", "slide"] = "slide"
    raw_text: str | None = None
    visible: bool = True
    visibility_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    semantic_role: ElementRole
    content_class: ContentClass
    text_fragments: list[TextFragmentAssessment] = Field(default_factory=list)
    replacement_policy: ReplacementPolicy
    must_replace_or_clear: bool = False
    requires_resolution: bool = False
    safe_to_preserve: bool = False
    user_keep: bool = False
    replaceable: bool = False
    clearable: bool = False
    cloneable: bool = True
    protected: bool = False
    group_id: str | None = None
    component_id: str | None = None
    slot_id: str | None = None
    slot_occurrence_id: str | None = None
    payload_kind: Literal["text", "image", "table", "chart", "metric", "none", "unknown"] = "unknown"
    payload_owner: Literal["element", "component", "slot", "none"] = "element"
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    deterministic_assessment: dict[str, Any] = Field(default_factory=dict)
    semantic_assessment: dict[str, Any] | None = None
    vision_review: VisibleElementVisionReview | None = None
    residue_risk: TemplateResidueAssessment
    warnings: list[str] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def enforce_policy_invariants(self) -> ElementSemanticDecision:
        mandatory = self.replacement_policy in {
            ReplacementPolicy.MUST_REPLACE,
            ReplacementPolicy.MUST_CLEAR,
            ReplacementPolicy.MUST_REPLACE_OR_CLEAR,
        }
        replaceable = self.replacement_policy in {
            ReplacementPolicy.MUST_REPLACE,
            ReplacementPolicy.MUST_REPLACE_OR_CLEAR,
            ReplacementPolicy.OPTIONAL_REPLACE,
        }
        clearable = self.replacement_policy in {
            ReplacementPolicy.MUST_CLEAR,
            ReplacementPolicy.MUST_REPLACE_OR_CLEAR,
            ReplacementPolicy.OPTIONAL_REPLACE,
        }
        protected = self.replacement_policy == ReplacementPolicy.PROTECTED
        if self.requires_resolution != mandatory or self.must_replace_or_clear != mandatory:
            raise ValueError("requires_resolution and must_replace_or_clear must derive from replacement_policy")
        if self.replaceable != replaceable or self.clearable != clearable or self.protected != protected:
            raise ValueError("replaceable, clearable, and protected must derive from replacement_policy")
        unsafe_classes = {
            ContentClass.TEMPLATE_INSTRUCTION,
            ContentClass.SAMPLE_CONTENT,
            ContentClass.PLACEHOLDER_LABEL,
            ContentClass.MIXED,
        }
        if self.user_keep and self.replacement_policy != ReplacementPolicy.PROTECTED:
            raise ValueError("user_keep requires a protected policy")
        if not self.user_keep and self.content_class in unsafe_classes and self.safe_to_preserve:
            raise ValueError(f"{self.content_class.value} cannot be safe_to_preserve")
        if not self.user_keep and self.content_class in unsafe_classes and self.replacement_policy in {
            ReplacementPolicy.PRESERVE,
            ReplacementPolicy.PROTECTED,
        }:
            raise ValueError(f"{self.content_class.value} cannot have a preserve/protected policy")
        if protected and self.replaceable:
            raise ValueError("protected elements cannot be replaceable")
        return self


ReplacePolicy = Literal["never", "replace_text", "replace_image", "replace_content", "manual_review"]
AllowedReplaceOperation = Literal["replace_text", "replace_image"]


class ElementReplacePolicy(TemplateContractModel):
    """Explicit, conservative mutation policy for one factual source element.

    The field is nested under ``RoleAssignment`` and optional there so stored
    TemplateModel v1 payloads remain readable.  Absence is intentionally
    interpreted by downstream consumers as ``never``.
    """

    source_element_id: str
    replaceable: bool = False
    replace_policy: ReplacePolicy = "never"
    allowed_operations: list[AllowedReplaceOperation] = Field(default_factory=list)
    semantic_role: ElementRole
    role_confidence: float = Field(ge=0.0, le=1.0)
    policy_confidence: float = Field(ge=0.0, le=1.0)
    source_element_fingerprint: str | None = None
    evidence: list[RuleEvidence] = Field(default_factory=list)
    reason: str


ContentKind = Literal["text", "image", "table", "chart", "metric", "mixed", "unknown"]


class TextSlotCapacity(TemplateContractModel):
    preferred_chars: int | None = Field(default=None, ge=0)
    max_chars: int | None = Field(default=None, ge=0)
    preferred_lines: int | None = Field(default=None, ge=0)
    max_lines: int | None = Field(default=None, ge=0)
    min_font_size: float | None = Field(default=None, gt=0)
    max_font_size: float | None = Field(default=None, gt=0)
    preferred_bullets: int | None = Field(default=None, ge=0)
    max_bullets: int | None = Field(default=None, ge=0)
    preferred_words_per_bullet: int | None = Field(default=None, ge=0)
    max_words_per_bullet: int | None = Field(default=None, ge=0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    measurement_source: str = "observed_source_content"


class ImageSlotCapacity(TemplateContractModel):
    min_count: int = Field(default=0, ge=0)
    max_count: int = Field(default=1, ge=0)
    preferred_aspect_ratio: float | None = Field(default=None, gt=0)
    aspect_tolerance: float = Field(default=0.35, ge=0.0, le=1.0)
    min_resolution: tuple[int, int] | None = None
    fit_modes: list[Literal["contain", "cover", "stretch", "preserve_source"]] = Field(
        default_factory=lambda: ["preserve_source"]
    )
    mask_frame_information: dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    measurement_source: str = "source_geometry"


class TableSlotCapacity(TemplateContractModel):
    max_rows: int | None = Field(default=None, ge=0)
    max_columns: int | None = Field(default=None, ge=0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    measurement_source: str = "source_table"


class UnfilledSlotResidueAssessment(TemplateContractModel):
    slot_id: str
    occurrence_id: str
    visible_residue: bool
    instruction_residue: bool = False
    sample_content_residue: bool = False
    empty_frame_residue: bool = False
    old_subject_residue: bool = False
    severity: Literal["none", "low", "medium", "high", "critical"] = "none"
    safe_unfilled: bool
    required_action: Literal[
        "none", "replace", "clear", "clear_or_replace", "use_layout_variant", "manual_review"
    ] = "none"


class SemanticSlotOccurrence(TemplateContractModel):
    """One factual realization of a semantic slot on an exact source slide.

    ``primary_payload_element_id`` is the only element that may become a patch
    target.  Container, frame, overlay, inherited, and otherwise non-payload
    members stay explicit and protected.
    """

    occurrence_id: str
    slot_id: str
    source_slide_id: str
    source_slide_index: int = Field(ge=0)
    source_slide_part_uri: str | None = None
    source_level: Literal["master", "layout", "slide"] = "slide"
    source_open_xml_shape_id: int | None = None
    source_element_fingerprint: str | None = None
    semantic_role: ElementRole | None = None
    content_kind: ContentKind = "unknown"
    payload_element_ids: list[str] = Field(default_factory=list)
    supporting_element_ids: list[str] = Field(default_factory=list)
    protected_element_ids: list[str] = Field(default_factory=list)
    primary_payload_element_id: str | None = None
    placeholder_id: str | None = None
    relationship_id: str | None = None
    geometry: NormalizedGeometry | None = None
    physical_aspect_ratio: float | None = Field(default=None, gt=0)
    role_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    replace_policy: ReplacePolicy = "never"
    allowed_operations: list[AllowedReplaceOperation] = Field(default_factory=list)
    policy_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    replaceable: bool = False
    allowed_asset_types: list[
        Literal["existing_image", "icon", "logo", "diagram_reference"]
    ] = Field(default_factory=list)
    evidence: list[RuleEvidence] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    component_id: str | None = None
    secondary_payload_element_ids: list[str] = Field(default_factory=list)
    source_layout_id: str | None = None
    source_master_id: str | None = None
    current_content_class: ContentClass = ContentClass.UNKNOWN
    current_replacement_policy: ReplacementPolicy = ReplacementPolicy.PROTECTED
    capacity_override: dict[str, Any] | None = None
    visibility: Literal["visible", "partial", "hidden", "unknown"] = "visible"
    residue_assessment: UnfilledSlotResidueAssessment | None = None


class RoleAssignment(TemplateContractModel):
    parser_element_id: str
    source_object_id: str
    parser_type: str
    parent_group_path: str | None = None
    source_level: Literal["master", "layout", "slide"] | None = None
    source_part: str | None = None
    source_open_xml_shape_id: int | None = None
    source_element_fingerprint: str | None = None
    source_relationship_id: str | None = None
    role: ElementRole
    normalized_geometry: NormalizedGeometry | None = None
    source_geometry: SourceGeometry | None = None
    layout_slot_id: str | None = None
    slot_occurrence_id: str | None = None
    slot_member_role: Literal["primary_payload", "supporting", "protected"] = "protected"
    typography_ref: str | None = None
    color_refs: list[str] = Field(default_factory=list)
    recurring_pattern_id: str | None = None
    confidence: ConfidenceScore
    evidence: list[RuleEvidence] = Field(default_factory=list)
    replace_policy: ElementReplacePolicy | None = None
    semantic_decision_id: str | None = None
    content_class: ContentClass = ContentClass.UNKNOWN
    replacement_policy: ReplacementPolicy = ReplacementPolicy.PROTECTED
    safe_to_preserve: bool = True
    requires_resolution: bool = False
    component_id: str | None = None


class StructuralSignature(TemplateContractModel):
    source_layout_id: str | None = None
    source_master_id: str | None = None
    background_key: str | None = None
    counts_by_type: dict[str, int] = Field(default_factory=dict)
    role_counts: dict[str, int] = Field(default_factory=dict)
    placeholder_types: list[str] = Field(default_factory=list)
    geometry_fingerprint: list[dict[str, Any]] = Field(default_factory=list)
    archetype_counts: dict[str, int] = Field(default_factory=dict)
    signature_hash: str


class TypographyToken(TemplateContractModel):
    token_id: str
    possible_role: ElementRole | None = None
    font_family: str | None = None
    median_font_size_pt: float | None = None
    min_font_size_pt: float | None = None
    max_font_size_pt: float | None = None
    font_weight: int | None = None
    bold: bool | None = None
    italic: bool | None = None
    underline: str | None = None
    capitalization: str | None = None
    color: str | None = None
    theme_color_ref: str | None = None
    alignment: str | None = None
    line_spacing: float | None = None
    paragraph_space_before: float | None = None
    paragraph_space_after: float | None = None
    character_spacing: float | None = None
    usage_count: int
    slide_ids: list[str]
    element_ids: list[str]
    confidence: ConfidenceScore
    evidence: list[RuleEvidence]


class ColorToken(TemplateContractModel):
    token_id: str
    color: str
    theme_color_ref: str | None = None
    declared_in_theme: bool = False
    usage_count: int = 0
    usage_contexts: list[str] = Field(default_factory=list)
    slide_ids: list[str] = Field(default_factory=list)
    element_ids: list[str] = Field(default_factory=list)
    possible_role: str | None = None
    confidence: ConfidenceScore
    evidence: list[RuleEvidence]


class BackgroundStyle(TemplateContractModel):
    background_id: str
    color: str | None = None
    theme_color_ref: str | None = None
    source_levels: list[str] = Field(default_factory=list)
    slide_ids: list[str]
    support_ratio: float = Field(ge=0.0, le=1.0)
    full_slide_element_ids: list[str] = Field(default_factory=list)
    confidence: ConfidenceScore
    evidence: list[RuleEvidence]


class AlignmentGuide(TemplateContractModel):
    guide_id: str
    axis: Literal["x", "y"]
    kind: Literal["left", "right", "center", "top", "bottom", "baseline"]
    position: float
    slide_ids: list[str]
    element_ids: list[str]
    confidence: ConfidenceScore
    evidence: list[RuleEvidence]


class SpacingRule(TemplateContractModel):
    spacing_id: str
    axis: Literal["horizontal", "vertical", "margin"]
    value: float
    relationship: str
    slide_ids: list[str]
    element_ids: list[str]
    confidence: ConfidenceScore
    evidence: list[RuleEvidence]


class ImagePattern(TemplateContractModel):
    image_pattern_id: str
    geometry: GeometryStatistics
    aspect_ratio_median: float
    crop_mode: Literal["crop", "uncropped", "unknown"]
    asset_hashes: list[str] = Field(default_factory=list)
    slide_ids: list[str]
    element_ids: list[str]
    related_layout_pattern_ids: list[str] = Field(default_factory=list)
    support_ratio: float = Field(ge=0.0, le=1.0)
    confidence: ConfidenceScore
    evidence: list[RuleEvidence]


class TablePattern(TemplateContractModel):
    table_pattern_id: str
    table_style_id: str | None = None
    geometry: GeometryStatistics | None = None
    header_style: dict[str, Any] | None = None
    body_style: dict[str, Any] | None = None
    banded_rows: bool = False
    border_color: str | None = None
    border_width_pt: float | None = None
    common_cell_margins_emu: dict[str, int | None] = Field(default_factory=dict)
    vertical_alignments: list[str] = Field(default_factory=list)
    slide_ids: list[str]
    element_ids: list[str]
    confidence: ConfidenceScore
    evidence: list[RuleEvidence]


class DecorativePattern(TemplateContractModel):
    decorative_pattern_id: str
    parser_type: str
    geometry: GeometryStatistics
    fill: str | None = None
    line: str | None = None
    slide_ids: list[str]
    element_ids: list[str]
    confidence: ConfidenceScore
    evidence: list[RuleEvidence]


class SlotRule(TemplateContractModel):
    slot_id: str
    role: ElementRole
    content_kind: ContentKind = "unknown"
    slot_kind: Literal["text", "visual", "table", "chart", "metric", "mixed", "unknown"] = "unknown"
    allowed_element_types: list[str]
    min_count: int
    max_count: int
    required: bool
    repeatable: bool = False
    replaceable: bool = False
    replace_policy: ReplacePolicy = "never"
    allowed_operations: list[AllowedReplaceOperation] = Field(default_factory=list)
    allowed_asset_types: list[
        Literal["existing_image", "icon", "logo", "diagram_reference"]
    ] = Field(default_factory=list)
    preferred_aspect_ratio: float | None = Field(default=None, gt=0.0)
    aspect_ratio_tolerance: float = Field(default=0.35, ge=0.0, le=1.0)
    min_resolution: tuple[int, int] | None = None
    transparency_preference: Literal["required", "preferred", "irrelevant"] = "irrelevant"
    source_policy: Literal[
        "user_provided", "template_asset", "user_or_brand_asset", "external_required"
    ] = "user_or_brand_asset"
    geometry: GeometryStatistics
    typography_refs: list[str] = Field(default_factory=list)
    color_refs: list[str] = Field(default_factory=list)
    image_pattern_ref: str | None = None
    text_rules: dict[str, Any] = Field(default_factory=dict)
    image_rules: dict[str, Any] = Field(default_factory=dict)
    confidence: ConfidenceScore
    evidence: list[RuleEvidence]
    occurrences: list[SemanticSlotOccurrence] = Field(default_factory=list)
    layout_family_id: str | None = None
    layout_variant_id: str | None = None
    semantic_role: ElementRole | None = None
    template_requiredness: Literal["required", "optional", "conditional"] = "optional"
    requiredness_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requiredness_evidence: list[RuleEvidence] = Field(default_factory=list)
    requiredness_condition: str | None = None
    allowed_content_types: list[ContentKind] = Field(default_factory=list)
    min_items: int = Field(default=0, ge=0)
    max_items: int = Field(default=1, ge=0)
    capacity: TextSlotCapacity | ImageSlotCapacity | TableSlotCapacity | dict[str, Any] | None = None
    replacement_policy: ReplacementPolicy = ReplacementPolicy.PROTECTED
    clearable: bool = False
    cloneable: bool = True
    empty_state_policy: Literal[
        "safe_empty",
        "clear_payload",
        "hide_payload",
        "preserve_frame",
        "collapse_component",
        "use_layout_variant",
        "not_allowed",
        "manual_review",
    ] = "manual_review"
    empty_state_residue: UnfilledSlotResidueAssessment | None = None
    component_id: str | None = None
    payload_roles: list[ElementRole] = Field(default_factory=list)
    generation_eligibility: Literal["safe", "conditional", "exemplar_only", "unsafe"] = "conditional"
    warnings: list[str] = Field(default_factory=list)


# Public semantic name for the strengthened visual subset of SlotRule.  It is
# intentionally an alias, not a second hierarchy that could drift from the
# canonical TemplateModel slot contract.
VisualSlotDefinition = SlotRule
TemplateSlotDefinition = SlotRule
TemplateSlotOccurrence = SemanticSlotOccurrence


class VisualComponentModel(TemplateContractModel):
    component_id: str
    source_slide_id: str
    component_type: Literal[
        "image_with_caption",
        "image_placeholder_frame",
        "content_card",
        "metric_block",
        "bullet_item",
        "icon_with_label",
        "title_block",
        "quote_block",
        "logo_lockup",
        "header_group",
        "footer_group",
        "chart_with_caption",
        "table_with_title",
        "generic_component",
    ]
    member_element_ids: list[str]
    payload_element_ids: list[str] = Field(default_factory=list)
    supporting_element_ids: list[str] = Field(default_factory=list)
    container_element_ids: list[str] = Field(default_factory=list)
    protected_element_ids: list[str] = Field(default_factory=list)
    primary_payload_element_id: str | None = None
    secondary_payload_element_ids: list[str] = Field(default_factory=list)
    semantic_role: ElementRole = "unknown"
    content_class: ContentClass = ContentClass.UNKNOWN
    replacement_policy: ReplacementPolicy = ReplacementPolicy.PROTECTED
    geometry: NormalizedGeometry | None = None
    layout_relationships: list[str] = Field(default_factory=list)
    group_source: Literal[
        "ooxml_group", "inferred_visual_group", "layout_component", "recurring_component"
    ] = "inferred_visual_group"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    vision_review: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class RecurringElementPattern(TemplateContractModel):
    recurring_pattern_id: str
    element_type: str
    possible_role: ElementRole | None = None
    slide_ids: list[str]
    element_ids: list[str]
    geometry: GeometryStatistics
    style_references: list[str] = Field(default_factory=list)
    asset_references: list[str] = Field(default_factory=list)
    asset_hashes: list[str] = Field(default_factory=list)
    support_ratio: float = Field(ge=0.0, le=1.0)
    style_consistency: float = Field(ge=0.0, le=1.0)
    confidence: ConfidenceScore
    evidence: list[RuleEvidence]


class LayoutPattern(TemplateContractModel):
    layout_pattern_id: str
    member_slide_ids: list[str]
    representative_slide_ids: list[str]
    outlier_slide_ids: list[str] = Field(default_factory=list)
    source_layout_ids: list[str] = Field(default_factory=list)
    source_master_ids: list[str] = Field(default_factory=list)
    structural_signature: StructuralSignature
    semantic_type: str | None = None
    semantic_description: str | None = None
    semantic_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    semantic_source: str | None = None
    slots: list[SlotRule] = Field(default_factory=list)
    alignment_guides: list[AlignmentGuide] = Field(default_factory=list)
    spacing_rules: list[SpacingRule] = Field(default_factory=list)
    background_style_ref: str | None = None
    typography_refs: list[str] = Field(default_factory=list)
    color_refs: list[str] = Field(default_factory=list)
    image_pattern_refs: list[str] = Field(default_factory=list)
    recurring_element_refs: list[str] = Field(default_factory=list)
    evidence_class: Literal["single_observation", "repeated_observation"] = "single_observation"
    confidence: ConfidenceScore
    evidence: list[RuleEvidence]
    warnings: list[str] = Field(default_factory=list)
    family_id: str | None = None
    variant_id: str | None = None
    generation_eligibility: Literal["safe", "conditional", "exemplar_only", "unsafe"] = "conditional"
    generation_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    auto_selectable: bool = False


class GenerationEligibility(TemplateContractModel):
    status: Literal["safe", "conditional", "exemplar_only", "unsafe"]
    generation_confidence: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    required_manual_actions: list[str] = Field(default_factory=list)
    residue_risk: Literal["none", "low", "medium", "high", "critical"] = "none"
    slot_coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_strength: float = Field(default=0.0, ge=0.0, le=1.0)
    auto_selectable: bool = False


class LayoutVariantModel(TemplateContractModel):
    variant_id: str
    family_id: str
    member_slide_ids: list[str]
    representative_slide_ids: list[str]
    strongest_semantic_slide_id: str | None = None
    safest_generation_slide_id: str | None = None
    variant_differences: list[str] = Field(default_factory=list)
    exact_slots: list[str] = Field(default_factory=list)
    source_layout_ids: list[str] = Field(default_factory=list)
    source_master_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    reuse_status: Literal["reusable", "conditional", "exemplar_only", "blocked"] = "conditional"
    generation_eligibility: GenerationEligibility


class LayoutFamilyModel(TemplateContractModel):
    family_id: str
    semantic_role: str
    member_variant_ids: list[str]
    member_slide_ids: list[str]
    canonical_slots: list[str] = Field(default_factory=list)
    common_component_structure: list[str] = Field(default_factory=list)
    common_alignment: list[str] = Field(default_factory=list)
    common_style_refs: list[str] = Field(default_factory=list)
    medoid_slide_id: str | None = None
    strongest_semantic_slide_id: str | None = None
    safest_generation_slide_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    generation_eligibility: GenerationEligibility


class SlotCoverageReport(TemplateContractModel):
    source_slide_id: str
    visible_elements: int = Field(ge=0)
    elements_with_decision: int = Field(ge=0)
    slot_covered_elements: int = Field(ge=0)
    component_covered_elements: int = Field(ge=0)
    protected_elements: int = Field(ge=0)
    unsafe_uncovered_elements: list[str] = Field(default_factory=list)
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    complete: bool
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TemplateReadinessReport(TemplateContractModel):
    status: Literal["ready", "partial", "blocked"] = "blocked"
    ready_for_planner: bool = False
    ready_for_clone_and_replace: bool = False
    ready_for_compose_from_slots: bool = False
    semantic_coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    slot_coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    visible_elements_without_policy: list[str] = Field(default_factory=list)
    unsafe_preserve_elements: list[str] = Field(default_factory=list)
    unresolved_template_instructions: list[str] = Field(default_factory=list)
    unresolved_sample_content: list[str] = Field(default_factory=list)
    unresolved_placeholder_labels: list[str] = Field(default_factory=list)
    low_confidence_payload_targets: list[str] = Field(default_factory=list)
    ambiguous_components: list[str] = Field(default_factory=list)
    unsafe_layout_patterns: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class TemplateSuitabilityReport(TemplateContractModel):
    task_context_hash: str | None = None
    presentation_type: str | None = None
    target_topic: str | None = None
    overall_status: Literal[
        "suitable", "conditionally_suitable", "not_suitable", "unknown_without_task_context"
    ] = "unknown_without_task_context"
    overall_score: float = Field(default=0.0, ge=0.0, le=1.0)
    safe_clone_slide_ids: list[str] = Field(default_factory=list)
    conditional_slide_ids: list[str] = Field(default_factory=list)
    blocked_slide_ids: list[str] = Field(default_factory=list)
    suitable_layout_family_ids: list[str] = Field(default_factory=list)
    missing_layout_capabilities: list[str] = Field(default_factory=list)
    old_subject_residue_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    manual_replacement_load: Literal["none", "low", "medium", "high"] = "none"
    required_asset_load: int = Field(default=0, ge=0)
    instruction_cleanup_load: int = Field(default=0, ge=0)
    slides_requiring_assets: list[str] = Field(default_factory=list)
    slides_requiring_manual_review: list[str] = Field(default_factory=list)
    slides_with_unresolved_subject_content: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)


class SlideTemplateAssignment(TemplateContractModel):
    slide_id: str
    slide_index: int
    layout_pattern_id: str
    cluster_distance: float = Field(ge=0.0)
    outlier_score: float = Field(ge=0.0, le=1.0)
    is_outlier: bool
    assignment_basis: Literal["exact_representative", "structural_match"]
    source_layout_id: str | None = None
    source_master_id: str | None = None
    alternative_layout_pattern_id: str | None = None
    alternative_distance: float | None = Field(default=None, ge=0.0)
    separation_margin: float | None = Field(default=None, ge=0.0)
    role_assignments: list[RoleAssignment] = Field(default_factory=list)
    confidence: ConfidenceScore
    warnings: list[str] = Field(default_factory=list)


class MasterLayoutThemeLink(TemplateContractModel):
    layout_id: str
    master_id: str | None = None
    theme_id: str | None = None
    slide_ids: list[str] = Field(default_factory=list)


class GlobalRule(TemplateContractModel):
    rule_id: str
    category: str
    name: str
    value: Any
    confidence: ConfidenceScore
    evidence: list[RuleEvidence]


class DesignSystem(TemplateContractModel):
    typography: list[TypographyToken] = Field(default_factory=list)
    colors: list[ColorToken] = Field(default_factory=list)
    backgrounds: list[BackgroundStyle] = Field(default_factory=list)
    alignment_guides: list[AlignmentGuide] = Field(default_factory=list)
    spacing_rules: list[SpacingRule] = Field(default_factory=list)
    images: list[ImagePattern] = Field(default_factory=list)
    tables: list[TablePattern] = Field(default_factory=list)
    decorative_patterns: list[DecorativePattern] = Field(default_factory=list)


class TemplateIssue(TemplateContractModel):
    code: str
    message: str
    severity: Literal["warning", "error"] = "warning"
    slide_id: str | None = None
    element_id: str | None = None
    rule_id: str | None = None


class TemplateDiagnostics(TemplateContractModel):
    status: Literal["success", "warning", "partial", "blocked", "failed"]
    issues: list[TemplateIssue] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    semantic_mode: Literal["off", "auto", "required"]
    semantic_provider: str | None = None
    semantic_model: str | None = None


class ConfidenceSummary(TemplateContractModel):
    overall: float = Field(ge=0.0, le=1.0)
    high_confidence_rules: int = 0
    medium_confidence_rules: int = 0
    low_confidence_rules: int = 0
    average_layout_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    average_pattern_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    average_assignment_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    average_role_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class EditScopeReadiness(TemplateContractModel):
    status: Literal["not_requested", "resolved", "waiting_for_input"] = "not_requested"
    code: str | None = None
    question: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class TemplateModel(TemplateContractModel):
    edit_scope_readiness: EditScopeReadiness = Field(default_factory=EditScopeReadiness)
    schema_version: str = "2.0.0"
    analyzer_version: str = "2.0.0"
    visual_slot_contract_version: str = "1.0.0"
    semantic_classifier_version: str = "1.0.0"
    replacement_policy_version: str = "1.0.0"
    vision_prompt_version: str = "1.0.0"
    component_analyzer_version: str = "1.0.0"
    layout_normalization_version: str = "1.0.0"
    readiness_rules_version: str = "1.0.0"
    source_presentation_id: str
    source_parser_schema_version: str
    source_parser_execution_id: str | None = None
    slide_dimensions: dict[str, int | float]
    design_system: DesignSystem
    layout_patterns: list[LayoutPattern]
    slide_assignments: list[SlideTemplateAssignment]
    recurring_elements: list[RecurringElementPattern]
    master_layout_theme_map: list[MasterLayoutThemeLink]
    global_rules: list[GlobalRule]
    diagnostics: TemplateDiagnostics
    confidence_summary: ConfidenceSummary
    analysis_context: TemplateAnalysisContext = Field(default_factory=TemplateAnalysisContext)
    element_semantics: list[ElementSemanticDecision] = Field(default_factory=list)
    visual_components: list[VisualComponentModel] = Field(default_factory=list)
    slot_coverage: list[SlotCoverageReport] = Field(default_factory=list)
    layout_families: list[LayoutFamilyModel] = Field(default_factory=list)
    layout_variants: list[LayoutVariantModel] = Field(default_factory=list)
    semantic_conflicts: list[SemanticDecisionConflict] = Field(default_factory=list)
    readiness: TemplateReadinessReport = Field(default_factory=TemplateReadinessReport)
    suitability: TemplateSuitabilityReport = Field(default_factory=TemplateSuitabilityReport)
    requires_template_reanalysis: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def derive_physical_image_aspects(self) -> TemplateModel:
        width = self.slide_dimensions.get("width_emu", 0)
        height = self.slide_dimensions.get("height_emu", 0)
        if not width or not height:
            return self
        source = {(a.slide_id, key): r.source_geometry for a in self.slide_assignments
                  for r in a.role_assignments for key in (r.source_object_id, r.parser_element_id)}
        for pattern in self.layout_patterns:
            for slot in pattern.slots:
                for occurrence in slot.occurrences:
                    geometry = source.get((occurrence.source_slide_id, occurrence.primary_payload_element_id))
                    if geometry and geometry.width_emu and geometry.height_emu:
                        occurrence.physical_aspect_ratio = round(geometry.width_emu / geometry.height_emu, 6)
                    elif occurrence.geometry and occurrence.geometry.width > 0 and occurrence.geometry.height > 0:
                        occurrence.physical_aspect_ratio = round(occurrence.geometry.width * width / (occurrence.geometry.height * height), 6)
        return self

    @model_validator(mode="after")
    def mark_legacy_semantics(self) -> TemplateModel:
        if not self.element_semantics or not self.layout_families or not self.layout_variants:
            self.requires_template_reanalysis = True
            self.metadata.setdefault("legacy_template_semantics", True)
        return self
