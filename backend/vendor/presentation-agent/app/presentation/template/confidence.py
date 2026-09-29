"""Transparent reproducible confidence scoring for inferred template rules."""

from __future__ import annotations

from app.presentation.template.models import ConfidenceScore


def score_confidence(
    *,
    sample_size: int,
    support_ratio: float,
    variance: float = 0.0,
    consistency: float = 1.0,
    conflicts: float = 0.0,
    direct_evidence: bool = False,
) -> ConfidenceScore:
    support = min(max(support_ratio, 0.0), 1.0)
    stable = min(max(consistency, 0.0), 1.0)
    variance_penalty = min(max(variance / 0.05, 0.0), 1.0)
    conflict_penalty = min(max(conflicts, 0.0), 1.0)
    sample_factor = min(max(sample_size, 0) / 5.0, 1.0)
    value = (
        0.42 * support
        + 0.25 * stable
        + 0.18 * (1.0 - variance_penalty)
        + 0.15 * sample_factor
        - 0.20 * conflict_penalty
    )
    if sample_size <= 1 and not direct_evidence:
        value = min(value, 0.62)
    elif sample_size == 2 and not direct_evidence:
        value = min(value, 0.78)
    value = round(min(max(value, 0.0), 0.99), 4)
    return ConfidenceScore(
        score=value,
        sample_size=sample_size,
        support_ratio=round(support, 4),
        consistency=round(stable, 4),
        variance_penalty=round(variance_penalty, 4),
        conflict_penalty=round(conflict_penalty, 4),
        rationale=(
            f"support={support:.3f}; samples={sample_size}; consistency={stable:.3f}; "
            f"variance_penalty={variance_penalty:.3f}; conflict_penalty={conflict_penalty:.3f}"
        ),
    )


def score_assignment_confidence(
    *,
    cluster_distance: float,
    distance_threshold: float,
    alternative_distance: float | None,
    pattern_sample_size: int,
    source_layout_match: bool,
    source_layout_available: bool,
) -> ConfidenceScore:
    """Score how well one observed slide fits its selected visual pattern.

    This deliberately does not use global pattern prevalence. A layout variant
    observed once can still be assigned with high confidence when the slide is
    the exact representative and competing patterns are structurally distant.
    Pattern generalizability continues to use ``score_confidence`` separately.
    """

    threshold = max(distance_threshold, 1e-6)
    distance = max(cluster_distance, 0.0)
    normalized_distance = min(distance / (threshold * 2.0), 1.0)
    fit = 1.0 - normalized_distance
    if alternative_distance is None:
        separation = 1.0
        conflict_penalty = 0.0
    else:
        gap = max(alternative_distance - distance, 0.0)
        separation = min(gap / threshold, 1.0)
        conflict_penalty = 1.0 - separation
    lineage = 1.0 if source_layout_match else 0.7 if source_layout_available else 0.4
    sample_factor = min(max(pattern_sample_size, 0) / 3.0, 1.0)
    value = 0.50 * fit + 0.20 * separation + 0.25 * lineage + 0.05 * sample_factor
    value = round(min(max(value, 0.0), 0.99), 4)
    return ConfidenceScore(
        score=value,
        sample_size=pattern_sample_size,
        support_ratio=round(fit, 4),
        consistency=round(separation, 4),
        variance_penalty=round(normalized_distance, 4),
        conflict_penalty=round(conflict_penalty, 4),
        rationale=(
            f"assignment_fit={fit:.3f}; separation={separation:.3f}; "
            f"cluster_distance={distance:.3f}; "
            f"alternative_distance={alternative_distance:.3f}; "
            f"source_layout_match={str(source_layout_match).lower()}; "
            f"pattern_samples={pattern_sample_size}"
            if alternative_distance is not None
            else (
                f"assignment_fit={fit:.3f}; separation=1.000; "
                f"cluster_distance={distance:.3f}; alternative_distance=none; "
                f"source_layout_match={str(source_layout_match).lower()}; "
                f"pattern_samples={pattern_sample_size}"
            )
        ),
    )


def score_pattern_confidence(
    *,
    sample_size: int,
    deck_prevalence: float,
    internal_distance: float,
    distance_threshold: float,
) -> ConfidenceScore:
    """Score whether an observed visual variant can generalize as a pattern.

    Deck prevalence is reported as evidence, not treated as correctness: a
    specialized layout may legitimately occur once in a large template deck.
    Repetition and internal structural stability drive generalizability.
    """

    threshold = max(distance_threshold, 1e-6)
    prevalence = min(max(deck_prevalence, 0.0), 1.0)
    stability_penalty = min(max(internal_distance / (threshold * 2.0), 0.0), 1.0)
    stability = 1.0 - stability_penalty
    evidence_sufficiency = min(max(sample_size, 0) / 3.0, 1.0)
    value = 0.50 * stability + 0.30 * evidence_sufficiency + 0.20
    if sample_size <= 1:
        value = min(value, 0.78)
    elif sample_size == 2:
        value = min(value, 0.88)
    value = round(min(max(value, 0.0), 0.99), 4)
    return ConfidenceScore(
        score=value,
        sample_size=sample_size,
        support_ratio=round(prevalence, 4),
        consistency=round(stability, 4),
        variance_penalty=round(stability_penalty, 4),
        conflict_penalty=0.0,
        rationale=(
            f"pattern_stability={stability:.3f}; samples={sample_size}; "
            f"evidence_sufficiency={evidence_sufficiency:.3f}; "
            f"deck_prevalence={prevalence:.3f}; "
            f"mean_structural_distance={internal_distance:.3f}"
        ),
    )
