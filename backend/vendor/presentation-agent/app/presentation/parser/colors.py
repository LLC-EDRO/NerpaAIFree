"""Deterministic OOXML color and theme resolution."""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass, field

from lxml import etree

from app.presentation.models import (
    ColorValue,
    FmtSchemeModel,
    FmtSchemeStyle,
    ThemeFontFace,
    ThemeFontScheme,
    ThemeModel,
)
from app.presentation.parser.ids import theme_id_from_part
from app.presentation.parser.xml_utils import (
    first_child,
    first_descendant,
    int_attr,
    local_name,
    xml_string,
)

SCHEME_ALIASES = {"bg1": "lt1", "bg2": "lt2", "tx1": "dk1", "tx2": "dk2"}
PRESET_COLORS = {
    "black": "#000000",
    "white": "#FFFFFF",
    "red": "#FF0000",
    "green": "#00FF00",
    "blue": "#0000FF",
    "yellow": "#FFFF00",
    "cyan": "#00FFFF",
    "magenta": "#FF00FF",
    "gray": "#808080",
    "grey": "#808080",
    "darkblue": "#00008B",
    "darkred": "#8B0000",
    "darkgreen": "#006400",
}
COLOR_NODE_NAMES = {"srgbClr", "schemeClr", "sysClr", "prstClr", "scrgbClr"}


def normalize_hex(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip().lstrip("#")
    if len(raw) == 3 and all(ch in "0123456789abcdefABCDEF" for ch in raw):
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in raw):
        return None
    return f"#{raw.upper()}"


def _rgb(hex_color: str) -> list[float]:
    raw = hex_color.lstrip("#")
    return [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]


def _hex(rgb: list[float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(channel * 255))):02X}" for channel in rgb)


def _apply_transforms(base: str, node: etree._Element) -> tuple[str, float | None, list[dict[str, object]]]:
    rgb = _rgb(base)
    alpha: float | None = None
    trace: list[dict[str, object]] = []
    for transform in node:
        name = local_name(transform)
        raw = int_attr(transform, "val")
        ratio = raw / 100_000 if raw is not None else None
        hue, lightness, saturation = colorsys.rgb_to_hls(*rgb)
        if name in {"tint", "lumOff"} and ratio is not None:
            lightness = lightness * ratio + (1 - ratio) if name == "tint" else lightness + ratio
            rgb = list(colorsys.hls_to_rgb(hue, max(0, min(1, lightness)), saturation))
        elif name in {"shade", "lumMod"} and ratio is not None:
            lightness *= ratio
            rgb = list(colorsys.hls_to_rgb(hue, max(0, min(1, lightness)), saturation))
        elif name == "satMod" and ratio is not None:
            rgb = list(colorsys.hls_to_rgb(hue, lightness, max(0, min(1, saturation * ratio))))
        elif name == "satOff" and ratio is not None:
            rgb = list(colorsys.hls_to_rgb(hue, lightness, max(0, min(1, saturation + ratio))))
        elif name == "hueMod" and ratio is not None:
            rgb = list(colorsys.hls_to_rgb((hue * ratio) % 1, lightness, saturation))
        elif name == "hueOff" and raw is not None:
            rgb = list(colorsys.hls_to_rgb((hue + raw / 21_600_000) % 1, lightness, saturation))
        elif name == "alpha" and ratio is not None:
            alpha = max(0, min(1, ratio))
        elif name == "alphaMod" and ratio is not None:
            alpha = max(0, min(1, (alpha if alpha is not None else 1) * ratio))
        elif name == "alphaOff" and ratio is not None:
            alpha = max(0, min(1, (alpha if alpha is not None else 1) + ratio))
        elif name in {"red", "green", "blue"} and ratio is not None:
            rgb[{"red": 0, "green": 1, "blue": 2}[name]] = ratio
        elif name in {"redMod", "greenMod", "blueMod"} and ratio is not None:
            rgb[{"redMod": 0, "greenMod": 1, "blueMod": 2}[name]] *= ratio
        elif name in {"redOff", "greenOff", "blueOff"} and ratio is not None:
            rgb[{"redOff": 0, "greenOff": 1, "blueOff": 2}[name]] += ratio
        elif name == "inv":
            rgb = [1 - channel for channel in rgb]
        elif name == "comp":
            rgb = list(colorsys.hls_to_rgb((hue + 0.5) % 1, lightness, saturation))
        elif name == "gray":
            gray = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
            rgb = [gray, gray, gray]
        elif name == "gamma":
            rgb = [math.pow(max(0, min(1, channel)), 1 / 2.2) for channel in rgb]
        elif name == "invGamma":
            rgb = [math.pow(max(0, min(1, channel)), 2.2) for channel in rgb]
        else:
            continue
        trace.append({"step": "transform", "detail": f"{name}({raw})" if raw is not None else name})
    return _hex(rgb), alpha, trace


@dataclass(slots=True)
class ResolvedColor:
    value: str | None
    theme_color_ref: str | None = None
    source: str = "unresolved"
    alpha: float | None = None
    raw: dict[str, object] = field(default_factory=dict)
    trace: list[dict[str, object]] = field(default_factory=list)


def find_color_node(node: etree._Element | None) -> etree._Element | None:
    if node is None:
        return None
    if local_name(node) in COLOR_NODE_NAMES:
        return node
    return next((item for item in node.iterdescendants() if local_name(item) in COLOR_NODE_NAMES), None)


def resolve_color(
    node: etree._Element | None,
    theme_colors: dict[str, str],
    color_map: dict[str, str] | None = None,
) -> ResolvedColor:
    color = find_color_node(node)
    if color is None:
        return ResolvedColor(None)
    kind = local_name(color)
    value = color.get("val")
    theme_ref: str | None = None
    source = "unresolved"
    base: str | None = None
    if kind == "srgbClr":
        base, source = normalize_hex(value), "srgb"
    elif kind == "schemeClr":
        theme_ref, source = value, "scheme"
        mapped = (color_map or {}).get(value or "", value or "")
        mapped = SCHEME_ALIASES.get(mapped, mapped)
        base = None if value == "phClr" else normalize_hex(theme_colors.get(mapped))
    elif kind == "sysClr":
        base, source = normalize_hex(color.get("lastClr") or value), "sys"
    elif kind == "prstClr":
        base, source = PRESET_COLORS.get((value or "").lower()), "prst"
    elif kind == "scrgbClr":
        try:
            channels = [int(color.get(key) or 0) / 100_000 for key in ("r", "g", "b")]
            base, source = _hex(channels), "scrgb"
        except ValueError:
            base = None
    if base:
        base, alpha, trace = _apply_transforms(base, color)
    else:
        alpha, trace = None, []
    return ResolvedColor(
        value=base,
        theme_color_ref=theme_ref,
        source=source,
        alpha=alpha,
        raw={"type": kind, "value": value},
        trace=trace,
    )


def parse_color_map(root: etree._Element | None, *, override: bool = False) -> dict[str, str]:
    if root is None:
        return {}
    names = {"overrideClrMapping"} if override else {"clrMap"}
    node = next((item for item in root.iter() if local_name(item) in names), None)
    return dict(node.attrib) if node is not None else {}


def apply_color_map(base: dict[str, str], override: dict[str, str]) -> dict[str, str]:
    return {**base, **override}


def resolve_solid_fill(
    node: etree._Element | None, theme_colors: dict[str, str], color_map: dict[str, str] | None = None
) -> ResolvedColor:
    if node is None:
        return ResolvedColor(None)
    solid = next((item for item in node.iter() if local_name(item) == "solidFill"), None)
    return resolve_color(solid, theme_colors, color_map)


def resolve_line(
    node: etree._Element | None, theme_colors: dict[str, str], color_map: dict[str, str] | None = None
) -> tuple[ResolvedColor, float | None]:
    line = first_descendant(node, "ln")
    color = resolve_solid_fill(line, theme_colors, color_map)
    width = int_attr(line, "w")
    return color, width / 12_700 if width is not None else None


def parse_background(root: etree._Element | None, theme_colors: dict[str, str], color_map: dict[str, str], source: str):
    from app.presentation.models import BackgroundModel

    background = first_descendant(root, "bg")
    if background is None:
        return None
    resolved = resolve_solid_fill(background, theme_colors, color_map)
    return BackgroundModel(color=resolved.value, theme_color_ref=resolved.theme_color_ref, source=source)


def _font_face(node: etree._Element | None) -> ThemeFontFace:
    return ThemeFontFace(
        latin=(first_child(node, "latin").get("typeface") if first_child(node, "latin") is not None else None),
        ea=(first_child(node, "ea").get("typeface") if first_child(node, "ea") is not None else None),
        cs=(first_child(node, "cs").get("typeface") if first_child(node, "cs") is not None else None),
    )


def _fmt_scheme(root: etree._Element) -> FmtSchemeModel | None:
    node = first_descendant(root, "fmtScheme")
    if node is None:
        return None
    mapping = (
        ("fillStyleLst", "fill", "fill_styles"),
        ("lnStyleLst", "line", "line_styles"),
        ("effectStyleLst", "effect", "effect_styles"),
        ("bgFillStyleLst", "bgFill", "bg_fill_styles"),
    )
    payload: dict[str, object] = {"name": node.get("name")}
    for list_name, kind, field_name in mapping:
        container = first_child(node, list_name)
        payload[field_name] = [
            FmtSchemeStyle(idx=index + 1, kind=kind, xml=xml_string(child))
            for index, child in enumerate(list(container) if container is not None else [])
        ]
    return FmtSchemeModel(**payload)


def parse_theme(root: etree._Element, source_part: str) -> ThemeModel:
    scheme = first_descendant(root, "clrScheme")
    theme_colors: dict[str, str] = {}
    if scheme is not None:
        for role in scheme:
            resolved = resolve_color(role, {})
            if resolved.value:
                theme_colors[local_name(role)] = resolved.value
    font_scheme_node = first_descendant(root, "fontScheme")
    major = _font_face(first_child(font_scheme_node, "majorFont"))
    minor = _font_face(first_child(font_scheme_node, "minorFont"))
    fonts = list(dict.fromkeys(value for value in (major.latin, minor.latin) if value))
    return ThemeModel(
        theme_id=theme_id_from_part(source_part),
        source_part=source_part,
        color_scheme=list(dict.fromkeys(theme_colors.values())),
        fonts=fonts,
        theme_colors=theme_colors,
        major_font=major.latin,
        minor_font=minor.latin,
        major_font_ea=major.ea,
        minor_font_ea=minor.ea,
        major_font_cs=major.cs,
        minor_font_cs=minor.cs,
        font_scheme=ThemeFontScheme(major=major, minor=minor),
        fmt_scheme=_fmt_scheme(root),
    )


def resolve_typeface(value: str | None, theme: ThemeModel | None) -> str | None:
    if not value:
        return None
    aliases = {
        "+mj-lt": theme.major_font if theme else None,
        "+mn-lt": theme.minor_font if theme else None,
        "+mj-ea": theme.major_font_ea if theme else None,
        "+mn-ea": theme.minor_font_ea if theme else None,
        "+mj-cs": theme.major_font_cs if theme else None,
        "+mn-cs": theme.minor_font_cs if theme else None,
    }
    return aliases.get(value, value)


def color_value(resolved: ResolvedColor) -> ColorValue | None:
    if resolved.value is None and resolved.theme_color_ref is None:
        return None
    return ColorValue(color=resolved.value, theme_color_ref=resolved.theme_color_ref)
