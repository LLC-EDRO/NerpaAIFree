"""Parser evidence and deterministic color-usage metrics."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from typing import Any

from app.presentation.models import ColorUsageMetric, EvidenceRecord


class EvidenceStore:
    def __init__(self) -> None:
        self.records: list[EvidenceRecord] = []

    def add(self, **payload: Any) -> None:
        stable = "|".join(
            str(payload.get(key) or "") for key in ("property", "value", "scope", "source_part", "object_id", "z_index")
        )
        self.records.append(
            EvidenceRecord(evidence_id=f"evidence_{hashlib.sha1(stable.encode()).hexdigest()[:16]}", **payload)
        )

    def snapshot(self) -> list[EvidenceRecord]:
        return list(self.records)


def color_usage_metrics(records: list[EvidenceRecord], slide_count: int) -> list[ColorUsageMetric]:
    by_color: dict[str, list[EvidenceRecord]] = defaultdict(list)
    for record in records:
        if isinstance(record.value, str) and record.value.startswith("#"):
            by_color[record.value].append(record)
    result: list[ColorUsageMetric] = []
    for value, items in sorted(by_color.items()):
        slides = {item.slide_id for item in items if item.slide_id}
        layouts = {item.layout_id for item in items if item.layout_id}
        counts = Counter(item.property for item in items)
        area = sum(item.area or 0 for item in items)
        concentration = max(
            Counter(item.slide_id or item.layout_id or item.master_id for item in items).values()
        ) / len(items)
        coverage = len(slides) / slide_count if slide_count else 0
        result.append(
            ColorUsageMetric(
                value=value,
                occurrences=len(items),
                unique_slides=len(slides),
                unique_layouts=len(layouts),
                concentration=concentration,
                line_count=counts["shape.line"],
                fill_count=counts["shape.fill"],
                text_count=counts["text.color"],
                approx_visual_area=area,
                slide_coverage=coverage,
                coverage_score=min(1, coverage + min(area, 1) * 0.25),
                concentration_penalty=max(0, concentration - 0.75),
            )
        )
    return result
