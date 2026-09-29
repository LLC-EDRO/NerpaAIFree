"""Native DrawingML text rectangles, not the shape's outer bounding box.

Preset definitions: https://raw.githubusercontent.com/plutext/docx4j/master/
docx4j-core/src/main/resources/org/docx4j/model/shapes/presetShapeDefinitions.xml
Only known presets with literal adjustments are supported. Source XML stays
unchanged; custom paths/formulas are never approximated as rectangles.
"""
import math
import re
from runtime.security import NS, xml


class ShapeTextGeometry:
    def __init__(self, parts):
        self.parts = parts
        self.roots = {}

    def insets(self, shape, width, height):
        kind = shape.text_frame.shape_geometry if shape.text_frame else None
        defaults = {'ellipse': {}, 'roundRect': {'adj': 16667}, 'round1Rect': {'adj': 16667},
                    'round2SameRect': {'adj1': 16667, 'adj2': 0},
                    'round2DiagRect': {'adj1': 16667, 'adj2': 0}}
        if kind not in defaults or min(width, height) <= 0:
            return None
        identities = [(shape.source_part, shape.shape_id)]
        for layer in reversed(shape.text_frame.style_layers):
            match = re.search(r'_obj_(\d+)$', layer.source_object_id or '')
            if match:
                identities.append((layer.source_part, int(match[1])))
        geometry = None
        for part, identity in dict.fromkeys(identities):
            if part not in self.parts or identity is None:
                continue
            if part not in self.roots:
                self.roots[part] = xml(self.parts[part])
            nodes = self.roots[part].xpath('.//p:sp[p:nvSpPr/p:cNvPr[@id=$id]]/p:spPr/*[self::a:prstGeom or self::a:custGeom]', namespaces=NS, id=str(identity))
            if len(nodes) > 1:
                return None
            if nodes:
                geometry = nodes[0]
                break
        if geometry is None or geometry.tag != '{%s}prstGeom' % NS['a'] or geometry.get('prst') != kind:
            return None
        values = dict(defaults[kind])
        seen = set()
        for guide in geometry.findall('a:avLst/a:gd', NS):
            name = guide.get('name')
            match = re.fullmatch(r'val\s+(-?\d+)', guide.get('fmla', ''))
            if name not in values or name in seen or not match:
                return None
            values[name] = min(50000, max(0, int(match[1])))
            seen.add(name)
        if kind == 'ellipse':
            # Native ellipse text rectangle: hc +/- wd2*cos(45deg),
            # vc +/- hd2*sin(45deg). Use local axes, including non-circles.
            dx = math.ceil(width * (1 - math.sqrt(.5)) / 2)
            dy = math.ceil(height * (1 - math.sqrt(.5)) / 2)
            return (dx, dy, dx, dy)
        def inset(value):
            return math.ceil(min(width, height) * value * 29289 / 10_000_000_000)
        if kind == 'round1Rect':
            return (0, 0, inset(values['adj']), 0)
        if kind == 'round2SameRect':
            top, bottom = inset(values['adj1']), inset(values['adj2'])
            return (max(top, bottom), top, max(top, bottom), bottom)
        value = inset(max(values.values()))
        return (value, value, value, value)
