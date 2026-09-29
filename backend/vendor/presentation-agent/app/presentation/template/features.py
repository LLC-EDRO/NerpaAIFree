"""Shared deterministic feature and evidence helpers."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections.abc import Iterable
from typing import Any

from app.presentation.models import GeometryModel, NormalizedBBox
from app.presentation.template.models import (
    GeometryStatistics,
    NormalizedGeometry,
    RuleEvidence,
    SourceGeometry,
)


def stable_hash(value: Any, *, length: int = 12) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def normalized_geometry(value: NormalizedBBox | dict[str, Any] | None) -> NormalizedGeometry | None:
    if value is None:
        return None
    data = value if isinstance(value, dict) else value.model_dump()
    return NormalizedGeometry(
        x=float(data["x"]),
        y=float(data["y"]),
        width=float(data["width"]),
        height=float(data["height"]),
    )


def source_geometry(value: GeometryModel | dict[str, Any] | None) -> SourceGeometry | None:
    if value is None:
        return None
    data = value if isinstance(value, dict) else value.model_dump()
    return SourceGeometry(
        x_emu=data.get("x_emu"),
        y_emu=data.get("y_emu"),
        width_emu=data.get("width_emu"),
        height_emu=data.get("height_emu"),
    )


def _geom_value(items: list[NormalizedGeometry], field: str, fn) -> float:
    return round(float(fn([getattr(item, field) for item in items])), 6)


def geometry_variance(items: Iterable[NormalizedGeometry]) -> float:
    values = list(items)
    if len(values) < 2:
        return 0.0
    deviations = []
    for field in ("x", "y", "width", "height"):
        deviations.append(statistics.pstdev(getattr(item, field) for item in values))
    return round(sum(deviations) / len(deviations), 6)


def geometry_statistics(
    geometries: Iterable[NormalizedGeometry],
    source_examples: Iterable[SourceGeometry | None] = (),
) -> GeometryStatistics:
    values = list(geometries)
    if not values:
        raise ValueError("Geometry statistics require at least one normalized geometry")

    def point(fn) -> NormalizedGeometry:
        return NormalizedGeometry(
            x=_geom_value(values, "x", fn),
            y=_geom_value(values, "y", fn),
            width=_geom_value(values, "width", fn),
            height=_geom_value(values, "height", fn),
        )

    stddev = NormalizedGeometry(
        x=round(statistics.pstdev(item.x for item in values), 6) if len(values) > 1 else 0.0,
        y=round(statistics.pstdev(item.y for item in values), 6) if len(values) > 1 else 0.0,
        width=round(statistics.pstdev(item.width for item in values), 6) if len(values) > 1 else 0.0,
        height=round(statistics.pstdev(item.height for item in values), 6) if len(values) > 1 else 0.0,
    )
    sources = [item for item in source_examples if item is not None][:5]
    return GeometryStatistics(
        representative=point(statistics.median),
        minimum=point(min),
        maximum=point(max),
        stddev=stddev,
        source_examples=sources,
    )


def evidence(
    *,
    category: str,
    source_type: str,
    slide_ids: Iterable[str] = (),
    element_ids: Iterable[str] = (),
    layout_ids: Iterable[str] = (),
    master_ids: Iterable[str] = (),
    sample_count: int,
    support_ratio: float,
    variance: dict[str, float] | None = None,
    details: dict[str, Any] | None = None,
) -> RuleEvidence:
    slides = sorted(set(slide_ids))
    elements = sorted(set(element_ids))
    layouts = sorted(item for item in set(layout_ids) if item)
    masters = sorted(item for item in set(master_ids) if item)
    identity = {
        "category": category,
        "source_type": source_type,
        "slides": slides,
        "elements": elements,
        "layouts": layouts,
        "masters": masters,
        "details": details or {},
    }
    return RuleEvidence(
        evidence_id=f"evidence_{category}_{stable_hash(identity, length=10)}",
        source_type=source_type,
        slide_ids=slides,
        element_ids=elements,
        layout_ids=layouts,
        master_ids=masters,
        sample_count=sample_count,
        support_ratio=round(min(max(support_ratio, 0.0), 1.0), 4),
        variance={key: round(float(value), 6) for key, value in (variance or {}).items()},
        details=details or {},
    )


def quantize(value: float, tolerance: float) -> int:
    return int(round(value / tolerance))


def geometry_distance(left: NormalizedGeometry, right: NormalizedGeometry) -> float:
    return (
        math.sqrt(
            (left.x - right.x) ** 2
            + (left.y - right.y) ** 2
            + (left.width - right.width) ** 2
            + (left.height - right.height) ** 2
        )
        / 2.0
    )
