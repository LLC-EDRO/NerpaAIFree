"""Deterministic template semantics, safety policy, and readiness analysis.

The parser supplies source facts.  This module owns the semantic decision that
turns those facts into reuse policy.  It deliberately remains deterministic;
optional LLM and vision assessments can be fused later without changing source
IDs, geometry, or OOXML relationships.
"""

from __future__ import annotations

import re
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from app.presentation.models import PresentationModel
from app.presentation.template.features import stable_hash
from app.presentation.template.models import (
    ContentClass,
    ElementSemanticDecision,
    GenerationEligibility,
    ImageSlotCapacity,
    LayoutFamilyModel,
    LayoutPattern,
    LayoutVariantModel,
    ReplacementPolicy,
    SemanticDecisionConflict,
    SemanticSlotOccurrence,
    SlideTemplateAssignment,
    SlotCoverageReport,
    TableSlotCapacity,
    TemplateAnalysisContext,
    TemplateReadinessReport,
    TemplateResidueAssessment,
    TemplateSuitabilityReport,
    TextFragmentAssessment,
    TextSlotCapacity,
    UnfilledSlotResidueAssessment,
    VisualComponentModel,
)
from app.presentation.template.observations import ElementObservation, normalized_text

INSTRUCTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:опишите|укажите|добавьте|вставьте|замените|напишите|расскажите|перечислите|выделите)\b",
        r"\b(?:краткая|кратко)\s+(?:информация|опишите|расскажите)\b",
        r"\b(?:description|describe|insert|add|replace|enter|write|summarize|explain|list)\b",
        r"\b(?:information|details)\s+(?:about|on)\b",
        r"\b(?:click|tap)\s+to\s+(?:add|edit)\b",
    )
]
PLACEHOLDER_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^(?:изображение(?:/фотография)?|фото|картинка|название|заголовок|подзаголовок|дата|место|ваш текст|текст слайда|текст заголовка|основной докладчик)$",
        r"^(?:image(?:/photo)?|photo|picture|title|subtitle|date|location|venue|your text|speaker|presenter)$",
        r"^[\[<{(].+[\]>})]$",
    )
]
SAMPLE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\blorem\s+ipsum\b",
        r"\b(?:dolor\s+sit\s+amet|consectetur\s+adipiscing)\b",
        r"\b(?:sample|example|demo|dummy)\b",
        r"\b(?:пример(?:а|ом|у|е|ы)?|образец|демо(?:нстрац(?:ия|ионный))?)\b",
        r"\b(?:john\s+doe|jane\s+doe|ivan\s+ivanov|иван\s+иванов)\b",
    )
]
GENERIC_HEADING_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^(?:обоснование\s+значимости|основные\s+преимущества|ключевые\s+выводы)$",
        r"^(?:key\s+benefits|key\s+takeaways|why\s+it\s+matters)$",
    )
]


@dataclass(frozen=True, slots=True)
class TemplateSemanticsConfig:
    design_only: bool = False
    content_class_min_confidence: float = 0.62
    policy_min_confidence: float = 0.70
    vision_conflict_threshold: float = 0.18
    unknown_content_policy: ReplacementPolicy = ReplacementPolicy.MUST_REPLACE_OR_CLEAR
    unknown_non_content_policy: ReplacementPolicy = ReplacementPolicy.PROTECTED
    min_generation_confidence: float = 0.72
    singleton_auto_use: bool = False
    layout_family_merge_threshold: float = 0.78
    layout_variant_threshold: float = 0.24


@dataclass(slots=True)
class TemplateSemanticsResult:
    decisions: list[ElementSemanticDecision]
    components: list[VisualComponentModel]
    coverage: list[SlotCoverageReport]
    families: list[LayoutFamilyModel]
    variants: list[LayoutVariantModel]
    readiness: TemplateReadinessReport
    suitability: TemplateSuitabilityReport
    conflicts: list[SemanticDecisionConflict]
    metrics: dict[str, int | float]


class TemplateSemanticsEngine:
    """Attach authoritative semantics to the existing TemplateModel topology."""

    def __init__(self, config: TemplateSemanticsConfig | None = None) -> None:
        self.config = config or TemplateSemanticsConfig()

    def analyze(
        self,
        *,
        presentation: PresentationModel,
        observations_by_slide: dict[str, list[ElementObservation]],
        layout_patterns: list[LayoutPattern],
        assignments: list[SlideTemplateAssignment],
        context: TemplateAnalysisContext,
    ) -> TemplateSemanticsResult:
        observations = [
            item
            for slide_id in sorted(observations_by_slide)
            for item in observations_by_slide[slide_id]
        ]
        repeats = Counter(
            normalized_text(item.text)
            for item in observations
            if item.text and normalized_text(item.text)
        )
        decisions = [self._decision(item, repeats, len(presentation.slides), context) for item in observations]
        decisions_by_key = self._decision_index(decisions)
        group_started = time.perf_counter()
        components = self._components(observations_by_slide, decisions_by_key)
        component_by_member = {
            (component.source_slide_id, element_id): component
            for component in components
            for element_id in component.member_element_ids
        }
        for decision in decisions:
            component = component_by_member.get((decision.source_slide_id, decision.element_id))
            if component:
                decision.component_id = component.component_id
                decision.payload_owner = "component"
        group_analysis_duration_ms = round((time.perf_counter() - group_started) * 1000, 3)

        self._enrich_assignments(assignments, decisions_by_key)
        self._enrich_slots(layout_patterns, decisions_by_key, component_by_member)
        coverage = self._coverage(observations_by_slide, decisions_by_key, component_by_member)
        layout_started = time.perf_counter()
        families, variants = self._layout_hierarchy(layout_patterns, assignments, coverage, decisions)
        layout_normalization_duration_ms = round((time.perf_counter() - layout_started) * 1000, 3)
        readiness_started = time.perf_counter()
        readiness = self._readiness(decisions, components, coverage, layout_patterns)
        suitability = TemplateSuitabilityReport() if self.config.design_only else self._suitability(context, assignments, decisions, families, readiness)
        readiness_duration_ms = round((time.perf_counter() - readiness_started) * 1000, 3)
        metrics = self._metrics(decisions, components, coverage, families, variants, readiness, suitability)
        metrics.update(
            group_analysis_duration_ms=group_analysis_duration_ms,
            layout_normalization_duration_ms=layout_normalization_duration_ms,
            readiness_duration_ms=readiness_duration_ms,
        )
        return TemplateSemanticsResult(
            decisions=decisions,
            components=components,
            coverage=coverage,
            families=families,
            variants=variants,
            readiness=readiness,
            suitability=suitability,
            conflicts=[],
            metrics=metrics,
        )

    def rebuild_after_fusion(
        self,
        *,
        result: TemplateSemanticsResult,
        observations_by_slide: dict[str, list[ElementObservation]],
        layout_patterns: list[LayoutPattern],
        assignments: list[SlideTemplateAssignment],
        context: TemplateAnalysisContext,
    ) -> TemplateSemanticsResult:
        decisions_by_key = self._decision_index(result.decisions)
        group_started = time.perf_counter()
        components = self._components(observations_by_slide, decisions_by_key)
        component_by_member = {
            (component.source_slide_id, element_id): component
            for component in components
            for element_id in component.member_element_ids
        }
        for decision in result.decisions:
            component = component_by_member.get((decision.source_slide_id, decision.element_id))
            decision.component_id = component.component_id if component else None
            decision.payload_owner = "component" if component else "element" if decision.payload_kind != "none" else "none"
        group_analysis_duration_ms = round((time.perf_counter() - group_started) * 1000, 3)
        self._enrich_assignments(assignments, decisions_by_key)
        self._enrich_slots(layout_patterns, decisions_by_key, component_by_member)
        coverage = self._coverage(observations_by_slide, decisions_by_key, component_by_member)
        layout_started = time.perf_counter()
        families, variants = self._layout_hierarchy(layout_patterns, assignments, coverage, result.decisions)
        layout_normalization_duration_ms = round((time.perf_counter() - layout_started) * 1000, 3)
        readiness_started = time.perf_counter()
        readiness = self._readiness(result.decisions, components, coverage, layout_patterns)
        suitability = TemplateSuitabilityReport() if self.config.design_only else self._suitability(context, assignments, result.decisions, families, readiness)
        result.components = components
        result.coverage = coverage
        result.families = families
        result.variants = variants
        result.readiness = readiness
        result.suitability = suitability
        result.metrics = self._metrics(result.decisions, components, coverage, families, variants, readiness, suitability)
        result.metrics.update(
            group_analysis_duration_ms=group_analysis_duration_ms,
            layout_normalization_duration_ms=layout_normalization_duration_ms,
            readiness_duration_ms=round((time.perf_counter() - readiness_started) * 1000, 3),
        )
        return result

    @staticmethod
    def _decision_index(
        decisions: list[ElementSemanticDecision],
    ) -> dict[tuple[str, str], ElementSemanticDecision]:
        index: dict[tuple[str, str], ElementSemanticDecision] = {}
        for decision in decisions:
            index[(decision.source_slide_id, decision.element_id)] = decision
            index[(decision.source_slide_id, decision.source_object_id)] = decision
        return index

    def fuse_content_assessment(
        self,
        *,
        decision: ElementSemanticDecision,
        observation: ElementObservation,
        content_class: ContentClass,
        confidence: float,
        evidence: list[str],
        assessment: dict[str, Any],
        context: TemplateAnalysisContext,
        source: str,
    ) -> tuple[ElementSemanticDecision, SemanticDecisionConflict | None]:
        """Fuse a bounded semantic/vision assessment through hard safety rules."""

        unsafe = {
            ContentClass.TEMPLATE_INSTRUCTION,
            ContentClass.SAMPLE_CONTENT,
            ContentClass.PLACEHOLDER_LABEL,
            ContentClass.MIXED,
        }
        original = decision.content_class
        accept = confidence >= self.config.content_class_min_confidence and (
            original == ContentClass.UNKNOWN
            or content_class in unsafe
            or (original == ContentClass.USER_CONTENT and content_class != ContentClass.DECORATIVE_TEXT)
        )
        # A provider cannot silently downgrade known unsafe content to a class
        # that permits preservation.
        if original in unsafe and content_class not in unsafe:
            accept = False
        resolved_class = content_class if accept else original
        policy, policy_evidence, warnings = self._policy(observation, resolved_class, context)
        mandatory = policy in {
            ReplacementPolicy.MUST_REPLACE,
            ReplacementPolicy.MUST_CLEAR,
            ReplacementPolicy.MUST_REPLACE_OR_CLEAR,
        }
        replaceable = policy in {
            ReplacementPolicy.MUST_REPLACE,
            ReplacementPolicy.MUST_REPLACE_OR_CLEAR,
            ReplacementPolicy.OPTIONAL_REPLACE,
        }
        clearable = policy in {
            ReplacementPolicy.MUST_CLEAR,
            ReplacementPolicy.MUST_REPLACE_OR_CLEAR,
            ReplacementPolicy.OPTIONAL_REPLACE,
        }
        protected = policy == ReplacementPolicy.PROTECTED
        safe = policy in {ReplacementPolicy.PRESERVE, ReplacementPolicy.PROTECTED} and resolved_class not in unsafe
        safe = safe or self._user_keep(observation, context)
        residue = self._residue(observation, resolved_class, policy, safe, context)
        values = decision.model_dump(mode="json")
        values.update(
            {
                "content_class": resolved_class,
                "replacement_policy": policy,
                "must_replace_or_clear": mandatory,
                "requires_resolution": mandatory,
                "safe_to_preserve": safe,
                "user_keep": self._user_keep(observation, context),
                "replaceable": replaceable,
                "clearable": clearable,
                "protected": protected,
                "cloneable": observation.source_level == "slide" and (not mandatory or self._patchable(observation)),
                "confidence": max(decision.confidence, confidence) if accept else decision.confidence,
                "evidence": list(dict.fromkeys([*decision.evidence, *evidence, *policy_evidence, f"fusion_source:{source}"])),
                "semantic_assessment": assessment if source == "semantic" else decision.semantic_assessment,
                "residue_risk": residue,
                "warnings": list(dict.fromkeys([*decision.warnings, *warnings, *([] if accept else [f"{source}_assessment_rejected_by_safety_rule"])])),
            }
        )
        fused = ElementSemanticDecision.model_validate(values)
        conflict = None
        if content_class != original:
            conflict = SemanticDecisionConflict(
                element_id=decision.element_id,
                deterministic_decision={
                    "content_class": original.value,
                    "replacement_policy": decision.replacement_policy.value,
                },
                semantic_decision=assessment if source == "semantic" else None,
                vision_decision=assessment if source == "vision" else None,
                resolved_decision={
                    "content_class": fused.content_class.value,
                    "replacement_policy": fused.replacement_policy.value,
                },
                resolution_rule="unsafe deterministic classes cannot be downgraded; otherwise high-confidence review may refine unknown content",
                confidence=fused.confidence,
                warning=f"{source} disagreed with deterministic content class",
            )
        return fused, conflict

    def _decision(
        self,
        item: ElementObservation,
        repeats: Counter[str],
        slide_count: int,
        context: TemplateAnalysisContext,
    ) -> ElementSemanticDecision:
        fragments = [self._fragment(item, raw, repeats, slide_count, context) for raw in item.text_fragments]
        classes = {fragment.content_class for fragment in fragments}
        if len(classes) > 1:
            content_class = ContentClass.MIXED
            confidence = min(fragment.confidence for fragment in fragments)
            class_evidence = ["paragraphs_have_different_content_classes"]
        elif fragments:
            content_class = fragments[0].content_class
            confidence = statistics.mean(fragment.confidence for fragment in fragments)
            class_evidence = [value for fragment in fragments for value in fragment.evidence]
        else:
            content_class, confidence, class_evidence = self._non_text_class(item)

        policy, policy_evidence, warnings = self._policy(item, content_class, context)
        requires_resolution = policy in {
            ReplacementPolicy.MUST_REPLACE,
            ReplacementPolicy.MUST_CLEAR,
            ReplacementPolicy.MUST_REPLACE_OR_CLEAR,
        }
        replaceable = policy in {
            ReplacementPolicy.MUST_REPLACE,
            ReplacementPolicy.MUST_REPLACE_OR_CLEAR,
            ReplacementPolicy.OPTIONAL_REPLACE,
        }
        clearable = policy in {
            ReplacementPolicy.MUST_CLEAR,
            ReplacementPolicy.MUST_REPLACE_OR_CLEAR,
            ReplacementPolicy.OPTIONAL_REPLACE,
        }
        protected = policy == ReplacementPolicy.PROTECTED
        safe_to_preserve = policy in {ReplacementPolicy.PRESERVE, ReplacementPolicy.PROTECTED} and content_class not in {
            ContentClass.TEMPLATE_INSTRUCTION,
            ContentClass.SAMPLE_CONTENT,
            ContentClass.PLACEHOLDER_LABEL,
            ContentClass.MIXED,
        }
        safe_to_preserve = safe_to_preserve or self._user_keep(item, context)
        payload_kind = self._payload_kind(item)
        patchable = self._patchable(item)
        residue = self._residue(item, content_class, policy, safe_to_preserve, context)
        if self.config.design_only and replaceable and not patchable:
            warnings.append("slot_has_no_supported_automatic_patch")
        if requires_resolution and not patchable:
            warnings.append("required_resolution_has_no_supported_automatic_patch")
        evidence = list(dict.fromkeys([*class_evidence, *policy_evidence, f"parser_origin:{item.source_level}"]))
        return ElementSemanticDecision(
            element_id=item.element_id,
            source_object_id=item.source_object_id,
            source_slide_id=item.slide_id,
            source_slide_index=item.slide_index,
            source_part_uri=item.source_part,
            openxml_shape_id=item.source_open_xml_shape_id,
            source_level=item.source_level,
            raw_text=item.text,
            visible=True,
            visibility_confidence=1.0,
            semantic_role=item.role,
            content_class=content_class,
            text_fragments=fragments,
            replacement_policy=policy,
            must_replace_or_clear=requires_resolution,
            requires_resolution=requires_resolution,
            safe_to_preserve=safe_to_preserve,
            user_keep=self._user_keep(item, context),
            replaceable=replaceable,
            clearable=clearable,
            cloneable=item.source_level == "slide" and (not requires_resolution or patchable),
            protected=protected,
            group_id=item.parent_group_path,
            slot_id=item.layout_slot_id,
            slot_occurrence_id=item.slot_occurrence_id,
            payload_kind=payload_kind,
            payload_owner="element" if payload_kind != "none" else "none",
            confidence=round(min(confidence, item.role_confidence.score), 4),
            evidence=evidence,
            deterministic_assessment={
                "content_class": content_class.value,
                "replacement_policy": policy.value,
                "parser_content_hint": item.parser_content_hint,
                "signals": evidence,
            },
            residue_risk=residue,
            warnings=warnings,
        )

    def _fragment(
        self,
        item: ElementObservation,
        raw: dict[str, Any],
        repeats: Counter[str],
        slide_count: int,
        context: TemplateAnalysisContext,
    ) -> TextFragmentAssessment:
        text = str(raw.get("text") or "").strip()
        normalized = normalized_text(text)
        evidence: list[str] = []
        content_class = ContentClass.UNKNOWN
        confidence = 0.56
        if item.source_level != "slide" and item.parser_content_hint == "field_value":
            content_class, confidence = ContentClass.DECORATIVE_TEXT, 0.96
            evidence.append("inherited_utility_field_furniture")
        elif item.role == "page_number":
            content_class, confidence = ContentClass.DECORATIVE_TEXT, 0.98
            evidence.append("dynamic_page_number_furniture")
        elif any(pattern.search(text) for pattern in SAMPLE_PATTERNS):
            content_class, confidence = ContentClass.SAMPLE_CONTENT, 0.99
            evidence.append("lexical_sample_marker")
        elif any(pattern.search(text) for pattern in PLACEHOLDER_PATTERNS):
            content_class, confidence = ContentClass.PLACEHOLDER_LABEL, 0.97
            evidence.append("generic_or_bracketed_placeholder_label")
        elif any(pattern.search(text) for pattern in INSTRUCTION_PATTERNS):
            content_class, confidence = ContentClass.TEMPLATE_INSTRUCTION, 0.95
            evidence.append("imperative_or_author_instruction_language")
        elif (
            context.source_presentation_role in {"template", "example_presentation"}
            and text.rstrip().endswith("?")
            and item.role in {"body", "label", "caption"}
        ):
            content_class, confidence = ContentClass.TEMPLATE_INSTRUCTION, 0.82
            evidence.append("author_question_in_template_context")
        elif (
            context.source_presentation_role in {"template", "example_presentation"}
            and (text.rstrip().endswith(("…", "...")) or re.search(r"\b(?:ваш|ваша|ваше|your)\b", text, re.IGNORECASE))
        ):
            content_class, confidence = ContentClass.TEMPLATE_INSTRUCTION, 0.78
            evidence.append("ellipsis_or_second_person_template_signal")
        elif (
            context.source_presentation_role in {"template", "example_presentation", "unknown"}
            and re.fullmatch(r"\s*(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|[+-]?\d+(?:[.,]\d+)?\s*%)\s*", text)
        ):
            content_class, confidence = ContentClass.SAMPLE_CONTENT, 0.82
            evidence.append("sample_date_or_metric_value")
        elif self._looks_decorative(item, text):
            content_class, confidence = ContentClass.DECORATIVE_TEXT, 0.86
            evidence.extend(["short_display_text", "decorative_role_or_typography"])
        elif self._looks_brand(item, normalized, repeats, slide_count):
            content_class, confidence = ContentClass.BRAND_ELEMENT, 0.88
            evidence.extend(["recurring_text", "brand_or_furniture_position"])
        elif any(pattern.search(text) for pattern in GENERIC_HEADING_PATTERNS):
            if context.source_presentation_role in {"template", "example_presentation", "unknown"}:
                content_class, confidence = ContentClass.SAMPLE_CONTENT, 0.76
                evidence.extend(["generic_heading", f"source_role:{context.source_presentation_role}"])
            else:
                content_class, confidence = ContentClass.USER_CONTENT, 0.66
                evidence.extend(["generic_heading", "existing_user_presentation_context"])
        elif context.source_presentation_role == "existing_user_presentation":
            content_class, confidence = ContentClass.USER_CONTENT, 0.82
            evidence.append("existing_user_presentation_context")
        elif context.source_presentation_role in {"template", "example_presentation"}:
            content_class, confidence = ContentClass.SAMPLE_CONTENT, 0.72
            evidence.append("template_or_example_source_context")
        elif item.placeholder_type and len(text.split()) <= 8:
            content_class, confidence = ContentClass.UNKNOWN, 0.60
            evidence.append("powerpoint_placeholder_without_enough_semantic_evidence")
        else:
            content_class, confidence = ContentClass.UNKNOWN, 0.58
            evidence.append("ambiguous_slide_level_text_requires_semantic_context")
        hint = self._policy(item, content_class, context)[0] if self.config.design_only else self._policy_hint(content_class, item.role)
        return TextFragmentAssessment(
            fragment_id=f"fragment_{stable_hash([item.element_id, raw.get('paragraph_index'), text], length=14)}",
            element_id=item.element_id,
            paragraph_index=int(raw.get("paragraph_index") or 0),
            run_indices=[int(value) for value in raw.get("run_indices") or []],
            text=text,
            content_class=content_class,
            semantic_role_hint=item.role,
            confidence=confidence,
            evidence=evidence,
            replacement_policy_hint=hint,
            warnings=["semantic_provider_review_recommended"] if confidence < self.config.content_class_min_confidence else [],
        )

    @staticmethod
    def _looks_brand(item: ElementObservation, normalized: str, repeats: Counter[str], slide_count: int) -> bool:
        repeated = bool(normalized and repeats[normalized] >= max(2, int(slide_count * 0.4)))
        furniture = item.source_level in {"master", "layout"} or item.role in {"footer", "logo", "header"}
        return (repeated and furniture and len(normalized) <= 80) or item.role == "logo"

    @staticmethod
    def _looks_decorative(item: ElementObservation, text: str) -> bool:
        size = float(item.text_style.get("font_size_pt") or item.style.get("font_size_pt") or 0.0)
        return item.role == "decorative" or (len(text.strip()) <= 3 and size >= 36)

    @staticmethod
    def _non_text_class(item: ElementObservation) -> tuple[ContentClass, float, list[str]]:
        if item.source_level in {"master", "layout"}:
            return ContentClass.DECORATIVE_TEXT, 0.88, ["inherited_visual_furniture"]
        if item.role == "logo":
            return ContentClass.BRAND_ELEMENT, 0.94, ["logo_semantic_role"]
        if item.role in {"decorative", "decorative_image", "background", "background_image", "container"}:
            return ContentClass.DECORATIVE_TEXT, 0.86, ["non_content_visual_role"]
        return ContentClass.UNKNOWN, 0.70, ["non_text_payload_content_class_not_applicable"]

    @staticmethod
    def _policy_hint(content_class: ContentClass, role: str) -> ReplacementPolicy:
        if content_class == ContentClass.TEMPLATE_INSTRUCTION:
            return ReplacementPolicy.MUST_REPLACE_OR_CLEAR
        if content_class == ContentClass.PLACEHOLDER_LABEL:
            return ReplacementPolicy.MUST_CLEAR
        if content_class in {ContentClass.SAMPLE_CONTENT, ContentClass.MIXED}:
            return ReplacementPolicy.MUST_REPLACE_OR_CLEAR
        if content_class == ContentClass.BRAND_ELEMENT:
            return ReplacementPolicy.PROTECTED
        if content_class == ContentClass.DECORATIVE_TEXT:
            return ReplacementPolicy.PRESERVE
        return ReplacementPolicy.MUST_REPLACE_OR_CLEAR if role not in {"decorative", "container"} else ReplacementPolicy.PROTECTED

    @staticmethod
    def _user_keep(item: ElementObservation, context: TemplateAnalysisContext) -> bool:
        return context.editable_elements is not None and not {item.element_id, item.source_object_id}.intersection(
            context.editable_elements.get(item.slide_id, [])
        )

    def _policy(
        self,
        item: ElementObservation,
        content_class: ContentClass,
        context: TemplateAnalysisContext,
    ) -> tuple[ReplacementPolicy, list[str], list[str]]:
        warnings: list[str] = []
        if self._user_keep(item, context):
            return ReplacementPolicy.PROTECTED, ["explicit_user_edit_scope_keep"], warnings
        if content_class == ContentClass.BRAND_ELEMENT:
            return ReplacementPolicy.PROTECTED, ["brand_elements_require_explicit_rebrand_mode"], warnings
        if content_class == ContentClass.DECORATIVE_TEXT:
            policy = ReplacementPolicy.PROTECTED if item.source_level != "slide" else ReplacementPolicy.PRESERVE
            return policy, ["decorative_design_element"], warnings
        if self.config.design_only:
            if content_class == ContentClass.UNKNOWN and item.role in {"background", "background_image", "decorative", "decorative_image", "container", "logo", "icon", "footer", "header", "page_number"} and not item.text:
                return ReplacementPolicy.PROTECTED, ["design_furniture"], warnings
            # This is a slot capability, never an instruction to mutate its payload.
            return ReplacementPolicy.OPTIONAL_REPLACE, ["template_payload_slot"], warnings
        if content_class == ContentClass.PLACEHOLDER_LABEL:
            policy = ReplacementPolicy.MUST_CLEAR if item.role in {"label", "caption"} else ReplacementPolicy.MUST_REPLACE_OR_CLEAR
            return policy, ["placeholder_labels_cannot_be_preserved"], warnings
        if content_class == ContentClass.TEMPLATE_INSTRUCTION:
            return ReplacementPolicy.MUST_REPLACE_OR_CLEAR, ["author_instructions_cannot_be_preserved"], warnings
        if content_class in {ContentClass.SAMPLE_CONTENT, ContentClass.MIXED}:
            policy = ReplacementPolicy.MUST_REPLACE if item.role in {"title", "section_title"} else ReplacementPolicy.MUST_REPLACE_OR_CLEAR
            return policy, ["sample_or_mixed_content_cannot_be_preserved"], warnings
        if content_class == ContentClass.USER_CONTENT:
            if context.preserve_original_subject_content:
                return ReplacementPolicy.OPTIONAL_REPLACE, ["original_subject_preservation_requested"], warnings
            if context.target_usage_mode == "clone_and_replace" or context.target_topic:
                return ReplacementPolicy.MUST_REPLACE_OR_CLEAR, ["target_context_may_differ_from_source_subject"], warnings
            warnings.append("user_content_policy_requires_task_context")
            return ReplacementPolicy.OPTIONAL_REPLACE, ["generic_analysis_without_target_topic"], warnings
        if item.role in {
            "background", "background_image", "decorative", "decorative_image", "container", "logo", "icon", "footer", "header", "page_number"
        } and not item.text:
            return self.config.unknown_non_content_policy, ["unknown_non_content_policy"], warnings
        warnings.append("unknown_visible_content_uses_conservative_policy")
        return self.config.unknown_content_policy, ["unknown_visible_content_policy"], warnings

    @staticmethod
    def _patchable(item: ElementObservation) -> bool:
        if item.source_level != "slide" or item.parser_support != "full" or not item.source_open_xml_shape_id:
            return False
        return bool((item.object_kind == "shape" and item.text) or item.object_kind == "picture")

    @staticmethod
    def _payload_kind(item: ElementObservation) -> str:
        if item.object_kind == "picture":
            return "image"
        if item.object_kind == "table":
            return "table"
        if item.object_kind == "chart":
            return "chart"
        if item.role == "metric":
            return "metric"
        if item.text:
            return "text"
        if item.role in {"decorative", "container", "background", "background_image"}:
            return "none"
        return "unknown"

    @staticmethod
    def _residue(
        item: ElementObservation,
        content_class: ContentClass,
        policy: ReplacementPolicy,
        safe: bool,
        context: TemplateAnalysisContext,
    ) -> TemplateResidueAssessment:
        required = policy in {
            ReplacementPolicy.MUST_REPLACE,
            ReplacementPolicy.MUST_CLEAR,
            ReplacementPolicy.MUST_REPLACE_OR_CLEAR,
        }
        subject_specific = content_class in {
            ContentClass.USER_CONTENT,
            ContentClass.SAMPLE_CONTENT,
            ContentClass.MIXED,
        } and bool(context.target_topic)
        critical = content_class in {
            ContentClass.TEMPLATE_INSTRUCTION,
            ContentClass.SAMPLE_CONTENT,
            ContentClass.PLACEHOLDER_LABEL,
            ContentClass.MIXED,
        }
        severity = "high" if critical or subject_specific else "medium" if required else "none"
        return TemplateResidueAssessment(
            element_id=item.element_id,
            content_class=content_class,
            current_content_summary=(item.text or item.object_kind)[:160],
            visible_if_unresolved=True,
            subject_specific=subject_specific,
            instruction_visible=content_class == ContentClass.TEMPLATE_INSTRUCTION,
            placeholder_visible=content_class == ContentClass.PLACEHOLDER_LABEL,
            sample_content_visible=content_class in {ContentClass.SAMPLE_CONTENT, ContentClass.MIXED},
            residue_severity=severity,
            safe_to_leave=safe,
            required_resolution=required,
            reason="Visible source payload must follow the authoritative replacement policy.",
        )

    def _components(
        self,
        observations_by_slide: dict[str, list[ElementObservation]],
        decisions: dict[tuple[str, str], ElementSemanticDecision],
    ) -> list[VisualComponentModel]:
        components: list[VisualComponentModel] = []
        claimed: set[tuple[str, str]] = set()
        for slide_id, observations in observations_by_slide.items():
            by_group: dict[str, list[ElementObservation]] = defaultdict(list)
            for item in observations:
                if item.parent_group_path:
                    by_group[item.parent_group_path].append(item)
            for group_path, members in sorted(by_group.items()):
                if len(members) < 2:
                    continue
                component = self._make_component(slide_id, members, decisions, "ooxml_group", group_path)
                components.append(component)
                claimed.update((slide_id, item.element_id) for item in members)

            pictures = [item for item in observations if item.object_kind == "picture"]
            text_items = [item for item in observations if item.text]
            frames = [item for item in observations if item.role == "container" or (not item.text and item.object_kind == "shape")]
            for picture in pictures:
                nearby = [
                    item for item in text_items
                    if (slide_id, item.element_id) not in claimed
                    and self._near_or_contains(picture, item)
                    and item.role in {"caption", "label", "body"}
                ]
                containers = [item for item in frames if self._contains(item, picture)]
                members = [picture, *nearby[:1], *containers[:1]]
                if len(members) > 1 and not any((slide_id, item.element_id) in claimed for item in members):
                    component = self._make_component(slide_id, members, decisions, "inferred_visual_group", "image")
                    components.append(component)
                    claimed.update((slide_id, item.element_id) for item in members)

            placeholder_labels = [
                item for item in text_items
                if decisions[(slide_id, item.element_id)].content_class == ContentClass.PLACEHOLDER_LABEL
            ]
            for label in placeholder_labels:
                if (slide_id, label.element_id) in claimed:
                    continue
                containers = [
                    item
                    for item in frames
                    if (slide_id, item.element_id) not in claimed and self._contains(item, label)
                ]
                if containers:
                    members = [containers[0], label]
                    component = self._make_component(slide_id, members, decisions, "inferred_visual_group", "placeholder")
                    components.append(component)
                    claimed.update((slide_id, item.element_id) for item in members)

            metric_candidates = [
                item for item in text_items
                if re.fullmatch(r"[\s\d.,+\-%]+", item.text or "")
                and (slide_id, item.element_id) not in claimed
            ]
            for metric in metric_candidates:
                nearby = [
                    item for item in text_items
                    if item is not metric and (slide_id, item.element_id) not in claimed and self._near_or_contains(metric, item)
                ]
                if nearby:
                    component = self._make_component(slide_id, [metric, nearby[0]], decisions, "inferred_visual_group", "metric")
                    components.append(component)
                    claimed.update((slide_id, item.element_id) for item in [metric, nearby[0]])
        return components

    def _make_component(
        self,
        slide_id: str,
        members: list[ElementObservation],
        decisions: dict[tuple[str, str], ElementSemanticDecision],
        group_source: str,
        discriminator: str,
    ) -> VisualComponentModel:
        member_decisions = [decisions[(slide_id, item.element_id)] for item in members]
        pictures = [item for item in members if item.object_kind == "picture"]
        labels = [item for item in members if decisions[(slide_id, item.element_id)].content_class == ContentClass.PLACEHOLDER_LABEL]
        containers = [item for item in members if item.role in {"container", "decorative", "background"} and not item.text]
        metrics = [item for item in members if re.fullmatch(r"[\s\d.,+\-%]+", item.text or "")]
        if labels and containers and not pictures:
            component_type = "image_placeholder_frame"
        elif pictures and any(item.text for item in members):
            component_type = "image_with_caption"
        elif metrics and len(members) >= 2:
            component_type = "metric_block"
        elif containers and sum(bool(item.text) for item in members) >= 2:
            component_type = "content_card"
        elif any(item.role == "footer" for item in members):
            component_type = "footer_group"
        elif any(item.role == "logo" for item in members):
            component_type = "logo_lockup"
        else:
            component_type = "generic_component"
        payload = [
            item
            for item, decision in zip(members, member_decisions, strict=True)
            if item.source_level == "slide"
            and decision.payload_kind not in {"none", "unknown"}
            and item.role not in {"container", "decorative", "background"}
            and (decision.replaceable or decision.clearable)
        ]
        payload_ids = {item.element_id for item in payload}
        supporting = [item for item in members if item.element_id not in payload_ids]
        supporting_ids = {item.element_id for item in supporting}
        primary = next((item for item in payload if item.object_kind == "picture"), payload[0] if payload else None)
        policies = [decision.replacement_policy for decision in member_decisions]
        component_policy = (
            ReplacementPolicy.MUST_REPLACE
            if ReplacementPolicy.MUST_REPLACE in policies
            else ReplacementPolicy.MUST_REPLACE_OR_CLEAR
            if any(policy in {ReplacementPolicy.MUST_CLEAR, ReplacementPolicy.MUST_REPLACE_OR_CLEAR} for policy in policies)
            else ReplacementPolicy.PROTECTED
            if all(policy == ReplacementPolicy.PROTECTED for policy in policies)
            else ReplacementPolicy.PRESERVE
        )
        classes = {decision.content_class for decision in member_decisions}
        content_class = next(iter(classes)) if len(classes) == 1 else ContentClass.MIXED
        geometry = self._union_geometry(members)
        return VisualComponentModel(
            component_id=f"component_{stable_hash([slide_id, discriminator, sorted(item.element_id for item in members)], length=14)}",
            source_slide_id=slide_id,
            component_type=component_type,
            member_element_ids=sorted(item.element_id for item in members),
            payload_element_ids=sorted(item.element_id for item in payload),
            supporting_element_ids=sorted(item.element_id for item in supporting),
            container_element_ids=sorted(item.element_id for item in containers),
            protected_element_ids=sorted(
                item.element_id
                for item, decision in zip(members, member_decisions, strict=True)
                if decision.protected or item.element_id in supporting_ids
            ),
            primary_payload_element_id=primary.element_id if primary else None,
            secondary_payload_element_ids=sorted(item.element_id for item in payload if item is not primary),
            semantic_role=primary.role if primary else members[0].role,
            content_class=content_class,
            replacement_policy=component_policy,
            geometry=geometry,
            layout_relationships=["shared_ooxml_group" if group_source == "ooxml_group" else "containment_or_alignment"],
            group_source=group_source,
            confidence=0.96 if group_source == "ooxml_group" else 0.82,
            evidence=[group_source, f"members:{len(members)}", "payload_supporting_separation"],
            warnings=[] if primary else ["component_has_no_patchable_primary_payload"],
        )

    @staticmethod
    def _contains(outer: ElementObservation, inner: ElementObservation, tolerance: float = 0.02) -> bool:
        return (
            outer.bbox.x <= inner.bbox.x + tolerance
            and outer.bbox.y <= inner.bbox.y + tolerance
            and outer.bbox.x + outer.bbox.width + tolerance >= inner.bbox.x + inner.bbox.width
            and outer.bbox.y + outer.bbox.height + tolerance >= inner.bbox.y + inner.bbox.height
        )

    @classmethod
    def _near_or_contains(cls, first: ElementObservation, second: ElementObservation) -> bool:
        if cls._contains(first, second) or cls._contains(second, first):
            return True
        x_gap = max(first.bbox.x - (second.bbox.x + second.bbox.width), second.bbox.x - (first.bbox.x + first.bbox.width), 0)
        y_gap = max(first.bbox.y - (second.bbox.y + second.bbox.height), second.bbox.y - (first.bbox.y + first.bbox.height), 0)
        aligned = abs(first.bbox.x - second.bbox.x) <= 0.05 or abs((first.bbox.x + first.bbox.width) - (second.bbox.x + second.bbox.width)) <= 0.05
        return aligned and x_gap <= 0.04 and y_gap <= 0.06

    @staticmethod
    def _union_geometry(members: list[ElementObservation]):
        from app.presentation.template.models import NormalizedGeometry

        left = min(item.bbox.x for item in members)
        top = min(item.bbox.y for item in members)
        right = max(item.bbox.x + item.bbox.width for item in members)
        bottom = max(item.bbox.y + item.bbox.height for item in members)
        return NormalizedGeometry(x=left, y=top, width=right - left, height=bottom - top)

    @staticmethod
    def _enrich_assignments(
        assignments: list[SlideTemplateAssignment],
        decisions: dict[tuple[str, str], ElementSemanticDecision],
    ) -> None:
        for assignment in assignments:
            for role in assignment.role_assignments:
                decision = decisions.get((assignment.slide_id, role.parser_element_id))
                if not decision:
                    continue
                role.semantic_decision_id = decision.element_id
                role.content_class = decision.content_class
                role.replacement_policy = decision.replacement_policy
                role.safe_to_preserve = decision.safe_to_preserve
                role.requires_resolution = decision.requires_resolution
                role.component_id = decision.component_id

    def _enrich_slots(
        self,
        patterns: list[LayoutPattern],
        decisions: dict[tuple[str, str], ElementSemanticDecision],
        components: dict[tuple[str, str], VisualComponentModel],
    ) -> None:
        for pattern in patterns:
            for slot in pattern.slots:
                slot.semantic_role = slot.role
                occurrence_slides = {item.source_slide_id for item in slot.occurrences}
                slot.template_requiredness = (
                    "required"
                    if slot.required
                    else "conditional"
                    if 0 < len(occurrence_slides) < len(pattern.member_slide_ids)
                    else "optional"
                )
                slot.requiredness_confidence = slot.confidence.score
                slot.requiredness_evidence = list(slot.evidence)
                if slot.template_requiredness == "conditional":
                    slot.requiredness_condition = "Use only on source/variant occurrences where the slot exists."
                slot.allowed_content_types = [slot.content_kind]
                slot.min_items = slot.min_count
                slot.max_items = slot.max_count
                occurrence_decisions: list[ElementSemanticDecision] = []
                for occurrence in slot.occurrences:
                    decision = decisions.get((occurrence.source_slide_id, occurrence.primary_payload_element_id or ""))
                    if decision is None:
                        decision = next(
                            (
                                decisions[(occurrence.source_slide_id, element_id)]
                                for element_id in occurrence.payload_element_ids
                                if (occurrence.source_slide_id, element_id) in decisions
                            ),
                            None,
                        )
                    if decision:
                        occurrence_decisions.append(decision)
                        occurrence.current_content_class = decision.content_class
                        occurrence.current_replacement_policy = decision.replacement_policy
                        occurrence.component_id = decision.component_id
                        occurrence.secondary_payload_element_ids = [
                            element_id for element_id in occurrence.payload_element_ids if element_id != occurrence.primary_payload_element_id
                        ]
                        occurrence.residue_assessment = self._slot_residue(slot.slot_id, occurrence, decision, components)
                slot.replacement_policy = self._aggregate_policy(occurrence_decisions)
                slot.clearable = slot.replacement_policy in {
                    ReplacementPolicy.MUST_CLEAR,
                    ReplacementPolicy.MUST_REPLACE_OR_CLEAR,
                    ReplacementPolicy.OPTIONAL_REPLACE,
                }
                slot.cloneable = all(item.cloneable for item in occurrence_decisions) if occurrence_decisions else False
                slot.component_id = next((item.component_id for item in occurrence_decisions if item.component_id), None)
                slot.payload_roles = sorted({item.semantic_role for item in occurrence_decisions})
                slot.empty_state_residue = next((item.residue_assessment for item in slot.occurrences if item.residue_assessment), None)
                slot.empty_state_policy = self._empty_state_policy(slot)
                slot.capacity = self._capacity(slot)

    @staticmethod
    def _aggregate_policy(decisions: list[ElementSemanticDecision]) -> ReplacementPolicy:
        policies = {item.replacement_policy for item in decisions}
        for policy in (
            ReplacementPolicy.MUST_REPLACE,
            ReplacementPolicy.MUST_REPLACE_OR_CLEAR,
            ReplacementPolicy.MUST_CLEAR,
            ReplacementPolicy.OPTIONAL_REPLACE,
            ReplacementPolicy.PRESERVE,
            ReplacementPolicy.PROTECTED,
        ):
            if policy in policies:
                return policy
        return ReplacementPolicy.PROTECTED

    @staticmethod
    def _slot_residue(
        slot_id: str,
        occurrence: SemanticSlotOccurrence,
        decision: ElementSemanticDecision,
        components: dict[tuple[str, str], VisualComponentModel],
    ) -> UnfilledSlotResidueAssessment:
        component = components.get((occurrence.source_slide_id, decision.element_id))
        empty_frame = bool(component and component.component_type == "image_placeholder_frame")
        required = not decision.user_keep and (decision.requires_resolution or empty_frame)
        return UnfilledSlotResidueAssessment(
            slot_id=slot_id,
            occurrence_id=occurrence.occurrence_id,
            visible_residue=decision.visible or empty_frame,
            instruction_residue=decision.content_class == ContentClass.TEMPLATE_INSTRUCTION,
            sample_content_residue=decision.content_class in {ContentClass.SAMPLE_CONTENT, ContentClass.MIXED},
            empty_frame_residue=empty_frame,
            old_subject_residue=decision.residue_risk.subject_specific,
            severity="high" if required else "none",
            safe_unfilled=not required,
            required_action="clear_or_replace" if required else "none",
        )

    @staticmethod
    def _empty_state_policy(slot) -> str:
        if slot.template_requiredness == "required":
            return "not_allowed"
        if slot.empty_state_residue and slot.empty_state_residue.empty_frame_residue:
            return "preserve_frame" if not slot.empty_state_residue.instruction_residue else "clear_payload"
        if slot.replacement_policy in {ReplacementPolicy.MUST_CLEAR, ReplacementPolicy.MUST_REPLACE_OR_CLEAR}:
            return "clear_payload"
        return "safe_empty"

    @staticmethod
    def _capacity(slot):
        if slot.content_kind == "image":
            return ImageSlotCapacity(
                min_count=slot.min_count,
                max_count=slot.max_count,
                preferred_aspect_ratio=slot.preferred_aspect_ratio,
                aspect_tolerance=slot.aspect_ratio_tolerance,
                min_resolution=slot.min_resolution,
                confidence=slot.confidence.score,
            )
        measurements = slot.text_rules.get("capacity_measurements", [])
        def minimum(key):
            values = [item[key] for item in measurements if item.get(key) is not None]
            return min(values) if len(values) == len(measurements) and values else None
        if slot.content_kind == "table":
            return TableSlotCapacity(
                max_rows=minimum("max_rows"), max_columns=minimum("max_columns"),
                confidence=minimum("confidence") or 0.0,
                measurement_source="source_table_grid",
            )
        maximum = minimum("max_chars")
        observed = slot.text_rules.get("median_characters")
        preferred = int(observed) if observed is not None else None
        return TextSlotCapacity(
            preferred_chars=min(preferred, maximum) if preferred is not None and maximum is not None else preferred,
            max_chars=maximum, max_lines=minimum("max_lines"),
            min_font_size=minimum("min_font_size"), max_font_size=minimum("max_font_size"),
            confidence=minimum("confidence") or 0.0,
            measurement_source="geometry_font_estimate" if maximum is not None else "unavailable_geometry_or_font",
        )

    @staticmethod
    def _coverage(
        observations_by_slide: dict[str, list[ElementObservation]],
        decisions: dict[tuple[str, str], ElementSemanticDecision],
        components: dict[tuple[str, str], VisualComponentModel],
    ) -> list[SlotCoverageReport]:
        reports: list[SlotCoverageReport] = []
        for slide_id, observations in sorted(observations_by_slide.items()):
            visible = len(observations)
            decided = [decisions[(slide_id, item.element_id)] for item in observations if (slide_id, item.element_id) in decisions]
            slot_covered = sum(bool(item.slot_id) for item in decided)
            component_covered = sum((slide_id, item.element_id) in components for item in decided)
            protected = sum(item.protected or item.safe_to_preserve for item in decided)
            unsafe = [
                item.element_id
                for item in decided
                if item.requires_resolution and not item.slot_id and (slide_id, item.element_id) not in components
            ]
            covered = sum(bool(item.slot_id) or (slide_id, item.element_id) in components or item.safe_to_preserve for item in decided)
            ratio = covered / visible if visible else 1.0
            reports.append(
                SlotCoverageReport(
                    source_slide_id=slide_id,
                    visible_elements=visible,
                    elements_with_decision=len(decided),
                    slot_covered_elements=slot_covered,
                    component_covered_elements=component_covered,
                    protected_elements=protected,
                    unsafe_uncovered_elements=unsafe,
                    coverage_ratio=round(ratio, 4),
                    complete=len(decided) == visible and not unsafe,
                    blocking_issues=[f"unsafe_uncovered:{element_id}" for element_id in unsafe],
                )
            )
        return reports

    def _layout_hierarchy(
        self,
        patterns: list[LayoutPattern],
        assignments: list[SlideTemplateAssignment],
        coverage: list[SlotCoverageReport],
        decisions: list[ElementSemanticDecision],
    ) -> tuple[list[LayoutFamilyModel], list[LayoutVariantModel]]:
        coverage_by_slide = {item.source_slide_id: item for item in coverage}
        decisions_by_slide: dict[str, list[ElementSemanticDecision]] = defaultdict(list)
        for decision in decisions:
            decisions_by_slide[decision.source_slide_id].append(decision)
        family_groups: dict[str, list[LayoutPattern]] = defaultdict(list)
        for pattern in patterns:
            family_groups[self._family_key(pattern)].append(pattern)
        families: list[LayoutFamilyModel] = []
        variants: list[LayoutVariantModel] = []
        for family_index, (family_key, members) in enumerate(sorted(family_groups.items()), start=1):
            family_id = f"layout_family_{family_index:03d}"
            family_variant_ids: list[str] = []
            for variant_index, pattern in enumerate(sorted(members, key=lambda item: item.layout_pattern_id), start=1):
                variant_id = f"{family_id}_variant_{variant_index:02d}"
                family_variant_ids.append(variant_id)
                eligibility = self._pattern_eligibility(pattern, coverage_by_slide, decisions_by_slide)
                singleton = len(pattern.member_slide_ids) == 1
                singleton_safety_threshold = min(1.0, self.config.min_generation_confidence + 0.05)
                singleton_safety_passed = (
                    eligibility.status == "safe"
                    and (
                        self.config.singleton_auto_use
                        or eligibility.generation_confidence >= singleton_safety_threshold
                    )
                )
                reuse_status = (
                    "blocked" if eligibility.status == "unsafe"
                    else "exemplar_only" if singleton and not singleton_safety_passed
                    else "reusable" if eligibility.status == "safe"
                    else "conditional"
                )
                if singleton and reuse_status == "exemplar_only":
                    eligibility.status = "exemplar_only"
                    eligibility.auto_selectable = False
                    eligibility.reasons.append(
                        f"singleton_variant_below_safety_threshold:{singleton_safety_threshold:.2f}"
                    )
                elif singleton:
                    eligibility.reasons.append(
                        f"singleton_variant_passed_safety_threshold:{singleton_safety_threshold:.2f}"
                    )
                pattern.family_id = family_id
                pattern.variant_id = variant_id
                pattern.generation_eligibility = eligibility.status
                pattern.generation_confidence = eligibility.generation_confidence
                pattern.auto_selectable = eligibility.auto_selectable
                for slot in pattern.slots:
                    slot.layout_family_id = family_id
                    slot.layout_variant_id = variant_id
                    slot.generation_eligibility = eligibility.status
                variants.append(
                    LayoutVariantModel(
                        variant_id=variant_id,
                        family_id=family_id,
                        member_slide_ids=pattern.member_slide_ids,
                        representative_slide_ids=pattern.representative_slide_ids,
                        strongest_semantic_slide_id=max(
                            pattern.member_slide_ids,
                            key=lambda slide_id: statistics.mean(
                                [item.confidence for item in decisions_by_slide.get(slide_id, [])] or [0.0]
                            ),
                            default=None,
                        ),
                        safest_generation_slide_id=next(
                            (slide_id for slide_id in pattern.member_slide_ids if coverage_by_slide[slide_id].complete),
                            None,
                        ),
                        variant_differences=["exact_geometry_and_decoration"],
                        exact_slots=[slot.slot_id for slot in pattern.slots],
                        source_layout_ids=pattern.source_layout_ids,
                        source_master_ids=pattern.source_master_ids,
                        confidence=pattern.confidence.score,
                        evidence=[item.evidence_id for item in pattern.evidence],
                        reuse_status=reuse_status,
                        generation_eligibility=eligibility,
                    )
                )
            member_slides = sorted({slide_id for pattern in members for slide_id in pattern.member_slide_ids})
            family_eligibility = self._family_eligibility([item.generation_eligibility for item in variants if item.family_id == family_id])
            families.append(
                LayoutFamilyModel(
                    family_id=family_id,
                    semantic_role=family_key,
                    member_variant_ids=family_variant_ids,
                    member_slide_ids=member_slides,
                    canonical_slots=sorted({slot.role for pattern in members for slot in pattern.slots}),
                    common_component_structure=[],
                    common_alignment=sorted({guide.kind for pattern in members for guide in pattern.alignment_guides}),
                    common_style_refs=sorted({ref for pattern in members for ref in [*pattern.typography_refs, *pattern.color_refs]}),
                    medoid_slide_id=members[0].representative_slide_ids[0] if members[0].representative_slide_ids else None,
                    strongest_semantic_slide_id=max(
                        member_slides,
                        key=lambda slide_id: statistics.mean([item.confidence for item in decisions_by_slide.get(slide_id, [])] or [0.0]),
                        default=None,
                    ),
                    safest_generation_slide_id=next((slide_id for slide_id in member_slides if coverage_by_slide[slide_id].complete), None),
                    confidence=round(statistics.mean(pattern.confidence.score for pattern in members), 4),
                    evidence=[item.evidence_id for pattern in members for item in pattern.evidence],
                    generation_eligibility=family_eligibility,
                )
            )
        return families, variants

    @staticmethod
    def _family_key(pattern: LayoutPattern) -> str:
        roles = {slot.role for slot in pattern.slots}
        if "table" in roles:
            return "title_table" if roles & {"title", "section_title"} else "table"
        if "chart" in roles:
            return "title_chart" if roles & {"title", "section_title"} else "chart"
        if "metric" in roles:
            return "metric_blocks"
        if roles & {"image", "photo", "hero_image", "product_image", "illustration"}:
            return "title_image" if roles & {"title", "section_title"} else "image_led_content"
        if roles <= {"title", "subtitle", "footer", "page_number"}:
            return "cover_or_section"
        if roles & {"body", "quote", "caption", "label"}:
            return "title_body" if roles & {"title", "section_title"} else "body_content"
        return pattern.semantic_type or "generic_layout"

    def _pattern_eligibility(self, pattern, coverage_by_slide, decisions_by_slide) -> GenerationEligibility:
        coverages = [coverage_by_slide[slide_id].coverage_ratio for slide_id in pattern.member_slide_ids]
        complete = all(coverage_by_slide[slide_id].complete for slide_id in pattern.member_slide_ids)
        relevant = [item for slide_id in pattern.member_slide_ids for item in decisions_by_slide.get(slide_id, [])]
        unsupported_by_id = {
            item.element_id: item
            for item in relevant
            if item.source_level == "slide" and item.requires_resolution and not item.cloneable
        }
        unsupported = list(unsupported_by_id.values())
        residue = "high" if unsupported else "low" if any(item.requires_resolution for item in relevant) else "none"
        confidence = min(pattern.confidence.score, statistics.mean(coverages) if coverages else 0.0)
        status = "safe" if complete and not unsupported and confidence >= self.config.min_generation_confidence else "conditional"
        if not complete or unsupported:
            status = "unsafe"
        return GenerationEligibility(
            status=status,
            generation_confidence=round(confidence, 4),
            reasons=["complete_semantic_and_slot_coverage"] if status == "safe" else ["generation_requires_review"],
            constraints=[f"unpatchable_required_elements:{len(unsupported)}"] if unsupported else [],
            required_manual_actions=[item.element_id for item in unsupported],
            residue_risk=residue,
            slot_coverage=round(statistics.mean(coverages), 4) if coverages else 0.0,
            evidence_strength=pattern.confidence.score,
            auto_selectable=status == "safe",
        )

    @staticmethod
    def _family_eligibility(variants: list[GenerationEligibility]) -> GenerationEligibility:
        safe = [item for item in variants if item.status == "safe"]
        status = "safe" if safe else "conditional" if any(item.status in {"conditional", "exemplar_only"} for item in variants) else "unsafe"
        scores = [item.generation_confidence for item in variants]
        return GenerationEligibility(
            status=status,
            generation_confidence=round(max(scores, default=0.0), 4),
            reasons=["family_has_generation_safe_variant"] if safe else ["family_requires_variant_review"],
            residue_risk=max((item.residue_risk for item in variants), key={"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}.get, default="none"),
            slot_coverage=max((item.slot_coverage for item in variants), default=0.0),
            evidence_strength=max((item.evidence_strength for item in variants), default=0.0),
            auto_selectable=bool(safe),
        )

    @staticmethod
    def _readiness(decisions, components, coverage, patterns) -> TemplateReadinessReport:
        missing_policy = sorted({item.element_id for item in decisions if not item.replacement_policy})
        unsafe_preserve = sorted(
            {item.element_id for item in decisions if item.safe_to_preserve and item.residue_risk.required_resolution}
        )
        unpatchable = {
            item.element_id: item
            for item in decisions
            if item.source_level == "slide" and item.requires_resolution and not item.cloneable
        }
        unsafe_layouts = sorted(
            {item.layout_pattern_id for item in patterns if item.generation_eligibility == "unsafe"}
        )
        safe_layouts = [item for item in patterns if item.generation_eligibility == "safe"]
        usable_layouts = [
            item for item in patterns if item.generation_eligibility in {"safe", "conditional"}
        ]
        blocking = [
            *[f"visible_element_without_policy:{item}" for item in missing_policy],
            *[f"unsafe_preserve:{item}" for item in unsafe_preserve],
        ]
        if not decisions:
            blocking.append("no_visible_element_inventory")
        if not usable_layouts:
            blocking.append("no_generation_eligible_layout_patterns")
        semantic_ratio = 1.0 if decisions else 0.0
        slot_ratio = statistics.mean([item.coverage_ratio for item in coverage]) if coverage else 0.0
        # Readiness is a capability of the usable catalog, not a demand that
        # every source slide be cloneable. Unsafe variants remain explicitly
        # excluded downstream while valid variants stay available to Planner.
        ready_clone = not blocking and bool(safe_layouts)
        ready_slots = not blocking and bool(usable_layouts)
        ready = ready_clone or ready_slots
        status = "ready" if ready else "partial" if decisions else "blocked"
        return TemplateReadinessReport(
            status=status,
            ready_for_planner=ready,
            ready_for_clone_and_replace=ready_clone,
            ready_for_compose_from_slots=ready_slots,
            semantic_coverage_ratio=semantic_ratio,
            slot_coverage_ratio=round(slot_ratio, 4),
            visible_elements_without_policy=missing_policy,
            unsafe_preserve_elements=unsafe_preserve,
            unresolved_template_instructions=sorted(
                item.element_id for item in unpatchable.values() if item.content_class == ContentClass.TEMPLATE_INSTRUCTION
            ),
            unresolved_sample_content=sorted(
                item.element_id for item in unpatchable.values() if item.content_class in {ContentClass.SAMPLE_CONTENT, ContentClass.MIXED}
            ),
            unresolved_placeholder_labels=sorted(
                item.element_id for item in unpatchable.values() if item.content_class == ContentClass.PLACEHOLDER_LABEL
            ),
            low_confidence_payload_targets=sorted(
                {item.element_id for item in decisions if item.replaceable and item.confidence < 0.62}
            ),
            ambiguous_components=sorted(
                {
                    item.component_id
                    for item in components
                    if not item.primary_payload_element_id
                    and item.replacement_policy not in {ReplacementPolicy.PRESERVE, ReplacementPolicy.PROTECTED}
                }
            ),
            unsafe_layout_patterns=unsafe_layouts,
            blocking_issues=blocking,
            warnings=(
                ([f"{len(unpatchable)} slide-local required resolutions need manual or unsupported patching"] if unpatchable else [])
                + ([f"{len(unsafe_layouts)} unsafe layout patterns were excluded from generation"] if unsafe_layouts else [])
                + (["Some template slots have no supported automatic patch operation"]
                   if any("slot_has_no_supported_automatic_patch" in item.warnings for item in decisions) else [])
            ),
            recommendations=["Re-run Planner and Composition from this TemplateModel version."],
        )

    @staticmethod
    def _suitability(context, assignments, decisions, families, readiness) -> TemplateSuitabilityReport:
        if not context.target_topic and not context.user_request_summary:
            return TemplateSuitabilityReport(
                overall_status="unknown_without_task_context",
                overall_score=readiness.semantic_coverage_ratio,
                suitable_layout_family_ids=[item.family_id for item in families if item.generation_eligibility.status == "safe"],
                warnings=["Task context was not supplied; generic readiness only."],
            )
        by_slide: dict[str, list[ElementSemanticDecision]] = defaultdict(list)
        for decision in decisions:
            by_slide[decision.source_slide_id].append(decision)
        safe: list[str] = []
        conditional: list[str] = []
        blocked: list[str] = []
        for assignment in assignments:
            slide_decisions = by_slide[assignment.slide_id]
            if any(item.requires_resolution and not item.cloneable for item in slide_decisions):
                blocked.append(assignment.slide_id)
            elif any(item.requires_resolution for item in slide_decisions):
                conditional.append(assignment.slide_id)
            else:
                safe.append(assignment.slide_id)
        subject = [item for item in decisions if item.residue_risk.subject_specific and item.requires_resolution]
        score = (len(safe) + 0.6 * len(conditional)) / max(len(assignments), 1)
        status = "suitable" if readiness.ready_for_planner and not blocked else "conditionally_suitable" if safe or conditional else "not_suitable"
        cleanup = sum(item.content_class in {ContentClass.TEMPLATE_INSTRUCTION, ContentClass.PLACEHOLDER_LABEL} for item in decisions)
        manual_count = len(blocked)
        return TemplateSuitabilityReport(
            task_context_hash=stable_hash(context.model_dump(mode="json"), length=16),
            target_topic=context.target_topic,
            overall_status=status,
            overall_score=round(score, 4),
            safe_clone_slide_ids=safe,
            conditional_slide_ids=conditional,
            blocked_slide_ids=blocked,
            suitable_layout_family_ids=[item.family_id for item in families if item.generation_eligibility.status == "safe"],
            old_subject_residue_risk=round(len(subject) / max(len(decisions), 1), 4),
            manual_replacement_load="high" if manual_count > 5 else "medium" if manual_count > 2 else "low" if manual_count else "none",
            required_asset_load=sum(item.payload_kind == "image" and item.requires_resolution for item in decisions),
            instruction_cleanup_load=cleanup,
            slides_requiring_assets=sorted({item.source_slide_id for item in decisions if item.payload_kind == "image" and item.requires_resolution}),
            slides_requiring_manual_review=blocked,
            slides_with_unresolved_subject_content=sorted({item.source_slide_id for item in subject}),
        )

    @staticmethod
    def _metrics(decisions, components, coverage, families, variants, readiness, suitability):
        class_counts = Counter(item.content_class.value for item in decisions)
        policy_counts = Counter(item.replacement_policy.value for item in decisions)
        metrics = {
            "visible_elements": len(decisions),
            "semantic_decisions": len(decisions),
            "semantic_coverage_ratio": readiness.semantic_coverage_ratio,
            "elements_without_decision": len(readiness.visible_elements_without_policy),
            **{f"{name}_elements": class_counts.get(name, 0) for name in ContentClass},
            **{name.value: policy_counts.get(name.value, 0) for name in ReplacementPolicy},
            "unsafe_preserve_count": len(readiness.unsafe_preserve_elements),
            "unresolved_residue_elements": sum(item.requires_resolution and not item.cloneable for item in decisions),
            "visual_components": len(components),
            "ooxml_groups": sum(item.group_source == "ooxml_group" for item in components),
            "inferred_components": sum(item.group_source == "inferred_visual_group" for item in components),
            "slot_coverage_ratio": readiness.slot_coverage_ratio,
            "layout_families": len(families),
            "layout_variants": len(variants),
            "singleton_variants": sum(len(item.member_slide_ids) == 1 for item in variants),
            "exemplar_only_variants": sum(item.reuse_status == "exemplar_only" for item in variants),
            "generation_safe_variants": sum(item.generation_eligibility.status == "safe" for item in variants),
            "generation_conditional_variants": sum(item.generation_eligibility.status in {"conditional", "exemplar_only"} for item in variants),
            "generation_unsafe_variants": sum(item.generation_eligibility.status == "unsafe" for item in variants),
            "readiness_blocking_issues": len(readiness.blocking_issues),
            "safe_clone_slides": len(suitability.safe_clone_slide_ids),
            "blocked_clone_slides": len(suitability.blocked_slide_ids),
        }
        # Keep the externally documented metric spelling while retaining the
        # mechanically derived per-class key used by existing consumers.
        metrics["brand_elements"] = class_counts.get(ContentClass.BRAND_ELEMENT.value, 0)
        return metrics
