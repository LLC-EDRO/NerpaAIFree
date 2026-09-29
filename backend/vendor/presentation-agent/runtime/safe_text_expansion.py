"""Bounded, source-derived growth of plain native text boxes.

No model-provided coordinates are accepted. Planning fit and export call the
same deterministic solver. Unknown transforms, cells and decorated shapes keep
their original geometry. Existing output without the opt-in flag is unchanged.
"""
import json
import math
from copy import deepcopy
from runtime.geometry import box, inside, intersection, rectangular_geometry
from runtime.security import NS, xml, read_package
from runtime.fonts import effective_source
from runtime.text_frames import TemplateTextFrame

EMU = 12700


def objects(items):
    for item in items:
        yield item
        yield from objects(item.get('children', []))


class ExpansionContext:
    def __init__(self, folder, data):
        path = folder / 'effective-parser.json'
        from runtime.model_store import read_model
        model = read_model(path if path.exists() else folder / 'parser.json')
        self.slides = {s['slide_id']: list(objects(s['objects'])) for s in model['slides']}
        parts = read_package(effective_source(folder / 'source.pptx', folder, data).read_bytes())
        self.roots = {l['id']: xml(parts[l['part']]) for l in data['layouts']}
        self.page = dict(x=0, y=0, w=data['width'], h=data['height'])
        self.parts = parts
        self.source_shapes = {o['source_object_id']: (o.get('source_part'),o.get('shape_id'))
                              for kind in ('masters','layouts','slides') for container in model.get(kind,[])
                              for o in objects(container.get('objects',[]) if kind=='slides' else container.get('placeholders',[])) if o.get('source_object_id')}

    def transparent_placeholder(self, layout_id, shape_id):
        from runtime.frame_appearance import transparent_inheritance
        item=next((o for o in self.slides[layout_id] if o.get('shape_id')==shape_id and o.get('source_level')=='slide'),None)
        if not item or not item.get('placeholder_type'):return False
        chain=[]
        for layer in (item.get('text_frame') or {}).get('style_layers',[]):
            source=layer.get('source_object_id')
            if not source:continue
            reference=self.source_shapes.get(source)
            if not reference or reference[0] not in self.parts:return False
            root=xml(self.parts[reference[0]])
            nodes=root.xpath('./p:cSld/p:spTree/p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(reference[1]))
            if len(nodes)!=1:return False
            chain.append(nodes[0])
        return bool(chain) and transparent_inheritance(chain)


def eligible(root, slot):
    if 'cell' in slot or slot.get('align','left')!='left':
        return False
    nodes = root.xpath('./p:cSld/p:spTree/p:sp[p:nvSpPr/p:cNvPr/@id=$id]', namespaces=NS, id=str(slot['shapeId']))
    if len(nodes) != 1:
        return False  # Inherited/grouped text requires a separate transform policy.
    node = nodes[0]
    transform = node.find('p:spPr/a:xfrm', NS)
    if transform is None or any(transform.get(k) not in (None, '0', 'false') for k in ('rot', 'flipH', 'flipV')):
        return False
    if transform.find('a:off', NS) is None or transform.find('a:ext', NS) is None:
        return False
    if node.find('p:style', NS) is not None:
        return False  # Theme effects/fill can change a visible shape when resized.
    if node.find('p:spPr/a:noFill',NS) is None:
        return False  # Absence of a fill may inherit a visible theme surface.
    if node.xpath('./p:spPr/*[not(self::a:xfrm or self::a:prstGeom[@prst="rect"] or self::a:noFill or self::a:ln[a:noFill] or self::a:effectLst[not(*)])]', namespaces=NS):
        return False
    body = node.find('p:txBody/a:bodyPr', NS)
    if node.xpath('./p:txBody//a:pPr[@algn and @algn!="l"]|./p:txBody/a:lstStyle/*[@algn and @algn!="l"]',namespaces=NS):
        return False  # Mixed alignment would move later paragraphs on growth.
    return body is not None and body.get('rot', '0') == '0' and body.get('vert', 'horz') == 'horz' and body.get('anchor','t')=='t'


def plain_container(item, root):
    """Only proven lower rectangular shape surfaces, never arbitrary pictures."""
    if item.get('object_kind') != 'shape' or any(r.get('text','').strip() for p in (item.get('rich_text') or {}).get('paragraphs',[]) for r in p.get('runs',[])):
        return False
    geometry=item.get('geometry_full') or {}
    if geometry.get('rotation_deg') or any(t.get('rotation_deg') for t in geometry.get('group_transform_chain', [])):
        return False
    nodes = root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]', namespaces=NS, id=str(item.get('shape_id')))
    return len(nodes) == 1 and not nodes[0].xpath('./p:spPr/a:blipFill|./p:spPr/a:effectDag|./p:spPr/a:effectLst/*|./p:spPr/a:xfrm[@rot!="0"]', namespaces=NS) and rectangular_geometry(nodes[0])


def expand_fields(layout, frames, fields, context, fitter):
    result = deepcopy(frames)
    changes = {}
    root = context.roots[layout['id']]
    items = context.slides[layout['id']]
    # Geometry is updated after each accepted change so two growing neighbours
    # cannot claim the same free region. Stable source order, not JSON order.
    current = {s['shapeId']: dict(x=s['x'], y=s['y'], w=s['w'], h=s['h']) for s in layout['slots'] if 'cell' not in s}
    for slot in layout['slots']:
        key = slot['key']
        text = fields.get(key, '')
        frame = TemplateTextFrame.model_validate(result[key])
        report = fitter.fit(frame, text.split('\n'))
        if report.status != 'overflow' or report.reason_codes != ['source_text_frame_overflow'] or not eligible(root, slot):
            continue
        original = current[slot['shapeId']]
        if not inside(original, context.page) or abs(frame.width_emu / EMU - original['w']) > .1 or abs(frame.height_emu / EMU - original['h']) > .1:
            continue
        source_index = next((i for i, o in enumerate(items) if o.get('source_level', 'slide') == 'slide' and o.get('shape_id') == slot['shapeId']), None)
        if source_index is None:
            continue
        boundary = dict(context.page)
        obstacles = []
        unsafe = False
        for i, item in enumerate(items):
            local = item.get('source_level', 'slide') == 'slide'
            if item.get('hidden') or local and item.get('shape_id') == slot['shapeId']:
                continue
            b = current.get(item.get('shape_id'), box(item)) if local else box(item)
            if item.get('object_kind') == 'group':
                continue  # The recursively flattened children remain obstacles.
            if b['w'] < 0 or b['h'] < 0:
                unsafe = True; break
            # A background container already supporting this text can bound
            # growth; an image containing raster labels cannot be inferred safe.
            if local and i < source_index and inside(original, b) and plain_container(item, root):
                left, top = max(boundary['x'], b['x']), max(boundary['y'], b['y'])
                boundary = dict(x=left, y=top, w=min(boundary['x']+boundary['w'], b['x']+b['w'])-left, h=min(boundary['y']+boundary['h'], b['y']+b['h'])-top)
                continue
            if intersection(original, b) > .1:
                unsafe = True; break  # Resizing cannot fix an existing overlay.
            obstacles.append(b)
        if unsafe:
            continue
        # Preserve x/y and all styles. Reserve a font-relative gap, bounded by
        # each existing source gap so a tightly designed layout is not rejected.
        gap = min(12, max(3, float(slot.get('size', 20)) * .2))
        max_width = boundary['x'] + boundary['w'] - original['x']
        widths = {original['w'], max_width}
        widths.update(b['x']-original['x']-min(gap, max(0, b['x']-original['x']-original['w'])) for b in obstacles if b['x'] >= original['x']+original['w'])
        # Dense templates stay bounded; narrow candidates first.
        widths = sorted(w for w in widths if original['w'] <= w <= max_width)
        if len(widths)>24:widths=widths[:23]+[widths[-1]]
        best = None
        for width in widths:
            height = boundary['y'] + boundary['h'] - original['y']
            for b in obstacles:
                if b['x'] < original['x']+width+gap and b['x']+b['w']+gap > original['x']:
                    if b['y'] >= original['y']+original['h']:
                        height = min(height, b['y']-original['y']-min(gap, b['y']-original['y']-original['h']))
                    elif b['y']+b['h'] > original['y']:
                        height = 0; break
            if height < original['h']:
                continue
            candidate = frame.model_copy(update={'width_emu': round(width*EMU), 'height_emu': round(height*EMU), 'visible_width_emu': None})
            measured = fitter.fit(candidate, text.split('\n'))
            if measured.status != 'fits':
                continue
            # Prefer preserving the original line band, then widen only as
            # much as needed. Testing only obstacle/page widths would stretch
            # a tiny sample title across the entire page or into a tall column.
            same_height=candidate.model_copy(update={'height_emu': frame.height_emu})
            if fitter.fit(same_height,text.split('\n')).status=='fits':
                lo,hi=original['w'],width
                for _ in range(12):
                    mid=(lo+hi)/2
                    if fitter.fit(same_height.model_copy(update={'width_emu':round(mid*EMU)}),text.split('\n')).status=='fits':hi=mid
                    else:lo=mid
                final_width=min(width,math.ceil(hi*20)/20)
                proposed=dict(original,w=final_width)
                fitted=same_height.model_copy(update={'width_emu':round(final_width*EMU)})
                score=(0,final_width-original['w'])
                if not any(intersection(proposed,b)>.1 for b in obstacles) and (best is None or score<best[0]):
                    best=(score,proposed,fitted)
                continue
            # Minimal successful height, retaining original insets and font.
            lo, hi = original['h'], height
            for _ in range(10):
                mid = (lo+hi)/2
                if fitter.fit(candidate.model_copy(update={'height_emu': round(mid*EMU)}), text.split('\n')).status == 'fits': hi = mid
                else: lo = mid
            # Pillow and Impress differ in baseline/descender placement. Newly
            # expanded frames reserve half a source-font line, never outside
            # the proven free rectangle. Existing fitting frames are untouched.
            leading = max((p.font_size_pt or 0 for p in frame.paragraphs), default=0) * .5
            final_height = min(height, math.ceil((hi+leading)*20)/20)
            proposed = dict(original, w=width, h=final_height)
            if any(intersection(proposed, b) > .1 for b in obstacles):
                continue
            score = (final_height-original['h'],width-original['w'])
            if best is None or score < best[0]:
                best = (score, proposed, candidate.model_copy(update={'height_emu': round(final_height*EMU)}))
        if best:
            _, proposed, measured_frame = best
            current[slot['shapeId']] = proposed
            changes[key] = dict(shapeId=slot['shapeId'], **proposed,
                                before=original,reason='source_text_frame_overflow')
            result[key] = measured_frame.model_dump()
    return result, changes


def expanded_layout(layout, changes):
    """Use the same accepted geometry for image safety and native text fit."""
    if not changes:return layout
    result=dict(layout)
    result['slots']=[{**s,**{k:changes[s['key']][k] for k in ('x','y','w','h')}} if s['key'] in changes and 'padding' not in changes[s['key']] else s for s in layout['slots']]
    return result


def expansion_report(expansions):
    return [dict(slide=index+1,key=key,shapeId=change['shapeId'],reason=change['reason'],
                 before=change['before'],after={k:change[k] for k in ('x','y','w','h')},units='pt',
                 **(dict(cell=change['cell'],paddingBeforeEmu=change['paddingBefore'],paddingAfterEmu=change['padding']) if 'padding' in change else {}))
            for index,changes in expansions.items() for key,change in changes.items()]


def apply_expansion(root, changes):
    for change in changes.values():
        if 'padding' in change:continue
        from runtime.text_containers import apply_page_box
        nodes=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(change['shapeId']))
        if len(nodes)!=1:raise ValueError('pptx_expansion_shape_changed')
        apply_page_box(nodes[0],change['before'],change)
