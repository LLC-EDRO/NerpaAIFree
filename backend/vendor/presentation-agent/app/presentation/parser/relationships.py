"""OPC relationship graph resolution."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.presentation.models import RelationshipModel
from app.presentation.parser.archive import PptxArchive, relationship_part, resolve_target
from app.presentation.parser.constants import NS


@dataclass(slots=True)
class RelationshipGraph:
    part: str
    edges: list[RelationshipModel] = field(default_factory=list)
    by_id: dict[str, RelationshipModel] = field(default_factory=dict)

    def by_type_suffix(self, suffix: str) -> RelationshipModel | None:
        return next((edge for edge in self.edges if (edge.relationship_type or "").endswith(suffix)), None)


def load_relationships(archive: PptxArchive, source_part: str) -> RelationshipGraph:
    graph = RelationshipGraph(part=source_part)
    root = archive.xml(relationship_part(source_part))
    if root is None:
        return graph
    for node in root.xpath("./pr:Relationship", namespaces=NS):
        rel_id = str(node.get("Id") or "")
        raw_target = str(node.get("Target") or "")
        mode = str(node.get("TargetMode") or "") or None
        target = raw_target if mode == "External" else resolve_target(source_part, raw_target)
        edge = RelationshipModel(
            source_part=source_part,
            relationship_id=rel_id,
            relationship_type=str(node.get("Type") or "") or None,
            target=target or None,
            target_mode=mode,
            broken=bool(mode != "External" and (not target or not archive.exists(target))),
        )
        graph.edges.append(edge)
        if rel_id:
            graph.by_id[rel_id] = edge
    return graph
