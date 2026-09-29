"""EMU-preserving shape and group geometry."""

from __future__ import annotations

import math
from dataclasses import dataclass

from lxml import etree

from app.presentation.models import GeometryFullModel, GeometryModel, GroupTransformStep, NormalizedBBox
from app.presentation.parser.constants import DEFAULT_LINE_WIDTH_EMU, DEFAULT_SLIDE_HEIGHT_EMU, DEFAULT_SLIDE_WIDTH_EMU
from app.presentation.parser.xml_utils import bool_attr, first_child, int_attr


@dataclass(slots=True)
class Xfrm:
    off_x: int = 0
    off_y: int = 0
    ext_cx: int = 0
    ext_cy: int = 0
    ch_off_x: int = 0
    ch_off_y: int = 0
    ch_ext_cx: int = 1
    ch_ext_cy: int = 1
    rotation: float = 0.0
    flip_h: bool = False
    flip_v: bool = False


@dataclass(slots=True)
class TransformFrame:
    x: float
    y: float
    cx: float
    cy: float
    ch_off_x: float
    ch_off_y: float
    ch_ext_cx: float
    ch_ext_cy: float
    rotation: float
    flip_h: bool
    flip_v: bool


def parse_xfrm(node: etree._Element | None) -> Xfrm | None:
    if node is None:
        return None
    off, ext = first_child(node, "off"), first_child(node, "ext")
    ch_off, ch_ext = first_child(node, "chOff"), first_child(node, "chExt")
    return Xfrm(
        off_x=int_attr(off, "x", 0) or 0,
        off_y=int_attr(off, "y", 0) or 0,
        ext_cx=int_attr(ext, "cx", 0) or 0,
        ext_cy=int_attr(ext, "cy", 0) or 0,
        ch_off_x=int_attr(ch_off, "x", 0) or 0,
        ch_off_y=int_attr(ch_off, "y", 0) or 0,
        ch_ext_cx=int_attr(ch_ext, "cx", int_attr(ext, "cx", 1)) or 1,
        ch_ext_cy=int_attr(ch_ext, "cy", int_attr(ext, "cy", 1)) or 1,
        rotation=(int_attr(node, "rot", 0) or 0) / 60_000,
        flip_h=bool_attr(node, "flipH"),
        flip_v=bool_attr(node, "flipV"),
    )


def identity_frame(width: int = DEFAULT_SLIDE_WIDTH_EMU, height: int = DEFAULT_SLIDE_HEIGHT_EMU) -> TransformFrame:
    return TransformFrame(0, 0, width, height, 0, 0, width, height, 0, False, False)


def _rotate(x: float, y: float, cx: float, cy: float, degrees: float) -> tuple[float, float]:
    if not degrees:
        return x, y
    radians = math.radians(degrees)
    cosine, sine = math.cos(radians), math.sin(radians)
    dx, dy = x - cx, y - cy
    return cx + dx * cosine - dy * sine, cy + dx * sine + dy * cosine


def _aabb(points: list[tuple[float, float]]) -> GeometryModel:
    xs, ys = [point[0] for point in points], [point[1] for point in points]
    min_x, max_x, min_y, max_y = min(xs), max(xs), min(ys), max(ys)
    return GeometryModel(
        x_emu=round(min_x),
        y_emu=round(min_y),
        width_emu=max(0, round(max_x - min_x)),
        height_emu=max(0, round(max_y - min_y)),
    )


def map_child_to_absolute(local: Xfrm, parent: TransformFrame) -> GeometryModel:
    scale_x = parent.cx / parent.ch_ext_cx if parent.ch_ext_cx else 1
    scale_y = parent.cy / parent.ch_ext_cy if parent.ch_ext_cy else 1
    x = parent.x + (local.off_x - parent.ch_off_x) * scale_x
    y = parent.y + (local.off_y - parent.ch_off_y) * scale_y
    width, height = local.ext_cx * scale_x, local.ext_cy * scale_y
    corners = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
    if local.flip_h:
        pivot = x + width / 2
        corners = [(2 * pivot - px, py) for px, py in corners]
    if local.flip_v:
        pivot = y + height / 2
        corners = [(px, 2 * pivot - py) for px, py in corners]
    if parent.flip_h:
        pivot = parent.x + parent.cx / 2
        corners = [(2 * pivot - px, py) for px, py in corners]
    if parent.flip_v:
        pivot = parent.y + parent.cy / 2
        corners = [(px, 2 * pivot - py) for px, py in corners]
    total_rotation = parent.rotation + local.rotation
    if total_rotation:
        box = _aabb(corners)
        cx = (box.x_emu or 0) + (box.width_emu or 0) / 2
        cy = (box.y_emu or 0) + (box.height_emu or 0) / 2
        corners = [_rotate(px, py, cx, cy, total_rotation) for px, py in corners]
    return _aabb(corners)


def push_group_frame(parent: TransformFrame, local: Xfrm) -> TransformFrame:
    box = map_child_to_absolute(local, parent)
    return TransformFrame(
        box.x_emu or 0,
        box.y_emu or 0,
        box.width_emu or 0,
        box.height_emu or 0,
        local.ch_off_x,
        local.ch_off_y,
        local.ch_ext_cx or 1,
        local.ch_ext_cy or 1,
        parent.rotation + local.rotation,
        parent.flip_h != local.flip_h,
        parent.flip_v != local.flip_v,
    )


def normalize_bbox(box: GeometryModel | None, width: int, height: int) -> NormalizedBBox | None:
    if box is None or not width or not height:
        return None
    return NormalizedBBox(
        x=(box.x_emu or 0) / width,
        y=(box.y_emu or 0) / height,
        width=(box.width_emu or 0) / width,
        height=(box.height_emu or 0) / height,
    )


def classify_off_canvas(
    box: GeometryModel | None, width: int, height: int, allow_degenerate: bool = False
) -> tuple[str | None, bool, bool]:
    if box is None or width <= 0 or height <= 0:
        return None, False, False
    x, y = (box.x_emu or 0) / width, (box.y_emu or 0) / height
    w, h = (box.width_emu or 0) / width, (box.height_emu or 0) / height
    right, bottom = x + w, y + h
    positive = w > 0 and h > 0
    intersects = (
        (right >= 0 and bottom >= 0 and x <= 1 and y <= 1)
        if allow_degenerate
        else (right > 0 and bottom > 0 and x < 1 and y < 1 and positive)
    )
    inside = x >= 0 and y >= 0 and right <= 1 and bottom <= 1 and (allow_degenerate or positive)
    if inside:
        return None, False, False
    if intersects:
        overflow = max(0, -x, right - 1, -y, bottom - 1)
        return ("intentional_bleed" if overflow <= 0.03 else "partially_visible"), True, False
    far = right < -0.15 or x > 1.15 or bottom < -0.15 or y > 1.15
    return ("suspicious_off_canvas" if far else "fully_off_canvas"), False, True


LINE_PRESETS = {
    "line",
    "straightConnector1",
    "bentConnector2",
    "bentConnector3",
    "bentConnector4",
    "bentConnector5",
    "curvedConnector2",
    "curvedConnector3",
    "curvedConnector4",
    "curvedConnector5",
}


def is_segment(kind: str, preset: str | None) -> bool:
    return kind in {"connector", "line"} or bool(preset and (preset in LINE_PRESETS or "connector" in preset.lower()))


def _painted_segment(box: GeometryModel, stroke_emu: int) -> GeometryModel:
    half = max(stroke_emu / 2, 1)
    return GeometryModel(
        x_emu=round((box.x_emu or 0) - half),
        y_emu=round((box.y_emu or 0) - half),
        width_emu=round((box.width_emu or 0) + 2 * half),
        height_emu=round((box.height_emu or 0) + 2 * half),
    )


def build_geometry(
    xfrm: Xfrm | None,
    parent: TransformFrame,
    z_index: int,
    width: int,
    height: int,
    chain: list[TransformFrame],
    *,
    kind: str,
    preset: str | None,
    stroke_width_emu: int = 0,
) -> GeometryFullModel:
    chain_models = [
        GroupTransformStep(
            x_emu=f.x,
            y_emu=f.y,
            width_emu=f.cx,
            height_emu=f.cy,
            rotation_deg=f.rotation,
            flip_h=f.flip_h,
            flip_v=f.flip_v,
        )
        for f in chain
    ]
    if xfrm is None:
        return GeometryFullModel(
            z_index=z_index,
            group_transform_chain=chain_models,
            nesting_depth=len(chain),
            rotation_deg=parent.rotation,
            flip_h=parent.flip_h,
            flip_v=parent.flip_v,
        )
    local = GeometryModel(x_emu=xfrm.off_x, y_emu=xfrm.off_y, width_emu=xfrm.ext_cx, height_emu=xfrm.ext_cy)
    absolute = map_child_to_absolute(xfrm, parent)
    segment = is_segment(kind, preset)
    painted = _painted_segment(absolute, stroke_width_emu or DEFAULT_LINE_WIDTH_EMU) if segment else absolute
    off_class, clipped, off_canvas = classify_off_canvas(painted, width, height, allow_degenerate=segment)
    area = ((painted.width_emu or 0) * (painted.height_emu or 0)) / (width * height) if width and height else 0
    return GeometryFullModel(
        local_bbox=local,
        absolute_bbox=absolute,
        normalized_bbox=normalize_bbox(absolute, width, height),
        rotation_deg=parent.rotation + xfrm.rotation,
        flip_h=parent.flip_h != xfrm.flip_h,
        flip_v=parent.flip_v != xfrm.flip_v,
        z_index=z_index,
        painted_area_ratio=min(1, max(0, area)),
        group_transform_chain=chain_models,
        nesting_depth=len(chain),
        off_canvas_class=off_class,
        clipped=clipped,
        painted_bbox=painted,
        painted_normalized_bbox=normalize_bbox(painted, width, height),
        painted_intersects_canvas=not off_canvas,
        visibility_geometry_type="segment" if segment else "area",
    )
