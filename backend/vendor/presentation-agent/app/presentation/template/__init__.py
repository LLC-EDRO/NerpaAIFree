"""Deterministic and optionally semantic presentation template analysis."""

from app.presentation.template.analyzer import (
    PreviewInput,
    TemplateAnalysisResult,
    TemplateAnalyzer,
    TemplateAnalyzerConfig,
    TemplateInputError,
)
from app.presentation.template.models import (
    ContentClass,
    ElementSemanticDecision,
    ReplacementPolicy,
    TemplateAnalysisContext,
    TemplateModel,
    TemplateReadinessReport,
)

__all__ = [
    "PreviewInput",
    "TemplateAnalysisResult",
    "TemplateAnalyzer",
    "TemplateAnalyzerConfig",
    "TemplateInputError",
    "ContentClass",
    "ReplacementPolicy",
    "ElementSemanticDecision",
    "TemplateAnalysisContext",
    "TemplateReadinessReport",
    "TemplateModel",
]
