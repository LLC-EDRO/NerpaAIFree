"""Recover zero-height content areas in transparent, top-anchored text boxes.

Some slide exporters leave a noAutofit title shorter than its own insets.
PowerPoint paints the line outside the rectangle. Increase only the invisible
working-copy rectangle; text origin, width, font and source package stay fixed.
"""
import math
from lxml import etree
from runtime.security import xml, NS
from runtime.text_frames import source_text_frame, TemplateTextFitter


def recover_textbox_bounds(parts, shapes):
    result = dict(parts)
    changes = []
    roots = {}
    fitter = TemplateTextFitter()
    for shape in shapes:
        if shape.object_kind != 'shape' or not shape.rich_text or not shape.text_frame or shape.hidden:
            continue
        frame = source_text_frame(shape)
        if frame.height_emu > frame.top_emu + frame.bottom_emu or frame.reason_codes:
            continue
        text = '\n'.join(''.join(r.text for r in p.runs) for p in shape.rich_text.paragraphs).strip()
        if not text or '\n' in text or len([p for p in frame.paragraphs if p.has_text]) != 1:
            continue
        root = roots.setdefault(shape.source_part, xml(parts[shape.source_part]))
        matches = root.xpath('.//p:sp[p:nvSpPr/p:cNvPr[@id=$id]]',namespaces=NS,id=str(shape.shape_id))
        if len(matches) != 1:
            continue
        sp = matches[0]
        prop, body = sp.find('p:spPr',NS), sp.find('p:txBody/a:bodyPr',NS)
        if prop is None or body is None or sp.getparent().tag == '{%s}grpSp' % NS['p']:
            continue
        geom, transform = prop.find('a:prstGeom',NS), prop.find('a:xfrm',NS)
        if (geom is None or geom.get('prst') != 'rect' or transform is None
            or transform.get('rot','0') != '0' or transform.get('flipV','0') not in ('0','false')
            or prop.find('a:noFill',NS) is None
            or (prop.find('a:ln',NS) is not None and prop.find('a:ln/a:noFill',NS) is None)
            or body.get('anchor','t') != 't' or body.get('vert','horz') != 'horz'
            or body.find('a:noAutofit',NS) is None):
            continue
        extent = transform.find('a:ext',NS)
        if extent is None:
            continue
        # Establish one line's actual font height. The fitter has a conservative
        # horizontal wrap margin; it must not invent extra lines here. New text
        # is still checked with the original wrapping and width during filling.
        probe = fitter.fit(frame.model_copy(update={'height_emu': 10**9, 'wrap': False}), [text])
        if probe.status == 'unverifiable' or not probe.paragraphs or probe.paragraphs[0].line_count != 1:
            continue
        height = math.ceil(frame.top_emu + frame.bottom_emu + probe.measured_height_emu + 12700)
        changes.append(dict(part=shape.source_part,shapeId=shape.shape_id,fromHeightEmu=int(extent.get('cy')),toHeightEmu=height))
        extent.set('cy',str(height))
    for part in {c['part'] for c in changes}:
        result[part]=etree.tostring(roots[part],xml_declaration=True,encoding='UTF-8',standalone=True)
    return result, changes
