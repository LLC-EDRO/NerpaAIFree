"""Content budgets inside native frames, without resizing any PPTX object.

A card's text frame may cover its entire background while icons and captions
occupy its lower half. Those reserved areas are not available for body copy.
"""
from runtime.geometry import box
from runtime.security import NS
from runtime.text_frames import TemplateTextFrame

EMU = 12700

def reserved_regions(items, roles):
    return [dict(shapeId=o['shape_id'], role='chart' if o.get('object_kind')=='chart' else roles[o['shape_id']], box=box(o))
            for o in items if not o.get('hidden') and o.get('source_level', 'slide') == 'slide'
            and (roles.get(o.get('shape_id')) in ('icon', 'logo') or o.get('object_kind')=='chart')
            and box(o)['w'] > 0 and box(o)['h'] > 0]

def text_limits(slots, reserved, root):
    limits = {}
    for slot in slots:
        if 'cell' in slot: continue  # Tables own their separate cell geometry.
        nodes = root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]', namespaces=NS, id=str(slot['shapeId']))
        if len(nodes) != 1: continue
        node = nodes[0]
        body = node.find('p:txBody/a:bodyPr', NS)
        # Reducing a centred/bottom/rotated frame changes its anchor. Such text
        # is checked using its real rendered position instead of a guessed fit.
        if body is None or body.get('anchor', 't') != 't' or body.get('vert', 'horz') != 'horz' or body.get('rot', '0') != '0': continue
        if node.xpath('./p:spPr/a:xfrm[@rot!="0"]|ancestor::p:grpSp/p:grpSpPr/a:xfrm[@rot!="0"]', namespaces=NS): continue
        obstacles = [dict(shapeId=s['shapeId'], key=s['key'], role='text', box=s) for s in slots if s['key'] != slot['key'] and 'cell' not in s]
        obstacles += reserved
        gap = min(4, max(1, slot.get('size', 20) * .15))
        candidates = []
        for obstacle in obstacles:
            b = obstacle['box']
            if obstacle['shapeId'] == slot['shapeId']: continue
            overlap = min(slot['x']+slot['w'], b['x']+b['w']) - max(slot['x'], b['x'])
            if overlap <= 1 or b['y'] <= slot['y'] or b['y'] >= slot['y']+slot['h']: continue
            height = b['y'] - slot['y'] - gap
            if height >= slot.get('size', 20) * 1.2:
                candidates.append((height, obstacle))
        if candidates:
            height, obstacle = min(candidates, key=lambda pair: pair[0])
            limits[slot['key']] = dict(x=slot['x'], y=slot['y'], w=slot['w'], h=height,
                                      blockerShapeId=obstacle['shapeId'], blockerRole=obstacle['role'])
    return limits

def constrained_frame(frame, slot, limit, fitter):
    """Fit-only height in local coordinates. Original XML/style stays intact."""
    source = TemplateTextFrame.model_validate(frame)
    if not limit or slot['h'] <= 0: return source
    changes={'height_emu': round(source.height_emu * limit['h'] / slot['h'])}
    if limit.get('explicitLines') and slot['w']>0:
        # After a real native resize, ordinary wrapping is correct if the
        # entire content area fits before the obstacle (including insets).
        scale=slot['w']/max(1,source.width_emu)
        content_left=slot['x']+source.left_emu*scale
        content_right=slot['x']+slot['w']-source.right_emu*scale
        if content_left<limit['x']-.01 or content_right>limit['x']+limit['w']+.01:
            width=max(0,round(source.width_emu*limit['w']/slot['w'])-source.left_emu-source.right_emu)
            changes['visible_width_emu']=min(width,source.visible_width_emu) if source.visible_width_emu is not None else width
    restricted = source.model_copy(update=changes)
    # Do not invent a stricter geometry for an intentional source composition.
    # The final PDF still checks actual text/icon and text/text collisions.
    if limit.get('blockerRole')!='foreground_picture' and slot.get('text', '').strip() and fitter.fit(restricted, slot['text'].split('\n')).status != 'fits': return source
    return restricted
