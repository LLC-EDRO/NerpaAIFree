"""Hybrid Template Analyzer with deterministic facts and optional semantic labels."""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from app.presentation.models import PresentationModel
from app.presentation.template.confidence import (
    score_assignment_confidence,
    score_confidence,
    score_pattern_confidence,
)
from app.presentation.template.features import (
    evidence,
    geometry_distance,
    geometry_statistics,
    geometry_variance,
    quantize,
    stable_hash,
)
from app.presentation.template.models import (
    AlignmentGuide,
    BackgroundStyle,
    ColorToken,
    ConfidenceSummary,
    ContentClass,
    DecorativePattern,
    DesignSystem,
    ElementReplacePolicy,
    GlobalRule,
    ImagePattern,
    LayoutPattern,
    MasterLayoutThemeLink,
    NormalizedGeometry,
    RecurringElementPattern,
    ReplacementPolicy,
    RoleAssignment,
    SemanticSlotOccurrence,
    SlideTemplateAssignment,
    SlotRule,
    SpacingRule,
    StructuralSignature,
    TablePattern,
    TemplateAnalysisContext,
    TemplateDiagnostics,
    TemplateIssue,
    TemplateModel,
    TypographyToken,
)
from app.presentation.template.observations import ElementObservation, extract_observations, normalized_text
from app.presentation.template.semantic import (
    CONTENT_PROMPT_VERSION,
    PROMPT_VERSION,
    VISION_PROMPT_VERSION,
    ContentSemanticAssessment,
    ContentSemanticProviderResult,
    ContentSemanticRequest,
    FileSemanticCache,
    NoOpSemanticProvider,
    SemanticClusterRequest,
    SemanticMetrics,
    TemplateContentSemanticClassifier,
    TemplateSemanticProvider,
    TemplateVisionReviewer,
    TemplateVisionReviewRequest,
    VisionReviewBatch,
    VisionReviewProviderResult,
)
from app.presentation.template.semantics import TemplateSemanticsConfig, TemplateSemanticsEngine
from app.presentation.template.work import (
    IncompleteBatchError,
    analysis_session,
    call_provider,
    current_work,
    validate_coverage,
    validate_subset,
)

SemanticMode = Literal["off", "auto", "required"]


class TemplateInputError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PreviewInput:
    slide_index: int
    path: Path
    sha256: str


@dataclass(frozen=True, slots=True)
class TemplateAnalyzerConfig:
    semantic_mode: SemanticMode = "auto"
    cluster_distance_threshold: float = 0.24
    recurring_geometry_tolerance: float = 0.025
    alignment_tolerance: float = 0.015
    slot_geometry_tolerance: float = 0.06
    min_recurring_support: float = 0.4
    low_confidence_threshold: float = 0.55
    assignment_ambiguity_margin: float = 0.04
    max_semantic_previews: int = 3
    vision_review_mode: Literal["off", "risk_based", "all_slides"] = "risk_based"
    content_class_min_confidence: float = 0.62
    policy_min_confidence: float = 0.70
    vision_conflict_threshold: float = 0.18
    unknown_content_policy: str = "must_replace_or_clear"
    unknown_non_content_policy: str = "protected"
    min_generation_confidence: float = 0.72
    singleton_auto_use: bool = False
    layout_family_merge_threshold: float = 0.78
    layout_variant_threshold: float = 0.24
    semantic_max_parallelism: int = 3
    content_batch_size: int = 12
    layout_batch_size: int = 4
    analysis_request_budget: int = 64
    analysis_time_budget_seconds: float = 240.0

    def __post_init__(self):
        if min(self.semantic_max_parallelism, self.content_batch_size, self.layout_batch_size,
               self.analysis_request_budget, self.analysis_time_budget_seconds) <= 0:
            raise ValueError("Template batch sizes, concurrency and budgets must be positive")


@dataclass(slots=True)
class TemplateAnalysisResult:
    template_model: TemplateModel
    metrics: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    diagnostic_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class _SlideFeatures:
    slide_id: str
    slide_index: int
    layout_id: str | None
    master_id: str | None
    background_key: str | None
    signature: StructuralSignature
    observations: list[ElementObservation]


class TemplateAnalyzer:
    analyzer_version = "3.0.0"

    def __init__(
        self,
        *,
        config: TemplateAnalyzerConfig | None = None,
        semantic_provider: TemplateSemanticProvider | None = None,
        semantic_cache: FileSemanticCache | None = None,
    ) -> None:
        self.config = config or TemplateAnalyzerConfig()
        self.semantic_provider = semantic_provider or NoOpSemanticProvider()
        self.content_semantic_classifier = TemplateContentSemanticClassifier(self.semantic_provider)
        self.vision_reviewer = TemplateVisionReviewer(self.semantic_provider)
        self.semantic_cache = semantic_cache
        self.semantics_engine = TemplateSemanticsEngine(
            TemplateSemanticsConfig(
                design_only=True,
                content_class_min_confidence=self.config.content_class_min_confidence,
                policy_min_confidence=self.config.policy_min_confidence,
                vision_conflict_threshold=self.config.vision_conflict_threshold,
                unknown_content_policy=ReplacementPolicy(self.config.unknown_content_policy),
                unknown_non_content_policy=ReplacementPolicy(self.config.unknown_non_content_policy),
                min_generation_confidence=self.config.min_generation_confidence,
                singleton_auto_use=self.config.singleton_auto_use,
                layout_family_merge_threshold=self.config.layout_family_merge_threshold,
                layout_variant_threshold=self.config.layout_variant_threshold,
            )
        )

    @analysis_session
    def analyze(
        self,
        *,
        presentation_model: PresentationModel | dict[str, Any],
        source_parser_execution_id: str | None = None,
        previews: list[PreviewInput] | None = None,
        analysis_context: TemplateAnalysisContext | dict[str, Any] | None = None,
    ) -> TemplateAnalysisResult:
        started = time.perf_counter()
        presentation = (
            presentation_model
            if isinstance(presentation_model, PresentationModel)
            else PresentationModel.model_validate(presentation_model)
        )
        self._validate_input(presentation)
        # Compatibility argument only: user intent must not influence a reusable
        # design model or trigger task clarification / permission resolution.
        context = TemplateAnalysisContext(
            semantic_mode=self.config.semantic_mode,
            vision_review_mode=self.config.vision_review_mode,
        )
        observations_by_slide = extract_observations(presentation)
        all_observations = [
            item
            for slide in sorted(presentation.slides, key=lambda value: value.slide_index)
            for item in observations_by_slide.get(slide.slide_id, [])
        ]
        slide_count = len(presentation.slides)
        diagnostic_events: list[dict[str, Any]] = [
            {"level": "info", "code": "TEMPLATE_INPUT_ACCEPTED", "message": f"{slide_count} slides received"},
            {
                "level": "info",
                "code": "TEMPLATE_ELEMENTS_ANALYZED",
                "message": f"{len(all_observations)} visible parser elements analyzed",
            },
        ]

        recurring = self._recurring_elements(all_observations, slide_count)
        self._apply_recurring_roles(all_observations, recurring)
        typography = self._typography_tokens(all_observations, slide_count)
        colors = self._color_tokens(presentation, all_observations)
        backgrounds = self._background_styles(presentation, all_observations)
        guides = self._alignment_guides(all_observations, slide_count)
        spacing = self._spacing_rules(observations_by_slide, slide_count)
        images = self._image_patterns(all_observations, slide_count)
        tables = self._table_patterns(presentation, all_observations, slide_count)
        decorative = self._decorative_patterns(all_observations, slide_count)

        slide_features = self._slide_features(presentation, observations_by_slide)
        clusters, distances = self._cluster_slides(slide_features)
        layout_patterns, assignments = self._layout_patterns(
            presentation=presentation,
            features=slide_features,
            clusters=clusters,
            distances=distances,
            backgrounds=backgrounds,
            guides=guides,
            spacing=spacing,
            images=images,
            recurring=recurring,
        )
        semantics_started = time.perf_counter()
        semantics = self.semantics_engine.analyze(
            presentation=presentation,
            observations_by_slide=observations_by_slide,
            layout_patterns=layout_patterns,
            assignments=assignments,
            context=context,
        )
        semantics, semantic_safety_metrics, semantic_safety_warnings = self._apply_semantic_safety_review(
            semantics=semantics,
            observations_by_slide=observations_by_slide,
            layout_patterns=layout_patterns,
            assignments=assignments,
            context=context,
            previews=previews or [],
        )
        if self.config.semantic_mode == "required" and semantic_safety_warnings:
            semantics.readiness.status = "blocked"
            semantics.readiness.ready_for_planner = False
            semantics.readiness.ready_for_clone_and_replace = False
            semantics.readiness.ready_for_compose_from_slots = False
            semantics.readiness.blocking_issues.append("required_semantic_or_vision_review_failed")
        semantics_duration_ms = round((time.perf_counter() - semantics_started) * 1000, 3)
        master_map = self._master_layout_theme_map(presentation)
        global_rules = self._global_rules(guides, spacing)
        issues = [
            *self._assignment_issues(assignments),
            *[
                TemplateIssue(
                    code="SEMANTIC_SAFETY_FALLBACK",
                    message=warning,
                    severity="error" if self.config.semantic_mode == "required" else "warning",
                )
                for warning in semantic_safety_warnings
            ],
        ]
        limitations = [
            "Corner radius and full shadow/effect semantics are not normalized for every parser object.",
            "Image fit versus fill is inferred only from available crop values; opaque masks are preserved as unknown.",
            "Table cell padding and border semantics are aggregated only when present in PresentationModel.",
        ]
        confidence_summary = self._confidence_summary(
            layout_patterns=layout_patterns,
            assignments=assignments,
            recurring=recurring,
            typography=typography,
            colors=colors,
            backgrounds=backgrounds,
            guides=guides,
            spacing=spacing,
            images=images,
            tables=tables,
            decorative=decorative,
            global_rules=global_rules,
        )
        diagnostics = TemplateDiagnostics(
            status="blocked" if semantics.readiness.status == "blocked" else "warning" if issues or semantics.readiness.status == "partial" else "success",
            issues=issues,
            limitations=limitations,
            semantic_mode=self.config.semantic_mode,
            semantic_provider=None,
            semantic_model=None,
        )
        template = TemplateModel(
            analyzer_version=self.analyzer_version,
            source_presentation_id=presentation.presentation_id,
            source_parser_schema_version=presentation.schema_version,
            source_parser_execution_id=source_parser_execution_id,
            slide_dimensions={
                "width_emu": presentation.dimensions.width_emu,
                "height_emu": presentation.dimensions.height_emu,
                "aspect_ratio": presentation.dimensions.aspect_ratio,
            },
            design_system=DesignSystem(
                typography=typography,
                colors=colors,
                backgrounds=backgrounds,
                alignment_guides=guides,
                spacing_rules=spacing,
                images=images,
                tables=tables,
                decorative_patterns=decorative,
            ),
            layout_patterns=layout_patterns,
            slide_assignments=assignments,
            recurring_elements=recurring,
            master_layout_theme_map=master_map,
            global_rules=global_rules,
            diagnostics=diagnostics,
            confidence_summary=confidence_summary,
            analysis_context=context,
            element_semantics=semantics.decisions,
            visual_components=semantics.components,
            slot_coverage=semantics.coverage,
            layout_families=semantics.families,
            layout_variants=semantics.variants,
            semantic_conflicts=semantics.conflicts,
            readiness=semantics.readiness,
            suitability=semantics.suitability,
            requires_template_reanalysis=False,
            metadata={
                "analysis_method": (
                    "deterministic_visual_variant_clustering_with_separate_assignment_and_pattern_confidence"
                ),
                "cluster_distance_threshold": self.config.cluster_distance_threshold,
                "assignment_ambiguity_margin": self.config.assignment_ambiguity_margin,
                "recurring_geometry_tolerance": self.config.recurring_geometry_tolerance,
                "parser_agent_version": presentation.parser.agent_version,
                "real_content_migration": "parser_content_role_is_neutral_source_fact",
                "stale_downstream_nodes": [
                    "planner_preflight",
                    "presentation_planner",
                    "content_agent",
                    "asset_agent",
                    "layout_composition_engine",
                ],
            },
        )
        deterministic_duration_ms = round((time.perf_counter() - started) * 1000, 3)
        semantic_started = time.perf_counter()
        semantic_metrics = self._apply_semantic(template, presentation, previews or [])
        semantic_duration_ms = round((time.perf_counter() - semantic_started) * 1000, 3)
        if semantic_metrics.warnings:
            template.diagnostics.issues.extend(
                TemplateIssue(code="SEMANTIC_FALLBACK", message=warning, severity="warning")
                for warning in semantic_metrics.warnings
            )
            template.diagnostics.status = "warning"

        for warning in template.readiness.warnings:
            template.diagnostics.issues.append(
                TemplateIssue(code="TEMPLATE_READINESS_LIMITATION", message=warning, severity="warning")
            )
        if template.readiness.warnings and template.diagnostics.status == "success":
            template.diagnostics.status = "warning"
        warnings = list(dict.fromkeys(
            issue.message for issue in template.diagnostics.issues if issue.severity == "warning"
        ))
        low_confidence_rules = template.confidence_summary.low_confidence_rules
        all_slots = [slot for pattern in template.layout_patterns for slot in pattern.slots]
        metrics = {
            "slides_analyzed": slide_count,
            "elements_analyzed": len(all_observations),
            "hidden_elements": sum(
                not item.render_visible
                for scene in presentation.effective_scenes
                for item in scene.effective_objects
            ),
            "inherited_elements": sum(item.inherited for item in all_observations),
            "layout_patterns": len(template.layout_patterns),
            "singleton_layouts": sum(len(item.member_slide_ids) == 1 for item in template.layout_patterns),
            "outlier_slides": sum(item.is_outlier for item in template.slide_assignments),
            "ambiguous_assignments": sum(
                "Ambiguous visual-pattern assignment" in warning
                for item in template.slide_assignments
                for warning in item.warnings
            ),
            "low_confidence_assignments": sum(
                item.confidence.score < self.config.low_confidence_threshold for item in template.slide_assignments
            ),
            "average_assignment_confidence": template.confidence_summary.average_assignment_confidence,
            "average_pattern_confidence": template.confidence_summary.average_pattern_confidence,
            "recurring_elements": len(template.recurring_elements),
            "typography_tokens": len(template.design_system.typography),
            "color_tokens": len(template.design_system.colors),
            "background_styles": len(template.design_system.backgrounds),
            "image_patterns": len(template.design_system.images),
            "table_patterns": len(template.design_system.tables),
            "grouped_payload_targets": sum(
                bool(item.group_id) and item.payload_kind != "none" for item in template.element_semantics
            ),
            "unsupported_grouped_targets": sum(
                bool(item.group_id) and item.requires_resolution and not item.cloneable
                for item in template.element_semantics
            ),
            "slots_total": len(all_slots),
            "slot_occurrences": sum(len(item.occurrences) for item in all_slots),
            "required_slots": sum(item.template_requiredness == "required" for item in all_slots),
            "optional_slots": sum(item.template_requiredness == "optional" for item in all_slots),
            "conditional_slots": sum(item.template_requiredness == "conditional" for item in all_slots),
            "slots_without_occurrences": sum(not item.occurrences for item in all_slots),
            "unsafe_unfilled_slots": sum(
                item.empty_state_residue is not None and not item.empty_state_residue.safe_unfilled
                for item in all_slots
            ),
            "rules_total": self._rules_total(template),
            "low_confidence_rules": low_confidence_rules,
            "warnings": len(warnings),
            "overall_confidence": template.confidence_summary.overall,
            "deterministic_duration_ms": deterministic_duration_ms,
            "semantic_duration_ms": semantic_duration_ms,
            "content_semantics_duration_ms": semantics_duration_ms,
            "vision_duration_ms": semantic_safety_metrics.get("vision_duration_ms", 0.0),
            "total_duration_ms": round(deterministic_duration_ms + semantic_duration_ms, 3),
            "semantic_requests": semantic_metrics.requests,
            "semantic_cache_hits": semantic_metrics.cache_hits,
            "semantic_input_tokens": semantic_metrics.input_tokens,
            "semantic_output_tokens": semantic_metrics.output_tokens,
            "semantic_tokens": semantic_metrics.input_tokens + semantic_metrics.output_tokens,
            **semantics.metrics,
            **semantic_safety_metrics,
        }
        diagnostic_events.extend(
            [
                {
                    "level": "info",
                    "code": "LAYOUT_PATTERNS_DETECTED",
                    "message": f"{len(template.layout_patterns)} structural layout patterns detected",
                },
                {
                    "level": "info",
                    "code": "STYLE_TOKENS_EXTRACTED",
                    "message": (f"{len(typography)} typography and {len(colors)} color tokens extracted"),
                },
                {
                    "level": "info",
                    "code": "SEMANTIC_ANALYSIS_STATUS",
                    "message": (
                        f"Semantic labeling completed with {semantic_metrics.requests} requests "
                        f"and {semantic_metrics.cache_hits} cache hits"
                        if semantic_metrics.requests or semantic_metrics.cache_hits
                        else "Semantic labeling disabled or provider unavailable; deterministic result retained"
                    ),
                },
                {
                    "level": "info",
                    "code": "TEMPLATE_CONTENT_SEMANTICS_RESOLVED",
                    "message": f"Resolved authoritative semantics for {len(semantics.decisions)} visible elements",
                    "data": {
                        "semantic_coverage_ratio": semantics.readiness.semantic_coverage_ratio,
                        "content_classes": {
                            content_class.value: sum(
                                item.content_class == content_class for item in semantics.decisions
                            )
                            for content_class in ContentClass
                        },
                    },
                },
                {
                    "level": "info",
                    "code": "REPLACEMENT_POLICIES_RESOLVED",
                    "message": "Authoritative replacement policies resolved",
                    "data": {
                        policy.value: sum(item.replacement_policy == policy for item in semantics.decisions)
                        for policy in ReplacementPolicy
                    },
                },
                {
                    "level": "info",
                    "code": "VISUAL_COMPONENTS_DETECTED",
                    "message": f"Detected {len(semantics.components)} visual components",
                    "data": {
                        "ooxml_groups": semantics.metrics.get("ooxml_groups", 0),
                        "inferred_components": semantics.metrics.get("inferred_components", 0),
                    },
                },
                {
                    "level": "info",
                    "code": "LAYOUT_NORMALIZATION_COMPLETED",
                    "message": (
                        f"Normalized {len(semantics.variants)} variants into "
                        f"{len(semantics.families)} semantic families"
                    ),
                    "data": {
                        "singleton_variants": semantics.metrics.get("singleton_variants", 0),
                        "exemplar_only_variants": semantics.metrics.get("exemplar_only_variants", 0),
                    },
                },
                {
                    "level": "info" if semantics.readiness.ready_for_planner else "error",
                    "code": "TEMPLATE_READINESS_EVALUATED",
                    "message": f"Template readiness is {semantics.readiness.status}",
                },
            ]
        )
        return TemplateAnalysisResult(
            template_model=template,
            metrics=metrics,
            warnings=warnings,
            diagnostic_events=diagnostic_events,
        )

    def _apply_semantic_safety_review(
        self,
        *,
        semantics,
        observations_by_slide: dict[str, list[ElementObservation]],
        layout_patterns: list[LayoutPattern],
        assignments: list[SlideTemplateAssignment],
        context: TemplateAnalysisContext,
        previews: list[PreviewInput],
    ):
        metrics: dict[str, int | float] = {
            "semantic_provider_calls": 0,
            "content_semantic_elements_reviewed": 0,
            "vision_slides_reviewed": 0,
            "vision_elements_reviewed": 0,
            "vision_conflicts": 0,
        }
        warnings: list[str] = []
        if self.config.semantic_mode == "off" or not self.semantic_provider.available:
            return semantics, metrics, warnings
        decisions = {(item.source_slide_id, item.element_id): item for item in semantics.decisions}

        if self.content_semantic_classifier.available:
            risky_items: list[tuple[str, ElementObservation]] = []
            for slide_id, observations in sorted(observations_by_slide.items()):
                risky_items.extend(
                    (slide_id, item)
                    for item in observations
                    if (decision := decisions[(slide_id, item.element_id)]).content_class.value in {"unknown", "mixed"}
                    or decision.confidence < self.config.content_class_min_confidence
                )
            work = current_work()
            work.plan("classification", len(risky_items))
            fresh = []
            content_results = []
            content_keys = {}
            for slide_id, item in risky_items:
                inventory_item = self._semantic_inventory_item(item, decisions[(slide_id, item.element_id)])
                key = self._review_cache_key("classification", [CONTENT_PROMPT_VERSION, context.model_dump(mode="json"), inventory_item])
                content_keys[(slide_id, item.element_id)] = key
                cached = self.semantic_cache.get_payload("classification", key) if self.semantic_cache else None
                try:
                    assessment = ContentSemanticAssessment.model_validate(cached) if cached else None
                    if assessment and assessment.element_id != item.element_id:
                        assessment = None
                except ValueError:
                    assessment = None
                if assessment is not None:
                    content_results.append(([(slide_id, item)], ContentSemanticProviderResult(assessments=[assessment]), None))
                    work.finish("classification", 1, cached=True)
                else:
                    fresh.append((slide_id, item))
            content_jobs = []
            batch = []
            for value in fresh:
                # A legacy response ID is element-local; do not put repeated IDs
                # from different slides in one batch.
                if batch and (len(batch) >= self.config.content_batch_size or value[1].element_id in {item.element_id for _, item in batch}):
                    content_jobs.append(batch)
                    batch = []
                batch.append(value)
            if batch:
                content_jobs.append(batch)

            def classify_content(batch):
                inventory = tuple(self._semantic_inventory_item(item, decisions[(slide_id, item.element_id)]) for slide_id, item in batch)
                request = ContentSemanticRequest(elements=inventory, analysis_context=context.model_dump(mode="json"),
                                                 cache_key=self._review_cache_key("classification_batch", [CONTENT_PROMPT_VERSION, context.model_dump(mode="json"), inventory]))
                response = call_provider(self.semantic_provider, self.content_semantic_classifier.classify, request)
                missing = validate_subset(response.assessments, [item.element_id for _, item in batch])
                if self.semantic_cache:
                    lookup = {item.element_id: slide_id for slide_id, item in batch}
                    for assessment in response.assessments:
                        self.semantic_cache.put_payload("classification", content_keys[(lookup[assessment.element_id], assessment.element_id)], assessment.model_dump(mode="json"))
                if missing:
                    raise IncompleteBatchError(response, [f"{slide_id}/{item.element_id}" for slide_id, item in batch if item.element_id in missing])
                return response

            before_requests = work.requests
            content_results.extend(work.jobs("classification", content_jobs, classify_content,
                                             lambda batch: [f"{slide_id}/{item.element_id}" for slide_id, item in batch]))
            metrics["classification_cache_hits"] = work.stages["classification"]["cached"]
            metrics["semantic_provider_calls"] += work.requests - before_requests
            if content_results:
                for batch, response, error in content_results:
                    if error is not None:
                        affected_slides = sorted({slide_id for slide_id, _ in batch})
                        warnings.append(
                            f"Content semantic classification failed for {', '.join(affected_slides)}: {error}"
                        )
                        if response is None:
                            continue
                    lookup = {item.element_id: (slide_id, item) for slide_id, item in batch}
                    metrics["content_semantic_elements_reviewed"] += len(response.assessments)
                    for assessment in response.assessments:
                        matched = lookup.get(assessment.element_id)
                        if matched is None:
                            continue
                        slide_id, observation = matched
                        key = (slide_id, assessment.element_id)
                        if key not in decisions:
                            continue
                        fused, conflict = self.semantics_engine.fuse_content_assessment(
                            decision=decisions[key],
                            observation=observation,
                            content_class=assessment.content_class,
                            confidence=assessment.confidence,
                            evidence=assessment.evidence,
                            assessment=assessment.model_dump(mode="json"),
                            context=context,
                            source="semantic",
                        )
                        decisions[key] = fused
                        if conflict:
                            semantics.conflicts.append(conflict)

        previews_by_index = {item.slide_index: item for item in previews}
        vision_started = time.perf_counter()
        if context.vision_review_mode != "off" and self.vision_reviewer.available:
            vision_jobs = []
            missing_previews = 0
            for slide_id, observations in sorted(observations_by_slide.items()):
                slide_index = observations[0].slide_index if observations else -1
                preview = previews_by_index.get(slide_index)
                reviewable = observations
                if context.vision_review_mode == "risk_based":
                    conflicts = {item.element_id for item in semantics.conflicts
                                 if item.deterministic_decision.get("content_class") != "unknown"
                                 or (item.semantic_decision or {}).get("ambiguity")}
                    reviewable = [item for item in observations if self._needs_vision(
                        item, decisions[(slide_id, item.element_id)], item.element_id in conflicts,
                    )]
                if not reviewable:
                    continue
                if preview is None:
                    missing_previews += 1
                    current_work().pending.add(f"vision_missing_preview:{slide_id}")
                    warnings.append(f"Vision review requires a rendered preview for {slide_id}")
                    continue
                inventory = tuple(
                    self._semantic_inventory_item(item, decisions[(slide_id, item.element_id)])
                    for item in reviewable
                )
                vision_jobs.append(
                    (
                        slide_id,
                        reviewable,
                        TemplateVisionReviewRequest(
                            slide_id=slide_id,
                            element_inventory=inventory,
                            preview_path=preview.path,
                            preview_hash=preview.sha256,
                            cache_key=stable_hash(
                                [VISION_PROMPT_VERSION, self._provider_cache_identity(), preview.sha256, inventory, context.model_dump(mode="json")], length=48
                            ),
                        ),
                    )
                )

            work = current_work()
            work.plan("vision", len(vision_jobs) + missing_previews)
            if missing_previews:
                work.finish("vision", missing_previews, failed=True)
            vision_results = []
            fresh_jobs = []
            for job in vision_jobs:
                slide_id, reviewable, request = job
                cached = self.semantic_cache.get_payload("vision", request.cache_key) if self.semantic_cache else None
                try:
                    batch = VisionReviewBatch.model_validate(cached) if cached else None
                    if batch:
                        validate_coverage(batch.reviews, [item.element_id for item in reviewable])
                except ValueError:
                    batch = None
                if batch is not None:
                    vision_results.append((job, VisionReviewProviderResult(reviews=batch.reviews), None))
                    work.finish("vision", 1, cached=True)
                else:
                    fresh_jobs.append(job)

            def review_slide(job):
                slide_id, reviewable, request = job
                response = call_provider(self.semantic_provider, self.vision_reviewer.review, request)
                validate_coverage(response.reviews, [item.element_id for item in reviewable])
                if self.semantic_cache:
                    self.semantic_cache.put_payload("vision", request.cache_key, {"reviews": [item.model_dump(mode="json") for item in response.reviews]})
                return response

            before_requests = work.requests
            vision_results.extend(work.jobs("vision", fresh_jobs, review_slide, lambda job: [job[0]]))
            metrics["vision_cache_hits"] = work.stages["vision"]["cached"]
            metrics["semantic_provider_calls"] += work.requests - before_requests
            if vision_results:
                for (slide_id, reviewable, _request), response, error in vision_results:
                    if error is not None:
                        warnings.append(f"Vision review failed for {slide_id}: {error}")
                        continue
                    metrics["vision_slides_reviewed"] += 1
                    metrics["vision_elements_reviewed"] += len(response.reviews)
                    for review in response.reviews:
                        key = (slide_id, review.element_id)
                        observation = next(
                            (item for item in reviewable if item.element_id == review.element_id), None
                        )
                        if key not in decisions or observation is None:
                            continue
                        current = decisions[key]
                        if review.content_class is not None:
                            fused, conflict = self.semantics_engine.fuse_content_assessment(
                                decision=current,
                                observation=observation,
                                content_class=review.content_class,
                                confidence=review.confidence,
                                evidence=[review.evidence_summary],
                                assessment=review.model_dump(mode="json"),
                                context=context,
                                source="vision",
                            )
                            current = fused
                            if conflict:
                                semantics.conflicts.append(conflict)
                                metrics["vision_conflicts"] += 1
                        values = current.model_dump(mode="json")
                        values["vision_review"] = review.model_dump(mode="json")
                        decisions[key] = type(current).model_validate(values)
        metrics["vision_duration_ms"] = round((time.perf_counter() - vision_started) * 1000, 3)

        semantics.decisions = [
            decisions[(item.source_slide_id, item.element_id)] for item in semantics.decisions
        ]
        semantics = self.semantics_engine.rebuild_after_fusion(
            result=semantics,
            observations_by_slide=observations_by_slide,
            layout_patterns=layout_patterns,
            assignments=assignments,
            context=context,
        )
        return semantics, metrics, warnings

    def _provider_cache_identity(self):
        return {"provider": self.semantic_provider.provider_name, "model": self.semantic_provider.model,
                "fallback_models": list(getattr(self.semantic_provider, "fallback_models", ()))}

    def _review_cache_key(self, stage, payload):
        return stable_hash(["review-cache-v1", stage, self._provider_cache_identity(), payload], length=48)

    def _needs_vision(self, observation, decision, conflict=False):
        # A class resolved only from text metadata is not visual evidence for a
        # picture or an opaque group. Never hide those uncertainties by sampling
        # only one representative of an otherwise identical layout.
        uncertain = decision.confidence < self.config.content_class_min_confidence or decision.content_class in {ContentClass.UNKNOWN, ContentClass.MIXED}
        opaque_group = bool(observation.parent_group_path) and (observation.parser_support != "full" or decision.payload_owner == "unknown")
        unverified_picture = observation.object_kind == "picture" and decision.deterministic_assessment.get("content_class") not in {"brand_element", "decorative_text"}
        return uncertain or conflict or opaque_group or unverified_picture

    @staticmethod
    def _semantic_inventory_item(item: ElementObservation, decision) -> dict[str, Any]:
        return {
            "source_slide_id": item.slide_id,
            "element_id": item.element_id,
            "raw_text": item.text,
            "paragraphs": item.text_fragments,
            "source_origin": item.source_level,
            "source_part": item.source_part,
            "placeholder_type": item.placeholder_type,
            "semantic_role_candidate": item.role,
            "geometry": item.bbox.model_dump(mode="json"),
            "parent_group_path": item.parent_group_path,
            "deterministic_content_class": decision.content_class.value,
            "deterministic_replacement_policy": decision.replacement_policy.value,
            "deterministic_confidence": decision.confidence,
        }

    @staticmethod
    def _validate_input(presentation: PresentationModel) -> None:
        if not presentation.passed or presentation.diagnostics.status == "failed":
            raise TemplateInputError("PresentationModel reports a failed parser result")
        if not presentation.slides:
            raise TemplateInputError("PresentationModel contains no slides")
        if presentation.dimensions.width_emu <= 0 or presentation.dimensions.height_emu <= 0:
            raise TemplateInputError("PresentationModel has invalid slide dimensions")
        if presentation.schema_version.split(".", 1)[0] != "1":
            raise TemplateInputError(f"Unsupported PresentationModel schema version: {presentation.schema_version}")
        slide_ids = {slide.slide_id for slide in presentation.slides}
        scene_ids = {scene.slide_id for scene in presentation.effective_scenes}
        if not slide_ids.issubset(scene_ids):
            missing = sorted(slide_ids - scene_ids)
            raise TemplateInputError(f"PresentationModel is missing effective scenes: {', '.join(missing)}")

    def _recurring_elements(
        self,
        observations: list[ElementObservation],
        slide_count: int,
    ) -> list[RecurringElementPattern]:
        groups: dict[tuple[Any, ...], list[ElementObservation]] = defaultdict(list)
        tolerance = self.config.recurring_geometry_tolerance
        for item in observations:
            geom_key = tuple(
                quantize(value, tolerance) for value in (item.bbox.x, item.bbox.y, item.bbox.width, item.bbox.height)
            )
            style_key = (
                item.style.get("font_family"),
                round(float(item.style.get("font_size_pt") or 0), 1),
                item.style.get("color"),
                item.fill,
                item.line,
            )
            if item.object_kind == "picture":
                identity = ("asset", item.asset_hash or item.media_id)
            elif item.placeholder_type and item.role in {"footer", "page_number"}:
                identity = ("placeholder", item.placeholder_type.lower(), normalized_text(item.text))
            elif item.text:
                identity = ("text", normalized_text(item.text))
            else:
                identity = ("visual", item.fill, item.line, item.inherited)
            groups[(item.object_kind, identity, geom_key, style_key)].append(item)

        patterns: list[RecurringElementPattern] = []
        candidates = []
        for key, items in groups.items():
            slide_ids = sorted({item.slide_id for item in items})
            support = len(slide_ids) / max(slide_count, 1)
            if len(slide_ids) < 2 or support < self.config.min_recurring_support:
                continue
            candidates.append((key, items, support))
        candidates.sort(key=lambda value: (-len({item.slide_id for item in value[1]}), repr(value[0])))

        for index, (key, items, support) in enumerate(candidates, start=1):
            geometries = [item.bbox for item in items]
            variance = geometry_variance(geometries)
            style_keys = {
                json.dumps(
                    {
                        "style": item.style,
                        "fill": item.fill,
                        "line": item.line,
                    },
                    sort_keys=True,
                    default=str,
                )
                for item in items
            }
            style_consistency = 1.0 / len(style_keys)
            role_counts = Counter(item.role for item in items)
            possible_role = role_counts.most_common(1)[0][0]
            representative = geometry_statistics(geometries, [item.source_bbox for item in items])
            area = representative.representative.width * representative.representative.height
            if key[0] == "picture" and (area <= 0.10 or support >= 0.8):
                possible_role = "logo"
            elif possible_role == "title" and normalized_text(items[0].text):
                possible_role = "header"
            rule_evidence = evidence(
                category="recurring_element",
                source_type="recurring_element",
                slide_ids=[item.slide_id for item in items],
                element_ids=[item.element_id for item in items],
                layout_ids=[item.layout_id for item in items if item.layout_id],
                master_ids=[item.master_id for item in items if item.master_id],
                sample_count=len(items),
                support_ratio=support,
                variance={"geometry": variance},
                details={
                    "fingerprint": repr(key),
                    "style_consistency": round(style_consistency, 4),
                    "possible_role": possible_role,
                },
            )
            patterns.append(
                RecurringElementPattern(
                    recurring_pattern_id=f"recurring_{index:03d}",
                    element_type=items[0].object_kind,
                    possible_role=possible_role,
                    slide_ids=sorted({item.slide_id for item in items}),
                    element_ids=[
                        item.element_id
                        for item in sorted(items, key=lambda value: (value.slide_index, value.element_id))
                    ],
                    geometry=representative,
                    style_references=[],
                    asset_references=sorted({item.media_id for item in items if item.media_id}),
                    asset_hashes=sorted({item.asset_hash for item in items if item.asset_hash}),
                    support_ratio=round(support, 4),
                    style_consistency=round(style_consistency, 4),
                    confidence=score_confidence(
                        sample_size=len(items),
                        support_ratio=support,
                        variance=variance,
                        consistency=style_consistency,
                    ),
                    evidence=[rule_evidence],
                )
            )
        return patterns

    @staticmethod
    def _apply_recurring_roles(
        observations: list[ElementObservation],
        patterns: list[RecurringElementPattern],
    ) -> None:
        by_element = {element_id: pattern for pattern in patterns for element_id in pattern.element_ids}
        for item in observations:
            pattern = by_element.get(item.element_id)
            if pattern is None:
                continue
            item.recurring_pattern_id = pattern.recurring_pattern_id
            if pattern.possible_role in {"logo", "footer", "header", "page_number", "decorative"}:
                item.role = pattern.possible_role
                item.role_confidence = pattern.confidence
                item.role_evidence = [*item.role_evidence, *pattern.evidence]

    def _typography_tokens(
        self,
        observations: list[ElementObservation],
        slide_count: int,
    ) -> list[TypographyToken]:
        groups: dict[tuple[Any, ...], list[ElementObservation]] = defaultdict(list)
        for item in observations:
            if not item.text or not item.text_style:
                continue
            size = item.text_style.get("font_size_pt")
            key = (
                (item.text_style.get("font_family") or "").casefold(),
                round(float(size) * 2) / 2 if size is not None else None,
                item.text_style.get("font_weight"),
                item.text_style.get("bold"),
                item.text_style.get("italic"),
                item.text_style.get("underline"),
                item.text_style.get("color"),
                item.text_style.get("theme_color_ref"),
                item.alignment,
            )
            groups[key].append(item)
        ordered = sorted(groups.items(), key=lambda pair: (-len(pair[1]), repr(pair[0])))
        tokens: list[TypographyToken] = []
        for index, (key, items) in enumerate(ordered, start=1):
            token_id = f"typography_{index:03d}"
            slide_ids = sorted({item.slide_id for item in items})
            sizes = [
                float(item.text_style["font_size_pt"])
                for item in items
                if item.text_style.get("font_size_pt") is not None
            ]
            roles = Counter(item.role for item in items)
            possible_role = roles.most_common(1)[0][0] if roles else None
            if possible_role and roles[possible_role] / len(items) < 0.6:
                possible_role = None
            capitalization = Counter(self._capitalization(item.text or "") for item in items).most_common(1)[0][0]
            support = len(slide_ids) / max(slide_count, 1)
            rule_evidence = evidence(
                category="typography_token",
                source_type="statistical_pattern",
                slide_ids=slide_ids,
                element_ids=[item.element_id for item in items],
                sample_count=len(items),
                support_ratio=support,
                details={"style_key": list(key), "possible_role": possible_role},
            )
            confidence = score_confidence(
                sample_size=len(items),
                support_ratio=support,
                variance=(statistics.pstdev(sizes) / 100.0 if len(sizes) > 1 else 0.0),
                consistency=1.0,
            )
            token = TypographyToken(
                token_id=token_id,
                possible_role=possible_role,
                font_family=items[0].text_style.get("font_family"),
                median_font_size_pt=round(statistics.median(sizes), 3) if sizes else None,
                min_font_size_pt=min(sizes) if sizes else None,
                max_font_size_pt=max(sizes) if sizes else None,
                font_weight=items[0].text_style.get("font_weight"),
                bold=items[0].text_style.get("bold"),
                italic=items[0].text_style.get("italic"),
                underline=items[0].text_style.get("underline"),
                capitalization=capitalization,
                color=items[0].text_style.get("color"),
                theme_color_ref=items[0].text_style.get("theme_color_ref"),
                alignment=items[0].alignment,
                line_spacing=self._median_optional(item.line_spacing for item in items),
                paragraph_space_before=self._median_optional(item.paragraph_space_before for item in items),
                paragraph_space_after=self._median_optional(item.paragraph_space_after for item in items),
                character_spacing=self._median_optional(item.character_spacing for item in items),
                usage_count=len(items),
                slide_ids=slide_ids,
                element_ids=[item.element_id for item in items],
                confidence=confidence,
                evidence=[rule_evidence],
            )
            tokens.append(token)
            for item in items:
                item.typography_ref = token_id
        return tokens

    @staticmethod
    def _capitalization(value: str) -> str:
        letters = "".join(character for character in value if character.isalpha())
        if not letters:
            return "none"
        if letters == letters.upper():
            return "uppercase"
        if letters == letters.lower():
            return "lowercase"
        if value.istitle():
            return "title_case"
        return "mixed"

    @staticmethod
    def _median_optional(values) -> float | None:
        present = [float(value) for value in values if value is not None]
        return round(statistics.median(present), 4) if present else None

    def _color_tokens(
        self,
        presentation: PresentationModel,
        observations: list[ElementObservation],
    ) -> list[ColorToken]:
        groups: dict[tuple[str, str | None], dict[str, Any]] = {}

        def add(
            color: str | None,
            theme_ref: str | None,
            context: str,
            *,
            slide_id: str | None = None,
            element_id: str | None = None,
            declared: bool = False,
        ) -> None:
            normalized = self._normalize_color(color)
            if normalized is None:
                return
            key = (normalized, theme_ref)
            group = groups.setdefault(
                key,
                {
                    "contexts": set(),
                    "slides": set(),
                    "elements": set(),
                    "usage": 0,
                    "declared": False,
                },
            )
            group["contexts"].add(context)
            if slide_id:
                group["slides"].add(slide_id)
            if element_id:
                group["elements"].add(element_id)
            if not declared:
                group["usage"] += 1
            group["declared"] = group["declared"] or declared

        if presentation.theme:
            for theme_ref, color in sorted(presentation.theme.theme_colors.items()):
                add(color, theme_ref, "theme_palette", declared=True)
        for slide in presentation.slides:
            if slide.background:
                add(
                    slide.background.color,
                    slide.background.theme_color_ref,
                    "background",
                    slide_id=slide.slide_id,
                )
        for item in observations:
            add(
                item.text_style.get("color"),
                item.text_style.get("theme_color_ref"),
                "text",
                slide_id=item.slide_id,
                element_id=item.element_id,
            )
            add(
                item.fill,
                item.fill_theme_ref,
                "fill",
                slide_id=item.slide_id,
                element_id=item.element_id,
            )
            add(
                item.line,
                item.line_theme_ref,
                "line",
                slide_id=item.slide_id,
                element_id=item.element_id,
            )
        for table in presentation.tables:
            for context, style in (("table_header", table.header), ("table_body", table.body)):
                if style:
                    add(style.fill, None, context, slide_id=table.parent_scope_id, element_id=table.linked_object_id)
                    add(
                        style.text_color,
                        None,
                        f"{context}_text",
                        slide_id=table.parent_scope_id,
                        element_id=table.linked_object_id,
                    )
            add(
                table.border_color,
                None,
                "table_border",
                slide_id=table.parent_scope_id,
                element_id=table.linked_object_id,
            )
        for chart in presentation.charts:
            for color in chart.series_colors:
                add(color, None, "chart_series", slide_id=chart.parent_scope_id, element_id=chart.linked_object_id)

        ordered = sorted(
            groups.items(),
            key=lambda pair: (-pair[1]["usage"], pair[0][0], pair[0][1] or ""),
        )
        tokens: list[ColorToken] = []
        token_by_key: dict[tuple[str, str | None], str] = {}
        for index, ((color, theme_ref), data) in enumerate(ordered, start=1):
            token_id = f"color_{index:03d}"
            token_by_key[(color, theme_ref)] = token_id
            sample_count = data["usage"] or (1 if data["declared"] else 0)
            support = len(data["slides"]) / max(len(presentation.slides), 1)
            rule_evidence = evidence(
                category="color_token",
                source_type="pptx_theme" if data["declared"] else "parser_data",
                slide_ids=data["slides"],
                element_ids=data["elements"],
                sample_count=sample_count,
                support_ratio=support if data["usage"] else 1.0,
                details={
                    "color": color,
                    "theme_color_ref": theme_ref,
                    "contexts": sorted(data["contexts"]),
                    "declared_in_theme": data["declared"],
                },
            )
            tokens.append(
                ColorToken(
                    token_id=token_id,
                    color=color,
                    theme_color_ref=theme_ref,
                    declared_in_theme=data["declared"],
                    usage_count=data["usage"],
                    usage_contexts=sorted(data["contexts"]),
                    slide_ids=sorted(data["slides"]),
                    element_ids=sorted(data["elements"]),
                    possible_role=self._color_role(data["contexts"]),
                    confidence=score_confidence(
                        sample_size=sample_count,
                        support_ratio=1.0 if data["declared"] else support,
                        variance=0.0,
                        consistency=1.0,
                        direct_evidence=data["declared"],
                    ),
                    evidence=[rule_evidence],
                )
            )
        for item in observations:
            keys = [
                (self._normalize_color(item.text_style.get("color")), item.text_style.get("theme_color_ref")),
                (self._normalize_color(item.fill), item.fill_theme_ref),
                (self._normalize_color(item.line), item.line_theme_ref),
            ]
            item.color_refs = [token_by_key[key] for key in keys if key[0] is not None and key in token_by_key]
        return tokens

    @staticmethod
    def _normalize_color(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        color = value.strip().upper()
        if len(color) == 6 and not color.startswith("#"):
            color = f"#{color}"
        return color

    @staticmethod
    def _color_role(contexts: set[str]) -> str | None:
        if "background" in contexts:
            return "background"
        if "text" in contexts:
            return "text"
        if "chart_series" in contexts:
            return "data_accent"
        if "fill" in contexts:
            return "surface_or_accent"
        return None

    def _background_styles(
        self,
        presentation: PresentationModel,
        observations: list[ElementObservation],
    ) -> list[BackgroundStyle]:
        groups: dict[tuple[str | None, str | None], dict[str, Any]] = defaultdict(
            lambda: {"slides": set(), "levels": set(), "elements": set()}
        )
        for slide in presentation.slides:
            background = slide.background
            key = (
                self._normalize_color(background.color) if background else None,
                background.theme_color_ref if background else None,
            )
            groups[key]["slides"].add(slide.slide_id)
            groups[key]["levels"].add(background.source if background else "unresolved")
        for item in observations:
            if item.role != "background":
                continue
            key = (self._normalize_color(item.fill), item.fill_theme_ref)
            groups[key]["slides"].add(item.slide_id)
            groups[key]["levels"].add(item.source_level)
            groups[key]["elements"].add(item.element_id)
        ordered = sorted(groups.items(), key=lambda pair: (-len(pair[1]["slides"]), repr(pair[0])))
        styles: list[BackgroundStyle] = []
        for index, ((color, theme_ref), data) in enumerate(ordered, start=1):
            support = len(data["slides"]) / max(len(presentation.slides), 1)
            rule_evidence = evidence(
                category="background_style",
                source_type="parser_data",
                slide_ids=data["slides"],
                element_ids=data["elements"],
                sample_count=len(data["slides"]),
                support_ratio=support,
                details={"color": color, "theme_color_ref": theme_ref, "sources": sorted(data["levels"])},
            )
            styles.append(
                BackgroundStyle(
                    background_id=f"background_{index:03d}",
                    color=color,
                    theme_color_ref=theme_ref,
                    source_levels=sorted(data["levels"]),
                    slide_ids=sorted(data["slides"]),
                    support_ratio=round(support, 4),
                    full_slide_element_ids=sorted(data["elements"]),
                    confidence=score_confidence(
                        sample_size=len(data["slides"]),
                        support_ratio=support,
                        variance=0.0,
                        consistency=1.0,
                        direct_evidence=True,
                    ),
                    evidence=[rule_evidence],
                )
            )
        return styles

    def _alignment_guides(
        self,
        observations: list[ElementObservation],
        slide_count: int,
    ) -> list[AlignmentGuide]:
        candidates: list[tuple[str, str, float, ElementObservation]] = []
        for item in observations:
            if item.role == "background":
                continue
            candidates.extend(
                [
                    ("x", "left", item.bbox.x, item),
                    ("x", "right", item.bbox.x + item.bbox.width, item),
                    ("x", "center", item.bbox.x + item.bbox.width / 2, item),
                    ("y", "top", item.bbox.y, item),
                    ("y", "bottom", item.bbox.y + item.bbox.height, item),
                ]
            )
        guides: list[AlignmentGuide] = []
        for axis, kind in (("x", "left"), ("x", "right"), ("x", "center"), ("y", "top"), ("y", "bottom")):
            values = [
                (value, item)
                for cand_axis, cand_kind, value, item in candidates
                if cand_axis == axis and cand_kind == kind
            ]
            for group in self._cluster_numeric(values, self.config.alignment_tolerance):
                slides = sorted({item.slide_id for _value, item in group})
                if len(group) < 2 or len(slides) < 2:
                    continue
                position = round(statistics.median(value for value, _item in group), 5)
                variance = statistics.pstdev(value for value, _item in group) if len(group) > 1 else 0.0
                support = len(slides) / max(slide_count, 1)
                rule_evidence = evidence(
                    category="alignment_guide",
                    source_type="statistical_pattern",
                    slide_ids=slides,
                    element_ids=[item.element_id for _value, item in group],
                    sample_count=len(group),
                    support_ratio=support,
                    variance={"position": variance},
                    details={"axis": axis, "kind": kind, "median": position},
                )
                guides.append(
                    AlignmentGuide(
                        guide_id=f"guide_{axis}_{kind}_{len(guides) + 1:03d}",
                        axis=axis,
                        kind=kind,
                        position=position,
                        slide_ids=slides,
                        element_ids=sorted({item.element_id for _value, item in group}),
                        confidence=score_confidence(
                            sample_size=len(group),
                            support_ratio=support,
                            variance=variance,
                            consistency=1.0,
                        ),
                        evidence=[rule_evidence],
                    )
                )
        return sorted(guides, key=lambda item: (item.axis, item.position, item.kind))

    def _spacing_rules(
        self,
        by_slide: dict[str, list[ElementObservation]],
        slide_count: int,
    ) -> list[SpacingRule]:
        candidates: list[tuple[str, float, str, str, ElementObservation, ElementObservation | None]] = []
        for slide_id, items in by_slide.items():
            visible = [item for item in items if item.role != "background"]
            for item in visible:
                if item.bbox.x <= 0.30:
                    candidates.append(("margin", item.bbox.x, "left_outer_margin", slide_id, item, None))
                right = 1.0 - (item.bbox.x + item.bbox.width)
                if right <= 0.30:
                    candidates.append(("margin", right, "right_outer_margin", slide_id, item, None))
            ordered_y = sorted(visible, key=lambda item: (item.bbox.y, item.bbox.x))
            for left, right_item in zip(ordered_y, ordered_y[1:], strict=False):
                gap = right_item.bbox.y - (left.bbox.y + left.bbox.height)
                if 0 < gap <= 0.35:
                    candidates.append(
                        ("vertical", gap, f"{left.role}_to_{right_item.role}", slide_id, left, right_item)
                    )
            ordered_x = sorted(visible, key=lambda item: (item.bbox.x, item.bbox.y))
            for left, right_item in zip(ordered_x, ordered_x[1:], strict=False):
                vertical_overlap = min(
                    left.bbox.y + left.bbox.height,
                    right_item.bbox.y + right_item.bbox.height,
                ) - max(left.bbox.y, right_item.bbox.y)
                gap = right_item.bbox.x - (left.bbox.x + left.bbox.width)
                if vertical_overlap > 0 and 0 < gap <= 0.35:
                    candidates.append(
                        ("horizontal", gap, f"{left.role}_to_{right_item.role}", slide_id, left, right_item)
                    )

        rules: list[SpacingRule] = []
        for axis in ("margin", "vertical", "horizontal"):
            values = [
                (value, (relationship, slide_id, left, right))
                for cand_axis, value, relationship, slide_id, left, right in candidates
                if cand_axis == axis
            ]
            for group in self._cluster_numeric(values, 0.02):
                slides = sorted({payload[1] for _value, payload in group})
                if len(group) < 2 or len(slides) < 2:
                    continue
                relationships = Counter(payload[0] for _value, payload in group)
                relationship = relationships.most_common(1)[0][0]
                value = round(statistics.median(item[0] for item in group), 5)
                variance = statistics.pstdev(item[0] for item in group) if len(group) > 1 else 0.0
                elements = sorted(
                    {
                        observation.element_id
                        for _number, payload in group
                        for observation in (payload[2], payload[3])
                        if observation is not None
                    }
                )
                support = len(slides) / max(slide_count, 1)
                rule_evidence = evidence(
                    category="spacing_rule",
                    source_type="statistical_pattern",
                    slide_ids=slides,
                    element_ids=elements,
                    sample_count=len(group),
                    support_ratio=support,
                    variance={"spacing": variance},
                    details={"axis": axis, "relationship": relationship, "median": value},
                )
                rules.append(
                    SpacingRule(
                        spacing_id=f"spacing_{axis}_{len(rules) + 1:03d}",
                        axis=axis,
                        value=value,
                        relationship=relationship,
                        slide_ids=slides,
                        element_ids=elements,
                        confidence=score_confidence(
                            sample_size=len(group),
                            support_ratio=support,
                            variance=variance,
                            consistency=relationships[relationship] / len(group),
                        ),
                        evidence=[rule_evidence],
                    )
                )
        return sorted(rules, key=lambda item: (item.axis, item.value, item.relationship))

    @staticmethod
    def _cluster_numeric(values: list[tuple[float, Any]], tolerance: float) -> list[list[tuple[float, Any]]]:
        groups: list[list[tuple[float, Any]]] = []
        for value, payload in sorted(values, key=lambda item: item[0]):
            if not groups:
                groups.append([(value, payload)])
                continue
            # Input and every group are already sorted. Re-sorting the whole
            # current group through statistics.median on each append makes
            # dense decks quadratic (tens of millions of comparisons).
            current = groups[-1]
            midpoint = len(current) // 2
            median = (
                current[midpoint][0]
                if len(current) % 2
                else (current[midpoint - 1][0] + current[midpoint][0]) / 2
            )
            if abs(value - median) <= tolerance:
                current.append((value, payload))
            else:
                groups.append([(value, payload)])
        return groups

    def _image_patterns(
        self,
        observations: list[ElementObservation],
        slide_count: int,
    ) -> list[ImagePattern]:
        groups: dict[tuple[Any, ...], list[ElementObservation]] = defaultdict(list)
        for item in observations:
            if item.object_kind != "picture":
                continue
            crop_mode = self._crop_mode(item.image_crop)
            key = (
                quantize(item.bbox.x, 0.05),
                quantize(item.bbox.y, 0.05),
                quantize(item.bbox.width, 0.05),
                quantize(item.bbox.height, 0.05),
                crop_mode,
            )
            groups[key].append(item)
        ordered = sorted(groups.items(), key=lambda pair: (-len(pair[1]), repr(pair[0])))
        patterns: list[ImagePattern] = []
        for index, (_key, items) in enumerate(ordered, start=1):
            slides = sorted({item.slide_id for item in items})
            support = len(slides) / max(slide_count, 1)
            geometries = [item.bbox for item in items]
            variance = geometry_variance(geometries)
            ratios = [item.bbox.width / item.bbox.height for item in items if item.bbox.height > 0]
            crop_modes = Counter(self._crop_mode(item.image_crop) for item in items)
            crop_mode = crop_modes.most_common(1)[0][0]
            rule_evidence = evidence(
                category="image_pattern",
                source_type="statistical_pattern",
                slide_ids=slides,
                element_ids=[item.element_id for item in items],
                sample_count=len(items),
                support_ratio=support,
                variance={"geometry": variance},
                details={"crop_mode": crop_mode, "aspect_ratios": [round(value, 4) for value in ratios]},
            )
            patterns.append(
                ImagePattern(
                    image_pattern_id=f"image_pattern_{index:03d}",
                    geometry=geometry_statistics(geometries, [item.source_bbox for item in items]),
                    aspect_ratio_median=round(statistics.median(ratios), 4) if ratios else 0.0,
                    crop_mode=crop_mode,
                    asset_hashes=sorted({item.asset_hash for item in items if item.asset_hash}),
                    slide_ids=slides,
                    element_ids=[item.element_id for item in items],
                    support_ratio=round(support, 4),
                    confidence=score_confidence(
                        sample_size=len(items),
                        support_ratio=support,
                        variance=variance,
                        consistency=crop_modes[crop_mode] / len(items),
                    ),
                    evidence=[rule_evidence],
                )
            )
        return patterns

    @staticmethod
    def _crop_mode(value: dict[str, float | None] | None) -> Literal["crop", "uncropped", "unknown"]:
        if value is None:
            return "unknown"
        present = [item for item in value.values() if item is not None]
        if not present:
            return "unknown"
        return "crop" if any(abs(float(item)) > 1e-9 for item in present) else "uncropped"

    def _table_patterns(
        self,
        presentation: PresentationModel,
        observations: list[ElementObservation],
        slide_count: int,
    ) -> list[TablePattern]:
        observation_by_source = {item.source_object_id: item for item in observations}
        groups: dict[tuple[Any, ...], list[Any]] = defaultdict(list)
        for table in presentation.tables:
            key = (
                table.table_style_id,
                json.dumps(table.header.model_dump(mode="json") if table.header else None, sort_keys=True),
                json.dumps(table.body.model_dump(mode="json") if table.body else None, sort_keys=True),
                table.banded_rows,
                table.border_color,
                table.border_width_pt,
            )
            groups[key].append(table)
        ordered = sorted(groups.items(), key=lambda pair: (-len(pair[1]), repr(pair[0])))
        patterns: list[TablePattern] = []
        for index, (_key, items) in enumerate(ordered, start=1):
            linked = [
                observation_by_source[item.linked_object_id]
                for item in items
                if item.linked_object_id in observation_by_source
            ]
            slides = sorted({item.parent_scope_id for item in items})
            support = len(slides) / max(slide_count, 1)
            variance = geometry_variance(item.bbox for item in linked)
            rule_evidence = evidence(
                category="table_pattern",
                source_type="parser_data",
                slide_ids=slides,
                element_ids=[item.linked_object_id for item in items if item.linked_object_id],
                sample_count=len(items),
                support_ratio=support,
                variance={"geometry": variance},
                details={
                    "table_style_id": items[0].table_style_id,
                    "banded_rows": items[0].banded_rows,
                    "row_counts": [item.row_count for item in items],
                    "column_counts": [item.col_count for item in items],
                },
            )
            patterns.append(
                TablePattern(
                    table_pattern_id=f"table_pattern_{index:03d}",
                    table_style_id=items[0].table_style_id,
                    geometry=geometry_statistics(
                        [item.bbox for item in linked],
                        [item.source_bbox for item in linked],
                    )
                    if linked
                    else None,
                    header_style=items[0].header.model_dump(mode="json") if items[0].header else None,
                    body_style=items[0].body.model_dump(mode="json") if items[0].body else None,
                    banded_rows=items[0].banded_rows,
                    border_color=items[0].border_color,
                    border_width_pt=items[0].border_width_pt,
                    common_cell_margins_emu=self._common_table_margins(items),
                    vertical_alignments=sorted(
                        {
                            cell.vertical_alignment
                            for table in items
                            for row in table.rows
                            for cell in row.cells
                            if cell.vertical_alignment
                        }
                    ),
                    slide_ids=slides,
                    element_ids=[item.linked_object_id for item in items if item.linked_object_id],
                    confidence=score_confidence(
                        sample_size=len(items),
                        support_ratio=support,
                        variance=variance,
                        consistency=1.0,
                        direct_evidence=True,
                    ),
                    evidence=[rule_evidence],
                )
            )
        return patterns

    @staticmethod
    def _common_table_margins(items: list[Any]) -> dict[str, int | None]:
        values: dict[str, list[int]] = defaultdict(list)
        for table in items:
            for row in table.rows:
                for cell in row.cells:
                    for side, value in cell.margins.items():
                        if value is not None:
                            values[side].append(int(value))
        return {
            side: int(statistics.median(side_values)) for side, side_values in sorted(values.items()) if side_values
        }

    def _decorative_patterns(
        self,
        observations: list[ElementObservation],
        slide_count: int,
    ) -> list[DecorativePattern]:
        groups: dict[tuple[Any, ...], list[ElementObservation]] = defaultdict(list)
        for item in observations:
            if item.role != "decorative":
                continue
            key = (
                item.object_kind,
                item.fill,
                item.line,
                quantize(item.bbox.x, 0.04),
                quantize(item.bbox.y, 0.04),
                quantize(item.bbox.width, 0.04),
                quantize(item.bbox.height, 0.04),
            )
            groups[key].append(item)
        ordered = sorted(groups.items(), key=lambda pair: (-len(pair[1]), repr(pair[0])))
        patterns = []
        for index, (_key, items) in enumerate(ordered, start=1):
            slides = sorted({item.slide_id for item in items})
            support = len(slides) / max(slide_count, 1)
            variance = geometry_variance(item.bbox for item in items)
            rule_evidence = evidence(
                category="decorative_pattern",
                source_type="statistical_pattern",
                slide_ids=slides,
                element_ids=[item.element_id for item in items],
                sample_count=len(items),
                support_ratio=support,
                variance={"geometry": variance},
                details={"fill": items[0].fill, "line": items[0].line},
            )
            patterns.append(
                DecorativePattern(
                    decorative_pattern_id=f"decorative_{index:03d}",
                    parser_type=items[0].object_kind,
                    geometry=geometry_statistics(
                        [item.bbox for item in items],
                        [item.source_bbox for item in items],
                    ),
                    fill=items[0].fill,
                    line=items[0].line,
                    slide_ids=slides,
                    element_ids=[item.element_id for item in items],
                    confidence=score_confidence(
                        sample_size=len(items),
                        support_ratio=support,
                        variance=variance,
                        consistency=1.0,
                    ),
                    evidence=[rule_evidence],
                )
            )
        return patterns

    def _slide_features(
        self,
        presentation: PresentationModel,
        by_slide: dict[str, list[ElementObservation]],
    ) -> list[_SlideFeatures]:
        backgrounds = {
            slide.slide_id: (
                self._normalize_color(slide.background.color) if slide.background else None,
                slide.background.theme_color_ref if slide.background else None,
            )
            for slide in presentation.slides
        }
        features: list[_SlideFeatures] = []
        scene_by_slide = {scene.slide_id: scene for scene in presentation.effective_scenes}
        for slide in sorted(presentation.slides, key=lambda item: item.slide_index):
            items = by_slide.get(slide.slide_id, [])
            counts = Counter(item.object_kind for item in items)
            roles = Counter(item.role for item in items)
            archetype_counts = self._archetype_counts(items)
            fingerprint = [
                {
                    "type": item.object_kind,
                    "role": item.role,
                    "x": round(item.bbox.x, 2),
                    "y": round(item.bbox.y, 2),
                    "w": round(item.bbox.width, 2),
                    "h": round(item.bbox.height, 2),
                }
                for item in sorted(
                    items,
                    key=lambda item: (-(item.bbox.width * item.bbox.height), item.bbox.y, item.bbox.x),
                )[:10]
            ]
            scene = scene_by_slide[slide.slide_id]
            background_key = json.dumps(backgrounds[slide.slide_id])
            signature_data = {
                "layout": slide.layout_id,
                "master": scene.master_id,
                "background": background_key,
                "counts": dict(sorted(counts.items())),
                "roles": dict(sorted(roles.items())),
                "placeholders": sorted({item.placeholder_type for item in items if item.placeholder_type}),
                "geometry": fingerprint,
                "archetype": archetype_counts,
            }
            signature = StructuralSignature(
                source_layout_id=slide.layout_id,
                source_master_id=scene.master_id,
                background_key=background_key,
                counts_by_type=dict(sorted(counts.items())),
                role_counts=dict(sorted(roles.items())),
                placeholder_types=signature_data["placeholders"],
                geometry_fingerprint=fingerprint,
                archetype_counts=archetype_counts,
                signature_hash=stable_hash(signature_data, length=20),
            )
            features.append(
                _SlideFeatures(
                    slide_id=slide.slide_id,
                    slide_index=slide.slide_index,
                    layout_id=slide.layout_id,
                    master_id=scene.master_id,
                    background_key=background_key,
                    signature=signature,
                    observations=items,
                )
            )
        return features

    @staticmethod
    def _archetype_counts(items: list[ElementObservation]) -> dict[str, int]:
        """Describe reusable content lanes without decorative template furniture.

        Fine geometry remains available in ``geometry_fingerprint``. This
        coarser silhouette lets equivalent media-left/text-right or multi-column
        variants match even when the actual device asset or copy height changes.
        """

        candidates = [
            item
            for item in items
            if not item.inherited and item.role not in {"decorative", "background", "footer", "page_number"}
        ]
        max_media_area = max(
            (item.bbox.width * item.bbox.height for item in candidates if item.object_kind == "picture"),
            default=0.0,
        )
        prepared: list[tuple[ElementObservation, str]] = []
        for item in candidates:
            area = item.bbox.width * item.bbox.height
            if item.object_kind == "picture":
                if max_media_area and area < max_media_area * 0.08:
                    continue
                group = "media"
            elif item.object_kind in {"table", "chart"}:
                group = "data"
            elif item.text:
                group = "text"
            else:
                continue
            prepared.append((item, group))

        sparse_text_only = len(prepared) <= 2 and not any(group in {"media", "data"} for _, group in prepared)
        counts: Counter[str] = Counter()
        for item, group in prepared:
            center_x = item.bbox.x + item.bbox.width / 2
            center_y = item.bbox.y + item.bbox.height / 2
            column = "left" if center_x < 0.42 else "right" if center_x > 0.58 else "center"
            if sparse_text_only:
                row = "top" if center_y < 0.33 else "bottom" if center_y > 0.67 else "middle"
                token = f"{group}:{column}:{row}"
            else:
                token = f"{group}:{column}"
            counts[token] += 1
        return {key: min(value, 4) for key, value in sorted(counts.items())}

    def _cluster_slides(
        self,
        features: list[_SlideFeatures],
    ) -> tuple[list[list[int]], dict[tuple[int, int], float]]:
        distances: dict[tuple[int, int], float] = {}
        for left in range(len(features)):
            for right in range(left + 1, len(features)):
                distances[(left, right)] = self._slide_distance(features[left], features[right])
        clusters: list[list[int]] = [[index] for index in range(len(features))]
        while True:
            best: tuple[float, int, int] | None = None
            for left in range(len(clusters)):
                for right in range(left + 1, len(clusters)):
                    pair_distances = [
                        distances[tuple(sorted((a, b)))] for a in clusters[left] for b in clusters[right] if a != b
                    ]
                    average = sum(pair_distances) / len(pair_distances)
                    candidate = (round(average, 8), left, right)
                    if best is None or candidate < best:
                        best = candidate
            if best is None or best[0] > self.config.cluster_distance_threshold:
                break
            _distance, left, right = best
            merged = sorted([*clusters[left], *clusters[right]])
            clusters = [cluster for index, cluster in enumerate(clusters) if index not in {left, right}]
            clusters.append(merged)
            clusters.sort(key=lambda cluster: min(features[index].slide_index for index in cluster))
        return clusters, distances

    def _slide_distance(self, left: _SlideFeatures, right: _SlideFeatures) -> float:
        count_distance = self._mapping_distance(
            left.signature.counts_by_type,
            right.signature.counts_by_type,
        )
        role_distance = self._mapping_distance(
            left.signature.role_counts,
            right.signature.role_counts,
        )
        geometry = self._fingerprint_distance(
            left.signature.geometry_fingerprint,
            right.signature.geometry_fingerprint,
        )
        layout = 0.0 if left.layout_id == right.layout_id else 1.0
        background = 0.0 if left.background_key == right.background_key else 1.0
        detailed_distance = (
            0.25 * count_distance + 0.20 * role_distance + 0.42 * geometry + 0.08 * layout + 0.05 * background
        )
        archetype_distance = self._mapping_distance(
            left.signature.archetype_counts,
            right.signature.archetype_counts,
        )
        return round(0.65 * detailed_distance + 0.35 * archetype_distance, 6)

    @staticmethod
    def _mapping_distance(left: dict[str, int], right: dict[str, int]) -> float:
        keys = set(left) | set(right)
        numerator = sum(abs(left.get(key, 0) - right.get(key, 0)) for key in keys)
        denominator = max(sum(left.values()), sum(right.values()), 1)
        return min(numerator / denominator, 1.0)

    @staticmethod
    def _fingerprint_distance(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> float:
        if not left and not right:
            return 0.0
        if not left or not right:
            return 1.0
        available = list(range(len(right)))
        distances = []
        for item in left:
            matching = [
                index
                for index in available
                if right[index]["role"] == item["role"] and right[index]["type"] == item["type"]
            ]
            if not matching:
                distances.append(1.0)
                continue
            best = min(
                matching,
                key=lambda index: geometry_distance(
                    NormalizedGeometry(
                        x=item["x"],
                        y=item["y"],
                        width=item["w"],
                        height=item["h"],
                    ),
                    NormalizedGeometry(
                        x=right[index]["x"],
                        y=right[index]["y"],
                        width=right[index]["w"],
                        height=right[index]["h"],
                    ),
                ),
            )
            distances.append(
                min(
                    geometry_distance(
                        NormalizedGeometry(x=item["x"], y=item["y"], width=item["w"], height=item["h"]),
                        NormalizedGeometry(
                            x=right[best]["x"],
                            y=right[best]["y"],
                            width=right[best]["w"],
                            height=right[best]["h"],
                        ),
                    )
                    / 0.35,
                    1.0,
                )
            )
            available.remove(best)
        distances.extend([1.0] * len(available))
        return sum(distances) / max(len(left), len(right))

    def _layout_patterns(
        self,
        *,
        presentation: PresentationModel,
        features: list[_SlideFeatures],
        clusters: list[list[int]],
        distances: dict[tuple[int, int], float],
        backgrounds: list[BackgroundStyle],
        guides: list[AlignmentGuide],
        spacing: list[SpacingRule],
        images: list[ImagePattern],
        recurring: list[RecurringElementPattern],
    ) -> tuple[list[LayoutPattern], list[SlideTemplateAssignment]]:
        patterns: list[LayoutPattern] = []
        assignments: list[SlideTemplateAssignment] = []
        background_by_slide = {slide_id: item.background_id for item in backgrounds for slide_id in item.slide_ids}
        image_by_element = {element_id: item.image_pattern_id for item in images for element_id in item.element_ids}
        recurring_by_element = {
            element_id: item.recurring_pattern_id for item in recurring for element_id in item.element_ids
        }
        ordered_clusters = sorted(
            clusters,
            key=lambda group: min(features[index].slide_index for index in group),
        )
        cluster_records = [
            (f"layout_pattern_{index:03d}", cluster, self._medoid(cluster, distances))
            for index, cluster in enumerate(ordered_clusters, start=1)
        ]
        for pattern_id, cluster, medoid in cluster_records:
            member_features = [features[index] for index in cluster]
            member_ids = [item.slide_id for item in member_features]
            pair_values = [
                distances[tuple(sorted((left, right)))]
                for pos, left in enumerate(cluster)
                for right in cluster[pos + 1 :]
            ]
            internal_distance = statistics.mean(pair_values) if pair_values else 0.0
            support = len(cluster) / len(features)
            pattern_confidence = score_pattern_confidence(
                sample_size=len(cluster),
                deck_prevalence=support,
                internal_distance=internal_distance,
                distance_threshold=self.config.cluster_distance_threshold,
            )
            pattern_evidence = evidence(
                category="layout_pattern",
                source_type="statistical_pattern",
                slide_ids=member_ids,
                layout_ids=[item.layout_id for item in member_features if item.layout_id],
                master_ids=[item.master_id for item in member_features if item.master_id],
                sample_count=len(cluster),
                support_ratio=support,
                variance={"mean_structural_distance": internal_distance},
                details={
                    "medoid_slide_id": features[medoid].slide_id,
                    "distance_threshold": self.config.cluster_distance_threshold,
                    "signature_hash": features[medoid].signature.signature_hash,
                    "evidence_class": ("single_observation" if len(cluster) == 1 else "repeated_observation"),
                },
            )
            member_observations = [observation for item in member_features for observation in item.observations]
            slots = self._slots(
                pattern_id=pattern_id,
                member_slide_ids=member_ids,
                observations=member_observations,
                image_by_element=image_by_element,
            )
            pattern_guides = [
                item for item in guides if len(set(item.slide_ids) & set(member_ids)) >= min(2, len(member_ids))
            ]
            pattern_spacing = [
                item for item in spacing if len(set(item.slide_ids) & set(member_ids)) >= min(2, len(member_ids))
            ]
            background_ref = Counter(background_by_slide.get(slide_id) for slide_id in member_ids).most_common(1)[0][0]
            typography_refs = sorted({item.typography_ref for item in member_observations if item.typography_ref})
            color_refs = sorted({ref for item in member_observations for ref in item.color_refs})
            image_refs = sorted(
                {
                    image_by_element[item.element_id]
                    for item in member_observations
                    if item.element_id in image_by_element
                }
            )
            recurring_refs = sorted(
                {
                    recurring_by_element[item.element_id]
                    for item in member_observations
                    if item.element_id in recurring_by_element
                }
            )
            patterns.append(
                LayoutPattern(
                    layout_pattern_id=pattern_id,
                    member_slide_ids=member_ids,
                    representative_slide_ids=[features[medoid].slide_id],
                    outlier_slide_ids=[],
                    source_layout_ids=sorted({item.layout_id for item in member_features if item.layout_id}),
                    source_master_ids=sorted({item.master_id for item in member_features if item.master_id}),
                    structural_signature=features[medoid].signature,
                    slots=slots,
                    alignment_guides=pattern_guides,
                    spacing_rules=pattern_spacing,
                    background_style_ref=background_ref,
                    typography_refs=typography_refs,
                    color_refs=color_refs,
                    image_pattern_refs=image_refs,
                    recurring_element_refs=recurring_refs,
                    evidence_class=("single_observation" if len(cluster) == 1 else "repeated_observation"),
                    confidence=pattern_confidence,
                    evidence=[pattern_evidence],
                    warnings=[],
                )
            )
            for feature_index in cluster:
                feature = features[feature_index]
                distance = 0.0 if feature_index == medoid else distances[tuple(sorted((feature_index, medoid)))]
                alternatives = sorted(
                    (
                        (
                            other_pattern_id,
                            distances[tuple(sorted((feature_index, other_medoid)))],
                        )
                        for other_pattern_id, _other_cluster, other_medoid in cluster_records
                        if other_pattern_id != pattern_id and feature_index != other_medoid
                    ),
                    key=lambda item: (item[1], item[0]),
                )
                alternative_pattern_id, alternative_distance = alternatives[0] if alternatives else (None, None)
                separation_margin = (
                    max(alternative_distance - distance, 0.0) if alternative_distance is not None else None
                )
                source_layout_match = feature.layout_id is not None and feature.layout_id == features[medoid].layout_id
                outlier = len(cluster) > 1 and distance > self.config.cluster_distance_threshold
                outlier_score = min(
                    distance / (self.config.cluster_distance_threshold * 2.0),
                    1.0,
                )
                assignment_confidence = score_assignment_confidence(
                    cluster_distance=distance,
                    distance_threshold=self.config.cluster_distance_threshold,
                    alternative_distance=alternative_distance,
                    pattern_sample_size=len(cluster),
                    source_layout_match=source_layout_match,
                    source_layout_available=feature.layout_id is not None,
                )
                role_assignments = [
                    RoleAssignment(
                        parser_element_id=item.element_id,
                        source_object_id=item.source_object_id,
                        parser_type=item.object_kind,
                        parent_group_path=item.parent_group_path,
                        source_level=item.source_level,
                        source_part=item.source_part,
                        source_open_xml_shape_id=item.source_open_xml_shape_id,
                        source_element_fingerprint=item.source_element_fingerprint,
                        source_relationship_id=item.source_relationship_id,
                        role=item.role,
                        normalized_geometry=item.bbox,
                        source_geometry=item.source_bbox,
                        layout_slot_id=item.layout_slot_id,
                        slot_occurrence_id=item.slot_occurrence_id,
                        slot_member_role=item.slot_member_role,
                        typography_ref=item.typography_ref,
                        color_refs=item.color_refs,
                        recurring_pattern_id=item.recurring_pattern_id,
                        confidence=item.role_confidence,
                        evidence=item.role_evidence,
                        replace_policy=self._replace_policy(item),
                    )
                    for item in feature.observations
                ]
                assignment_warnings = []
                if outlier:
                    assignment_warnings.append("Structural outlier within the selected visual pattern")
                ambiguous = (
                    alternative_distance is not None
                    and alternative_distance <= self.config.cluster_distance_threshold * 1.25
                    and separation_margin is not None
                    and separation_margin < self.config.assignment_ambiguity_margin
                )
                if ambiguous:
                    assignment_warnings.append(
                        "Ambiguous visual-pattern assignment; nearest alternatives are too close"
                    )
                if assignment_confidence.score < self.config.low_confidence_threshold:
                    assignment_warnings.append("Low-confidence visual-pattern assignment")
                assignments.append(
                    SlideTemplateAssignment(
                        slide_id=feature.slide_id,
                        slide_index=feature.slide_index,
                        layout_pattern_id=pattern_id,
                        cluster_distance=round(distance, 6),
                        outlier_score=round(outlier_score, 4),
                        is_outlier=outlier,
                        assignment_basis=("exact_representative" if feature_index == medoid else "structural_match"),
                        source_layout_id=feature.layout_id,
                        source_master_id=feature.master_id,
                        alternative_layout_pattern_id=alternative_pattern_id,
                        alternative_distance=(
                            round(alternative_distance, 6) if alternative_distance is not None else None
                        ),
                        separation_margin=(round(separation_margin, 6) if separation_margin is not None else None),
                        role_assignments=role_assignments,
                        confidence=assignment_confidence,
                        warnings=assignment_warnings,
                    )
                )
        for pattern in patterns:
            pattern.outlier_slide_ids = [
                item.slide_id
                for item in assignments
                if item.layout_pattern_id == pattern.layout_pattern_id and item.is_outlier
            ]
        for image in images:
            image.related_layout_pattern_ids = sorted(
                {
                    assignment.layout_pattern_id
                    for assignment in assignments
                    if any(role.parser_element_id in image.element_ids for role in assignment.role_assignments)
                }
            )
        return patterns, sorted(assignments, key=lambda item: item.slide_index)

    @staticmethod
    def _replace_policy(item: ElementObservation) -> ElementReplacePolicy:
        """Issue an explicit allowlist policy only when mutation is defensible.

        This intentionally does not improve semantic mapping.  It records the
        existing analyzer evidence and defaults every ambiguous case to keep.
        """

        protected_roles = {
            "background",
            "background_image",
            "decorative",
            "decorative_image",
            "container",
            "logo",
            "icon",
            "footer",
            "header",
            "page_number",
            "unknown",
        }
        full_slide_visual = (
            item.bbox.x <= 0.02
            and item.bbox.y <= 0.02
            and item.bbox.width >= 0.96
            and item.bbox.height >= 0.96
        )
        policy = "never"
        allowed: list[str] = []
        reason = "Source elements are protected unless a supported replacement is explicitly allowed."
        if item.source_level != "slide":
            reason = f"{item.source_level} elements are inherited and immutable in clone-and-replace mode."
        elif item.recurring_pattern_id:
            reason = "Recurring elements are protected by default."
        elif item.parser_support != "full":
            reason = f"Parser support is {item.parser_support}; unsupported structure must be preserved."
        elif item.role in protected_roles:
            reason = f"Semantic role {item.role} is protected by default."
        elif full_slide_visual:
            reason = "Full-slide visual is treated as background and protected."
        elif item.object_kind == "picture" and item.role in {
            "image",
            "photo",
            "hero_image",
            "product_image",
            "illustration",
        }:
            policy = "replace_image"
            allowed = ["replace_image"]
            reason = "Slide-local picture, including an exact group child, may replace only its media reference."
        elif item.object_kind == "shape" and item.text and item.role in {
            "title",
            "subtitle",
            "section_title",
            "body",
            "caption",
            "label",
            "quote",
            "metric",
        }:
            policy = "replace_text"
            allowed = ["replace_text"]
            reason = "Slide-local text shape, including an exact group child, may replace only its text payload."
        else:
            reason = "Element type and role do not form a supported explicit replacement target."
        replaceable = bool(allowed)
        policy_confidence = 1.0
        if policy == "replace_text":
            policy_confidence = 0.96
        elif policy == "replace_image":
            policy_confidence = 0.98
        return ElementReplacePolicy(
            source_element_id=item.source_object_id,
            replaceable=replaceable,
            replace_policy=policy,
            allowed_operations=allowed,
            semantic_role=item.role,
            role_confidence=item.role_confidence.score,
            policy_confidence=policy_confidence,
            source_element_fingerprint=item.source_element_fingerprint,
            evidence=item.role_evidence,
            reason=reason,
        )

    @staticmethod
    def _medoid(cluster: list[int], distances: dict[tuple[int, int], float]) -> int:
        if len(cluster) == 1:
            return cluster[0]
        return min(
            cluster,
            key=lambda candidate: (
                sum(distances[tuple(sorted((candidate, other)))] for other in cluster if other != candidate),
                candidate,
            ),
        )

    def _slots(
        self,
        *,
        pattern_id: str,
        member_slide_ids: list[str],
        observations: list[ElementObservation],
        image_by_element: dict[str, str],
    ) -> list[SlotRule]:
        groups: dict[tuple[Any, ...], list[ElementObservation]] = defaultdict(list)
        for item in observations:
            if item.role in {
                "background",
                "background_image",
                "decorative",
                "decorative_image",
                "container",
                "logo",
                "icon",
                "footer",
                "header",
                "page_number",
                "unknown",
            } and item.object_kind != "picture":
                continue
            key = (
                item.role,
                item.object_kind,
                quantize(item.bbox.x, self.config.slot_geometry_tolerance),
                quantize(item.bbox.y, self.config.slot_geometry_tolerance),
                quantize(item.bbox.width, self.config.slot_geometry_tolerance),
                quantize(item.bbox.height, self.config.slot_geometry_tolerance),
            )
            groups[key].append(item)
        ordered = sorted(groups.items(), key=lambda pair: (pair[0][0], pair[0][1], repr(pair[0][2:])))
        slots: list[SlotRule] = []
        for index, (_key, items) in enumerate(ordered, start=1):
            slides = sorted({item.slide_id for item in items})
            support = len(slides) / max(len(member_slide_ids), 1)
            per_slide = Counter(item.slide_id for item in items)
            # A populated singleton exemplar is not sufficient evidence that a
            # payload is structurally required. Titles are the only safe
            # deterministic exception; other roles need repeated observations.
            structurally_required_role = items[0].role in {"title", "section_title"}
            repeated_evidence = len(member_slide_ids) >= 2 and len(slides) >= 2
            required = structurally_required_role or (support >= 0.8 and repeated_evidence)
            variance = geometry_variance(item.bbox for item in items)
            slot_id = f"{pattern_id}_slot_{index:02d}"
            for item in items:
                item.layout_slot_id = slot_id
            occurrences = self._slot_occurrences(slot_id, items, observations)
            text_lengths = [len(item.text or "") for item in items if item.text]
            capacity_facts = [item.capacity_facts for item in items]
            image_ref = Counter(
                image_by_element[item.element_id] for item in items if item.element_id in image_by_element
            ).most_common(1)
            rule_evidence = evidence(
                category="layout_slot",
                source_type="statistical_pattern",
                slide_ids=slides,
                element_ids=[item.element_id for item in items],
                layout_ids=[item.layout_id for item in items if item.layout_id],
                master_ids=[item.master_id for item in items if item.master_id],
                sample_count=len(items),
                support_ratio=support,
                variance={"geometry": variance},
                details={
                    "role": items[0].role,
                    "required": required,
                    "pattern_id": pattern_id,
                    "repeated_requiredness_evidence": repeated_evidence,
                    "singleton_payload_does_not_imply_required": len(member_slide_ids) == 1,
                },
            )
            content_kind = self._content_kind(items[0])
            visual_slot = content_kind == "image"
            replaceable_occurrences = [item for item in occurrences if item.replaceable]
            allowed_operations = sorted(
                {operation for item in replaceable_occurrences for operation in item.allowed_operations}
            )
            allowed_asset_types = sorted(
                {asset_type for item in replaceable_occurrences for asset_type in item.allowed_asset_types}
            )
            preferred_aspect_ratio = (
                round(
                    statistics.median(
                        item.geometry.width / item.geometry.height
                        for item in occurrences
                        if item.geometry is not None and item.geometry.height > 0
                    ),
                    4,
                )
                if visual_slot
                and any(item.geometry is not None and item.geometry.height > 0 for item in occurrences)
                else None
            )
            slots.append(
                SlotRule(
                    slot_id=slot_id,
                    role=items[0].role,
                    content_kind=content_kind,
                    slot_kind=(
                        "visual"
                        if visual_slot
                        else "table"
                        if content_kind == "table"
                        else "chart"
                        if content_kind == "chart"
                        else "metric"
                        if content_kind == "metric"
                        else "text"
                        if content_kind == "text"
                        else "unknown"
                    ),
                    allowed_element_types=sorted({item.object_kind for item in items}),
                    min_count=1 if required else 0,
                    max_count=max(per_slide.values()),
                    required=required,
                    repeatable=max(per_slide.values()) > 1,
                    replaceable=bool(replaceable_occurrences),
                    replace_policy=(
                        "replace_image"
                        if "replace_image" in allowed_operations
                        else "replace_text"
                        if "replace_text" in allowed_operations
                        else "never"
                    ),
                    allowed_operations=allowed_operations,
                    allowed_asset_types=allowed_asset_types,
                    preferred_aspect_ratio=preferred_aspect_ratio,
                    transparency_preference=(
                        "preferred" if items[0].role in {"logo", "icon"} else "irrelevant"
                    ),
                    geometry=geometry_statistics(
                        [item.bbox for item in items],
                        [item.source_bbox for item in items],
                    ),
                    typography_refs=sorted({item.typography_ref for item in items if item.typography_ref}),
                    color_refs=sorted({ref for item in items for ref in item.color_refs}),
                    image_pattern_ref=image_ref[0][0] if image_ref else None,
                    text_rules={
                        "capacity_measurements": capacity_facts,
                        "median_characters": round(statistics.median(text_lengths), 2) if text_lengths else None,
                        "observed_min_characters": min(text_lengths) if text_lengths else None,
                        "observed_max_characters": max(text_lengths) if text_lengths else None,
                    },
                    image_rules={
                        "aspect_ratio": round(
                            statistics.median(
                                item.bbox.width / item.bbox.height
                                for item in items
                                if item.object_kind == "picture" and item.bbox.height > 0
                            ),
                            4,
                        )
                    }
                    if any(item.object_kind == "picture" and item.bbox.height > 0 for item in items)
                    else {},
                    confidence=score_confidence(
                        sample_size=len(items),
                        support_ratio=support,
                        variance=variance,
                        consistency=1.0,
                    ),
                    evidence=[rule_evidence],
                    occurrences=occurrences,
                )
            )
        return slots

    def _slot_occurrences(
        self,
        slot_id: str,
        items: list[ElementObservation],
        all_observations: list[ElementObservation],
    ) -> list[SemanticSlotOccurrence]:
        by_slide: dict[str, list[ElementObservation]] = defaultdict(list)
        for item in items:
            by_slide[item.slide_id].append(item)
        occurrences: list[SemanticSlotOccurrence] = []
        for slide_id, members in sorted(by_slide.items()):
            ordered = sorted(members, key=lambda item: (item.effective_z_index, item.source_object_id))
            policies = {item.source_object_id: self._replace_policy(item) for item in ordered}
            primary_candidates = [
                item
                for item in ordered
                if policies[item.source_object_id].replaceable
                and policies[item.source_object_id].allowed_operations
                and item.source_level == "slide"
            ]
            primary = primary_candidates[0] if len(primary_candidates) == 1 else None
            supporting: list[ElementObservation] = []
            if primary is not None:
                supporting = [
                    item
                    for item in all_observations
                    if item.slide_id == slide_id
                    and item.source_level == "slide"
                    and item.source_object_id != primary.source_object_id
                    and item.role in {"container", "decorative", "decorative_image"}
                    and self._supports_payload(item.bbox, primary.bbox)
                ]
            occurrence_id = f"{slot_id}_occ_{slide_id}"
            protected_ids = sorted(
                {
                    *[item.source_object_id for item in ordered if item is not primary],
                    *[item.source_object_id for item in supporting],
                }
            )
            for item in ordered:
                item.slot_occurrence_id = occurrence_id
                item.slot_member_role = "primary_payload" if item is primary else "protected"
            for item in supporting:
                if item.layout_slot_id is None:
                    item.layout_slot_id = slot_id
                item.slot_occurrence_id = occurrence_id
                item.slot_member_role = "supporting"
            policy = policies[primary.source_object_id] if primary is not None else None
            warnings: list[str] = []
            if len(primary_candidates) > 1:
                warnings.append("ambiguous_primary_payload")
            elif primary is None:
                warnings.append("no_slide_local_primary_payload")
            placeholder_id = None
            if primary is not None and primary.placeholder_idx is not None:
                placeholder_id = (
                    f"{primary.source_level}:{primary.source_object_id}:"
                    f"placeholder:{primary.placeholder_idx}:{primary.placeholder_type or 'unknown'}"
                )
            occurrences.append(
                SemanticSlotOccurrence(
                    occurrence_id=occurrence_id,
                    slot_id=slot_id,
                    source_slide_id=slide_id,
                    source_slide_index=ordered[0].slide_index,
                    source_slide_part_uri=(
                        primary.source_part if primary is not None else ordered[0].source_part
                    ),
                    source_level=(primary.source_level if primary is not None else ordered[0].source_level),
                    source_open_xml_shape_id=(
                        primary.source_open_xml_shape_id
                        if primary is not None
                        else ordered[0].source_open_xml_shape_id
                    ),
                    source_element_fingerprint=(
                        primary.source_element_fingerprint
                        if primary is not None
                        else ordered[0].source_element_fingerprint
                    ),
                    semantic_role=(primary.role if primary is not None else ordered[0].role),
                    content_kind=self._content_kind(primary if primary is not None else ordered[0]),
                    payload_element_ids=sorted(item.source_object_id for item in ordered),
                    supporting_element_ids=sorted(item.source_object_id for item in supporting),
                    protected_element_ids=protected_ids,
                    primary_payload_element_id=primary.source_object_id if primary is not None else None,
                    placeholder_id=placeholder_id,
                    source_layout_id=(primary.layout_id if primary is not None else ordered[0].layout_id),
                    source_master_id=(primary.master_id if primary is not None else ordered[0].master_id),
                    relationship_id=(
                        primary.source_relationship_id
                        if primary is not None
                        else ordered[0].source_relationship_id
                    ),
                    geometry=primary.bbox if primary is not None else ordered[0].bbox,
                    capacity_override={
                        **(primary.capacity_facts if primary is not None else ordered[0].capacity_facts),
                        "observed_text_characters": len(primary.text or "") if primary is not None else 0,
                        "observed_aspect_ratio": (
                            round(primary.bbox.width / primary.bbox.height, 4)
                            if primary is not None and primary.bbox.height > 0
                            else None
                        ),
                    },
                    role_confidence=(primary.role_confidence.score if primary is not None else max(
                        item.role_confidence.score for item in ordered
                    )),
                    replace_policy=policy.replace_policy if policy is not None else "never",
                    allowed_operations=list(policy.allowed_operations) if policy is not None else [],
                    policy_confidence=policy.policy_confidence if policy is not None else 1.0,
                    replaceable=bool(policy and policy.replaceable),
                    allowed_asset_types=(
                        self._allowed_asset_types(primary.role)
                        if primary is not None and policy is not None and policy.replaceable
                        else []
                    ),
                    evidence=[evidence for item in ordered for evidence in item.role_evidence],
                    warnings=warnings,
                )
            )
        return occurrences

    @staticmethod
    def _allowed_asset_types(role: str) -> list[str]:
        if role in {"image", "photo", "hero_image", "product_image"}:
            return ["existing_image"]
        if role == "illustration":
            return ["diagram_reference", "existing_image"]
        if role == "icon":
            return ["icon"]
        if role == "logo":
            return ["logo"]
        return []

    @staticmethod
    def _content_kind(item: ElementObservation) -> str:
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
        return "unknown"

    @staticmethod
    def _supports_payload(container, payload) -> bool:
        contains = (
            container.x <= payload.x + 0.015
            and container.y <= payload.y + 0.015
            and container.x + container.width + 0.015 >= payload.x + payload.width
            and container.y + container.height + 0.015 >= payload.y + payload.height
        )
        if contains:
            return True
        overlap_width = max(
            0.0,
            min(container.x + container.width, payload.x + payload.width) - max(container.x, payload.x),
        )
        overlap_height = max(
            0.0,
            min(container.y + container.height, payload.y + payload.height) - max(container.y, payload.y),
        )
        overlap = overlap_width * overlap_height
        smaller = min(container.width * container.height, payload.width * payload.height)
        return bool(smaller and overlap / smaller >= 0.85)

    @staticmethod
    def _master_layout_theme_map(presentation: PresentationModel) -> list[MasterLayoutThemeLink]:
        slides_by_layout: dict[str, list[str]] = defaultdict(list)
        for slide in presentation.slides:
            if slide.layout_id:
                slides_by_layout[slide.layout_id].append(slide.slide_id)
        theme_id = presentation.theme.theme_id if presentation.theme else None
        return [
            MasterLayoutThemeLink(
                layout_id=layout.layout_id,
                master_id=layout.master_id,
                theme_id=theme_id,
                slide_ids=sorted(slides_by_layout.get(layout.layout_id, [])),
            )
            for layout in sorted(presentation.layouts, key=lambda item: item.layout_id)
        ]

    @staticmethod
    def _global_rules(
        guides: list[AlignmentGuide],
        spacing: list[SpacingRule],
    ) -> list[GlobalRule]:
        rules: list[GlobalRule] = []
        for guide in guides:
            rules.append(
                GlobalRule(
                    rule_id=f"rule_{guide.guide_id}",
                    category="alignment",
                    name=f"{guide.kind}_{guide.axis}",
                    value=guide.position,
                    confidence=guide.confidence,
                    evidence=guide.evidence,
                )
            )
        for item in spacing:
            rules.append(
                GlobalRule(
                    rule_id=f"rule_{item.spacing_id}",
                    category="spacing",
                    name=item.relationship,
                    value=item.value,
                    confidence=item.confidence,
                    evidence=item.evidence,
                )
            )
        return rules

    def _assignment_issues(
        self,
        assignments: list[SlideTemplateAssignment],
    ) -> list[TemplateIssue]:
        issues: list[TemplateIssue] = []
        for assignment in assignments:
            if assignment.is_outlier:
                issues.append(
                    TemplateIssue(
                        code="STRUCTURAL_PATTERN_OUTLIER",
                        message=(f"Structural outlier within visual pattern for {assignment.slide_id}"),
                        slide_id=assignment.slide_id,
                        rule_id=assignment.layout_pattern_id,
                    )
                )
            if any(warning.startswith("Ambiguous") for warning in assignment.warnings):
                issues.append(
                    TemplateIssue(
                        code="AMBIGUOUS_LAYOUT_ASSIGNMENT",
                        message=f"Ambiguous visual-pattern assignment for {assignment.slide_id}",
                        slide_id=assignment.slide_id,
                        rule_id=assignment.layout_pattern_id,
                    )
                )
            if assignment.confidence.score < self.config.low_confidence_threshold:
                issues.append(
                    TemplateIssue(
                        code="LOW_CONFIDENCE_LAYOUT_ASSIGNMENT",
                        message=f"Low-confidence visual-pattern assignment for {assignment.slide_id}",
                        slide_id=assignment.slide_id,
                        rule_id=assignment.layout_pattern_id,
                    )
                )
        return issues

    @staticmethod
    def _confidence_summary(
        *,
        layout_patterns: list[LayoutPattern],
        assignments: list[SlideTemplateAssignment],
        recurring: list[RecurringElementPattern],
        typography: list[TypographyToken],
        colors: list[ColorToken],
        backgrounds: list[BackgroundStyle],
        guides: list[AlignmentGuide],
        spacing: list[SpacingRule],
        images: list[ImagePattern],
        tables: list[TablePattern],
        decorative: list[DecorativePattern],
        global_rules: list[GlobalRule],
    ) -> ConfidenceSummary:
        confidence_objects = [
            *[item.confidence for item in layout_patterns],
            *[role.confidence for assignment in assignments for role in assignment.role_assignments],
            *[item.confidence for item in recurring],
            *[item.confidence for item in typography],
            *[item.confidence for item in colors],
            *[item.confidence for item in backgrounds],
            *[item.confidence for item in guides],
            *[item.confidence for item in spacing],
            *[item.confidence for item in images],
            *[item.confidence for item in tables],
            *[item.confidence for item in decorative],
            *[item.confidence for item in global_rules],
        ]
        values = [item.score for item in confidence_objects]
        pattern_values = [item.confidence.score for item in layout_patterns]
        assignment_values = [item.confidence.score for item in assignments]
        role_values = [role.confidence.score for assignment in assignments for role in assignment.role_assignments]
        return ConfidenceSummary(
            overall=round(statistics.mean(values), 4) if values else 0.0,
            high_confidence_rules=sum(value >= 0.8 for value in values),
            medium_confidence_rules=sum(0.55 <= value < 0.8 for value in values),
            low_confidence_rules=sum(value < 0.55 for value in values),
            average_layout_confidence=(round(statistics.mean(pattern_values), 4) if pattern_values else 0.0),
            average_pattern_confidence=(round(statistics.mean(pattern_values), 4) if pattern_values else 0.0),
            average_assignment_confidence=(round(statistics.mean(assignment_values), 4) if assignment_values else 0.0),
            average_role_confidence=round(statistics.mean(role_values), 4) if role_values else 0.0,
        )

    def _apply_semantic(
        self,
        template: TemplateModel,
        presentation: PresentationModel,
        previews: list[PreviewInput],
    ) -> SemanticMetrics:
        metrics = SemanticMetrics()
        mode = self.config.semantic_mode
        provider = self.semantic_provider
        if mode == "off":
            return metrics
        if not provider.available:
            if mode == "required":
                raise RuntimeError("Semantic mode is required but no semantic provider is configured")
            return metrics
        template.diagnostics.semantic_provider = provider.provider_name
        template.diagnostics.semantic_model = provider.model
        preview_by_index = {item.slide_index: item for item in previews}
        slide_index_by_id = {slide.slide_id: slide.slide_index for slide in presentation.slides}
        work = current_work()
        work.plan("layouts", len(template.layout_patterns))
        pending = []
        completed = {}
        for pattern in template.layout_patterns:
            selected_previews = [
                preview_by_index[slide_index_by_id[slide_id]]
                for slide_id in pattern.representative_slide_ids[: self.config.max_semantic_previews]
                if slide_index_by_id.get(slide_id) in preview_by_index
            ]
            summary = {
                "member_slide_ids": pattern.member_slide_ids,
                "representative_slide_ids": pattern.representative_slide_ids,
                "structural_signature": pattern.structural_signature.model_dump(mode="json"),
                "slots": [
                    {
                        "slot_id": slot.slot_id,
                        "role": slot.role,
                        "allowed_element_types": slot.allowed_element_types,
                        "required": slot.required,
                        "geometry": slot.geometry.representative.model_dump(mode="json"),
                    }
                    for slot in pattern.slots
                ],
            }
            cache_key = stable_hash(
                {
                    "layout_pattern_id": pattern.layout_pattern_id,
                    "summary": summary,
                    "preview_hashes": [item.sha256 for item in selected_previews],
                    "provider": provider.provider_name,
                    "model": provider.model,
                    "prompt_version": PROMPT_VERSION,
                    "fallback_models": list(getattr(provider, "fallback_models", ())),
                },
                length=48,
            )
            request = SemanticClusterRequest(
                layout_pattern_id=pattern.layout_pattern_id,
                structural_summary=summary,
                preview_paths=tuple(item.path for item in selected_previews),
                preview_hashes=tuple(item.sha256 for item in selected_previews),
                cache_key=cache_key,
            )
            cached = self.semantic_cache.get(cache_key) if self.semantic_cache else None
            if cached and cached.label.layout_pattern_id == pattern.layout_pattern_id:
                completed[pattern.layout_pattern_id] = cached
                metrics.cache_hits += 1
                work.finish("layouts", 1, cached=True)
            else:
                pending.append(request)
        batch_method = getattr(provider, "label_clusters", None)
        size = self.config.layout_batch_size if callable(batch_method) else 1
        jobs = [tuple(pending[offset:offset + size]) for offset in range(0, len(pending), size)]

        def describe(requests):
            results = (call_provider(provider, batch_method, requests) if callable(batch_method)
                       else [call_provider(provider, provider.label_cluster, requests[0])])
            missing = validate_subset([item.label for item in results], [item.layout_pattern_id for item in requests], "layout_pattern_id")
            if self.semantic_cache:
                keys = {item.layout_pattern_id: item.cache_key for item in requests}
                for result in results:
                    self.semantic_cache.put(keys[result.label.layout_pattern_id], result)
            if missing:
                raise IncompleteBatchError(results, missing)
            return results

        before_requests = work.requests
        for requests, results, error in work.jobs("layouts", jobs, describe, lambda batch: [item.layout_pattern_id for item in batch]):
            if error is not None:
                if mode == "required" and not work.exhausted and not isinstance(error, IncompleteBatchError):
                    raise error
                metrics.warnings.append(f"Semantic labeling failed for {', '.join(item.layout_pattern_id for item in requests)}; deterministic data retained: {error}")
                if results is None:
                    continue
            for result in results:
                completed[result.label.layout_pattern_id] = result
                metrics.input_tokens += result.input_tokens
                metrics.output_tokens += result.output_tokens
        metrics.requests = work.requests - before_requests
        for pattern in template.layout_patterns:
            result = completed.get(pattern.layout_pattern_id)
            if result:
                pattern.semantic_type = result.label.semantic_type
                pattern.semantic_description = result.label.short_description
                pattern.semantic_confidence = result.label.confidence
                pattern.semantic_source = provider.provider_name
        return metrics

    @staticmethod
    def _rules_total(template: TemplateModel) -> int:
        return (
            len(template.global_rules)
            + len(template.layout_patterns)
            + sum(len(item.slots) for item in template.layout_patterns)
            + len(template.recurring_elements)
            + len(template.design_system.typography)
            + len(template.design_system.colors)
            + len(template.design_system.backgrounds)
            + len(template.design_system.alignment_guides)
            + len(template.design_system.spacing_rules)
            + len(template.design_system.images)
            + len(template.design_system.tables)
            + len(template.design_system.decorative_patterns)
        )
