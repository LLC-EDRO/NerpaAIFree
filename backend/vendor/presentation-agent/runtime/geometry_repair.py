"""Model-proposed frame growth, validated against native objects before export."""
import math
from copy import deepcopy
from runtime.security import NS
from runtime.geometry import box, inside, intersection
from runtime.safe_text_expansion import plain_container, EMU
from runtime.text_containers import axis_aligned, container_for


def editable(root, slot, context=None, layout_id=None):
    if 'cell' in slot or slot.get('role') in ('page_number','brand','decoration','footer','logo'):
        return False
    nodes=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]', namespaces=NS,id=str(slot['shapeId']))
    if len(nodes)!=1:return False
    node=nodes[0]
    transform=node.find('p:spPr/a:xfrm',NS)
    body=node.find('p:txBody/a:bodyPr',NS)
    return (transform is not None and transform.find('a:off',NS) is not None and transform.find('a:ext',NS) is not None
        and axis_aligned(node)
        and body is not None and body.get('vert','horz')=='horz' and body.get('rot','0')=='0'
        and (node.find('p:spPr/a:noFill',NS) is not None or (context is not None and context.transparent_placeholder(layout_id,slot['shapeId']))) and node.find('p:style',NS) is None
        and not node.xpath('./p:spPr/*[not(self::a:xfrm or self::a:prstGeom[@prst="rect"] or self::a:noFill or self::a:ln[a:noFill] or self::a:effectLst[not(*)])]',namespaces=NS))


def repair_context(layout, context, keys):
    root=context.roots[layout['id']]
    fields=[]
    for slot in layout['slots']:
        if slot['key'] not in keys or not editable(root,slot,context,layout['id']):continue
        node=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(slot['shapeId']))[0]
        fields.append(dict(key=slot['key'],shapeId=slot['shapeId'],box={k:slot[k] for k in ('x','y','w','h')},
            align=slot.get('align'),anchor=node.find('p:txBody/a:bodyPr',NS).get('anchor','t'),fontSize=slot.get('size'),container=container_for(slot,context.slides[layout['id']],root)))
    neighbours=[]
    for item in context.slides[layout['id']]:
        if item.get('hidden') or item.get('object_kind')=='group':continue
        b=box(item)
        if any(intersection(dict(x=f['box']['x']-40,y=f['box']['y']-40,w=f['box']['w']+80,h=f['box']['h']+80),b)>0 for f in fields):
            neighbours.append(dict(shapeId=item.get('shape_id'),kind=item.get('object_kind'),box=b))
    return dict(page=context.page,fields=fields,neighbours=neighbours[:100],neighboursTruncated=len(neighbours)>100,
                rules='Coordinates in pt. Resize or move the transparent text frame locally to free space. Keep fonts and alignment. Reduce existing overlaps; never introduce new overlaps. No page overflow or tables. Axis-aligned nested groups use page coordinates; their local transforms are preserved. Server measures the proposed text in the new frame.')


def apply_repairs(layout, frames, proposals, context):
    if not proposals:return frames,{},[]
    result=deepcopy(frames);changes={};issues=[]
    root=context.roots[layout['id']];items=context.slides[layout['id']]
    slots={s['key']:s for s in layout['slots']}
    def reject(key,reason):issues.append(dict(key=key,reason='geometry_repair_rejected',details=[reason]))
    if not isinstance(proposals,dict) or len(proposals)>12:
        return frames,{},[dict(reason='geometry_repair_rejected',details=['invalid_proposals'])]
    for key,b in proposals.items():
        slot=slots.get(key)
        if not slot or not editable(root,slot,context,layout['id']):reject(key,'unsupported_frame');continue
        old={k:slot[k] for k in ('x','y','w','h')}
        if not isinstance(b,dict) or set(b)!={'x','y','w','h'} or any(type(v) not in (int,float) or not math.isfinite(v) for v in b.values()):
            reject(key,'invalid_coordinates');continue
        if b['w']<=0 or b['h']<=0 or not inside(b,context.page):
            reject(key,'must_stay_inside_page');continue
        if intersection(old,b)<=0 and (abs(b['x']-old['x'])>36 or abs(b['y']-old['y'])>max(72,old['h'])):
            reject(key,'source_anchor_lost');continue
        # A narrow title must not become a huge empty panel through an AI guess.
        if b['w']>max(old['w']*4,old['w']+144) or b['h']>max(old['h']*4,old['h']+72):
            reject(key,'excessive_growth');continue
        index=next((i for i,o in enumerate(items) if o.get('source_level','slide')=='slide' and o.get('shape_id')==slot['shapeId']),None)
        if index is None:reject(key,'missing_source_object');continue
        conflict=None
        owner=container_for(slot,items,root)
        if owner and not inside(b,owner):reject(key,'outside_container');continue
        for i,item in enumerate(items):
            local=item.get('source_level','slide')=='slide'
            if item.get('hidden') or item.get('object_kind')=='group' or local and item.get('shape_id')==slot['shapeId']:continue
            old_other=box(item)
            other_key=next((s['key'] for s in slots.values() if local and s['shapeId']==item.get('shape_id')),None)
            other=proposals.get(other_key,old_other)
            if not isinstance(other,dict) or set(other)!={'x','y','w','h'} or any(type(v) not in (int,float) or not math.isfinite(v) for v in other.values()):other=old_other
            if i<index and local and plain_container(item,root) and (inside(old,old_other) or owner and owner['shapeId']==item.get('shape_id')):
                if not inside(b,other):conflict='outside_container';break
                continue
            before=intersection(old,old_other);after=intersection(b,other)
            if after>before+.1:conflict='new_overlap';break
        if conflict:reject(key,conflict);continue
        result[key]={**result[key],'width_emu':round(result[key]['width_emu']*b['w']/old['w']),'height_emu':round(result[key]['height_emu']*b['h']/old['h']),'visible_width_emu':None}
        changes[key]=dict(shapeId=slot['shapeId'],**b,before=old,reason='ai_geometry_repair')
    # Atomic: do not partly apply a proposal whose other fields failed safety.
    return (frames,{},issues) if issues else (result,changes,[])
