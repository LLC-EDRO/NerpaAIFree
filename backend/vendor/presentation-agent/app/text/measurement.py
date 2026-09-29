"""Deterministic font resolution, measurement, wrapping, and bounded fitting."""

from __future__ import annotations

import hashlib
import math
import time
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from PIL import ImageFont

EMU_PER_INCH = 914_400

if TYPE_CHECKING:
    from app.composition.models import GeometrySpec, MarginsSpec, TextFitResult


@dataclass(frozen=True, slots=True)
class FontResolution:
    requested_family: str
    resolved_family: str
    path: Path | None
    status: str
    warning: str | None = None


class FontResolver:
    """Resolve installed fonts without bundling or redistributing font files."""

    FONT_ROOTS = (
        Path("/System/Library/Fonts"),
        Path("/Library/Fonts"),
        Path.home() / "Library/Fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
    )

    def __init__(self, fallback_families: tuple[str, ...] = ("Arial", "Aptos", "DejaVu Sans")) -> None:
        self.fallback_families = fallback_families
        self._font_index = self._build_index()

    @staticmethod
    def _normalize(value: str) -> str:
        return "".join(character for character in value.lower() if character.isalnum())

    @classmethod
    @lru_cache(maxsize=1)
    def _build_index(cls) -> dict[str, tuple[Path, ...]]:
        found: dict[str, list[Path]] = {}
        for root in cls.FONT_ROOTS:
            if not root.exists():
                continue
            for extension in ("*.ttf", "*.otf", "*.ttc"):
                for path in root.rglob(extension):
                    key = cls._normalize(path.stem)
                    found.setdefault(key, []).append(path)
        return {key: tuple(sorted(paths, key=lambda item: item.as_posix())) for key, paths in found.items()}

    def resolve(self, family: str | None, *, bold: bool = False, italic: bool = False) -> FontResolution:
        requested = (family or "").strip() or self.fallback_families[0]
        exact = self._find(requested, bold=bold, italic=italic)
        if exact is not None:
            return FontResolution(requested, requested, exact, "exact")
        for fallback in self.fallback_families:
            candidate = self._find(fallback, bold=bold, italic=italic)
            if candidate is not None:
                return FontResolution(
                    requested,
                    fallback,
                    candidate,
                    "substituted",
                    f"Font '{requested}' is unavailable; measured with '{fallback}'.",
                )
        return FontResolution(
            requested,
            "Pillow default",
            None,
            "estimated",
            f"Font '{requested}' is unavailable; Pillow default metrics were used.",
        )

    def _find(self, family: str, *, bold: bool, italic: bool) -> Path | None:
        target = self._normalize(family)
        candidates = [
            path
            for key, paths in self._font_index.items()
            if key == target or key.startswith(target) or target.startswith(key)
            for path in paths
        ]
        if not candidates:
            return None

        def score(path: Path) -> tuple[int, str]:
            name = self._normalize(path.stem)
            has_bold = "bold" in name or "semibold" in name or "demi" in name
            has_italic = "italic" in name or "oblique" in name
            mismatch = int(has_bold != bold) + int(has_italic != italic)
            return mismatch, path.as_posix()

        return min(candidates, key=score)

    @property
    def environment_fingerprint(self) -> str:
        inventory = "\n".join(
            path.as_posix()
            for key in sorted(self._font_index)
            for path in self._font_index[key]
        )
        return hashlib.sha256(inventory.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class Measurement:
    width_emu: int
    height_emu: int
    line_count: int
    lines: tuple[str, ...]
    method: str


class TextMeasurementService:
    def __init__(
        self,
        *,
        resolver: FontResolver | None = None,
        dpi: int = 144,
        safety_margin: float = 0.06,
    ) -> None:
        self.resolver = resolver or FontResolver()
        self.dpi = dpi
        self.safety_margin = safety_margin
        self._cache: dict[tuple[object, ...], Measurement] = {}
        self._metrics_lock = Lock()
        self._duration_ms = 0.0

    @property
    def duration_ms(self) -> float:
        with self._metrics_lock:
            return self._duration_ms

    def measure(
        self,
        text: str,
        *,
        family: str,
        font_size_pt: float,
        available_width_emu: int,
        bold: bool = False,
        italic: bool = False,
        wrap: bool = True,
        line_spacing: float = 1.0,
    ) -> tuple[Measurement, FontResolution]:
        started = time.perf_counter()
        resolution = self.resolver.resolve(family, bold=bold, italic=italic)
        key = (
            text,
            resolution.path.as_posix() if resolution.path else None,
            round(font_size_pt, 3),
            available_width_emu,
            bold,
            italic,
            wrap,
            round(line_spacing, 3),
            self.dpi,
        )
        cached = self._cache.get(key)
        if cached is not None:
            self._record_duration(started)
            return cached, resolution
        pixel_size = max(1, round(font_size_pt * self.dpi / 72))
        try:
            font = (
                ImageFont.truetype(str(resolution.path), pixel_size)
                if resolution.path
                else ImageFont.load_default(size=pixel_size)
            )
            method = "pillow_installed_font" if resolution.path else "pillow_default_font"
        except OSError:
            font = ImageFont.load_default(size=pixel_size)
            method = "pillow_default_font"
            resolution = FontResolution(
                resolution.requested_family,
                "Pillow default",
                None,
                "estimated",
                f"Resolved font '{resolution.resolved_family}' could not be opened; default metrics were used.",
            )
        max_width_px = max(1.0, available_width_emu / EMU_PER_INCH * self.dpi)
        lines = self._wrap(text, font, max_width_px) if wrap else tuple(text.splitlines() or [""])
        widths = [self._text_width(line, font) for line in lines] or [0.0]
        ascent, descent = font.getmetrics()
        line_height_px = max(1.0, (ascent + descent) * line_spacing)
        measurement = Measurement(
            width_emu=round(max(widths) / self.dpi * EMU_PER_INCH),
            height_emu=round(len(lines) * line_height_px / self.dpi * EMU_PER_INCH),
            line_count=len(lines),
            lines=lines,
            method=method,
        )
        self._cache[key] = measurement
        self._record_duration(started)
        return measurement, resolution

    def _record_duration(self, started: float) -> None:
        elapsed_ms = (time.perf_counter() - started) * 1000
        with self._metrics_lock:
            self._duration_ms += elapsed_ms

    def fit(
        self,
        text: str,
        *,
        geometry: GeometrySpec,
        margins: MarginsSpec,
        family: str,
        preferred_font_size_pt: float,
        min_font_size_pt: float,
        max_lines: int | None,
        bold: bool = False,
        italic: bool = False,
        wrap: bool = True,
        line_spacing: float = 1.0,
        minimum_line_spacing: float = 0.9,
    ) -> TextFitResult:
        from app.composition.models import TextFitResult

        available_width = max(0, geometry.width_emu - margins.left_emu - margins.right_emu)
        available_height = max(0, geometry.height_emu - margins.top_emu - margins.bottom_emu)
        repairs: list[str] = []
        selected_size = preferred_font_size_pt
        selected_spacing = line_spacing
        measurement, resolution = self.measure(
            text,
            family=family,
            font_size_pt=selected_size,
            available_width_emu=available_width,
            bold=bold,
            italic=italic,
            wrap=wrap,
            line_spacing=selected_spacing,
        )

        def fits(value: Measurement) -> bool:
            safe_width = round(available_width * (1.0 - self.safety_margin))
            safe_height = round(available_height * (1.0 - self.safety_margin))
            return (
                value.width_emu <= safe_width
                and value.height_emu <= safe_height
                and (max_lines is None or value.line_count <= max_lines)
            )

        if not fits(measurement) and selected_spacing > minimum_line_spacing:
            selected_spacing = minimum_line_spacing
            repairs.append("reduced_line_spacing_within_allowed_range")
            measurement, resolution = self.measure(
                text,
                family=family,
                font_size_pt=selected_size,
                available_width_emu=available_width,
                bold=bold,
                italic=italic,
                wrap=wrap,
                line_spacing=selected_spacing,
            )
        while not fits(measurement) and selected_size - 0.5 >= min_font_size_pt:
            selected_size = round(selected_size - 0.5, 2)
            measurement, resolution = self.measure(
                text,
                family=family,
                font_size_pt=selected_size,
                available_width_emu=available_width,
                bold=bold,
                italic=italic,
                wrap=wrap,
                line_spacing=selected_spacing,
            )
        if selected_size < preferred_font_size_pt:
            repairs.append("reduced_font_size_within_minimum")
        did_fit = fits(measurement)
        max_safe_width = round(available_width * (1.0 - self.safety_margin))
        max_safe_height = round(available_height * (1.0 - self.safety_margin))
        ratio = max(
            measurement.width_emu / max(1, max_safe_width),
            measurement.height_emu / max(1, max_safe_height),
            measurement.line_count / max_lines if max_lines else 0.0,
        )
        estimate = "overflow" if not did_fit else "near_limit" if ratio >= 0.9 else "fits"
        warnings = [resolution.warning] if resolution.warning else []
        if not did_fit:
            warnings.append("Text exceeds the available geometry; no content was truncated.")
        return TextFitResult(
            fits=did_fit,
            fit_estimate=estimate,
            selected_font_size_pt=selected_size,
            preferred_font_size_pt=preferred_font_size_pt,
            min_font_size_pt=min_font_size_pt,
            estimated_width_emu=measurement.width_emu,
            estimated_height_emu=measurement.height_emu,
            available_width_emu=available_width,
            available_height_emu=available_height,
            line_count=measurement.line_count,
            max_lines=max_lines,
            overflow_x_emu=max(0, measurement.width_emu - max_safe_width),
            overflow_y_emu=max(0, measurement.height_emu - max_safe_height),
            safety_margin=self.safety_margin,
            repairs_applied=repairs,
            measurement_method=measurement.method,
            font_resolution_status=resolution.status,  # type: ignore[arg-type]
            warnings=warnings,
        )

    @staticmethod
    def _text_width(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> float:
        left, _top, right, _bottom = font.getbbox(text or " ")
        return max(0.0, right - left)

    def _wrap(
        self,
        text: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width_px: float,
    ) -> tuple[str, ...]:
        lines: list[str] = []
        for paragraph in text.splitlines() or [""]:
            words = paragraph.split(" ")
            current = ""
            for word in words:
                candidate = word if not current else f"{current} {word}"
                if self._text_width(candidate, font) <= max_width_px:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                if self._text_width(word, font) <= max_width_px:
                    current = word
                    continue
                fragments = self._split_long_word(word, font, max_width_px)
                lines.extend(fragments[:-1])
                current = fragments[-1] if fragments else ""
            lines.append(current)
        return tuple(lines or [""])

    def _split_long_word(
        self,
        word: str,
        font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
        max_width_px: float,
    ) -> list[str]:
        result: list[str] = []
        current = ""
        for character in word:
            if current and self._text_width(current + character, font) > max_width_px:
                result.append(current)
                current = character
            else:
                current += character
        if current or not result:
            result.append(current)
        return result


def text_from_items(items: Iterable[str]) -> str:
    return "\n".join(f"• {item}" for item in items)


def estimate_target_pixels(geometry: GeometrySpec, dpi: int) -> tuple[int, int]:
    return (
        max(1, math.ceil(geometry.width_emu / EMU_PER_INCH * dpi)),
        max(1, math.ceil(geometry.height_emu / EMU_PER_INCH * dpi)),
    )
