"""Small namespace-tolerant helpers for lxml nodes."""

from __future__ import annotations

from collections.abc import Iterable

from lxml import etree


def local_name(node: etree._Element | None) -> str:
    if node is None or not isinstance(node.tag, str):
        return ""
    # OOXML elements overwhelmingly use Clark notation (``{uri}name``).
    # Avoid constructing millions of QName wrapper objects while walking
    # shape-heavy decks; the fallback keeps namespace-free XML supported.
    tag = node.tag
    closing = tag.rfind("}")
    return tag[closing + 1 :] if closing >= 0 else tag.rsplit(":", 1)[-1]


def first_child(node: etree._Element | None, name: str) -> etree._Element | None:
    if node is None:
        return None
    return next((child for child in node if local_name(child) == name), None)


def children(node: etree._Element | None, name: str) -> list[etree._Element]:
    if node is None:
        return []
    return [child for child in node if local_name(child) == name]


def first_descendant(node: etree._Element | None, names: str | Iterable[str]) -> etree._Element | None:
    if node is None:
        return None
    accepted = {names} if isinstance(names, str) else set(names)
    # Let libxml2 filter tags in C instead of visiting every descendant in
    # Python and allocating its local-name string. This is material for slides
    # containing thousands of grouped vector shapes.
    tags = tuple(f"{{*}}{name}" for name in accepted)
    return next((item for item in node.iter(*tags) if item is not node), None)


def descendants(node: etree._Element | None, name: str) -> list[etree._Element]:
    if node is None:
        return []
    return [item for item in node.iter(f"{{*}}{name}") if item is not node]


def int_attr(node: etree._Element | None, name: str, default: int | None = None) -> int | None:
    if node is None:
        return default
    raw = node.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def float_attr(node: etree._Element | None, name: str, default: float | None = None) -> float | None:
    if node is None:
        return default
    raw = node.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def bool_attr(node: etree._Element | None, name: str, default: bool = False) -> bool:
    if node is None:
        return default
    raw = (node.get(name) or "").lower()
    if not raw:
        return default
    return raw in {"1", "true", "on", "yes"}


def xml_string(node: etree._Element) -> str:
    return etree.tostring(node, encoding="unicode", with_tail=False)
