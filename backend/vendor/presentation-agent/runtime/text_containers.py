"""Source-derived text container bounds and axis-aligned group transforms."""
from runtime.security import NS
from runtime.geometry import box, intersection, inside

def axis_aligned(node):
    transforms=node.xpath('./p:spPr/a:xfrm|ancestor::p:grpSp/p:grpSpPr/a:xfrm',namespaces=NS)
    return all(all(t.get(k) in (None,'0','false') for k in ('rot','flipH','flipV')) for t in transforms)

def container_for(slot, items, root):
    from runtime.safe_text_expansion import plain_container
    index=next((i for i,o in enumerate(items) if o.get('shape_id')==slot['shapeId'] and o.get('source_level','slide')=='slide'),None)
    if index is None:return None
    candidates=[]
    for item in items[:index]:
        if item.get('hidden') or not plain_container(item,root):continue
        nodes=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(item['shape_id']))
        if not nodes or nodes[0].find('p:spPr/a:noFill',NS) is not None:continue
        b=box(item)
        # Short rails and touching decor are not owners. A slightly overlong
        # frame can still belong to a card when most of it lies inside.
        if b['w']<=0 or b['h']<=0 or slot['w']<=0 or slot['h']<=0:continue
        overlap=intersection(slot,b)/(slot['w']*slot['h'])
        if overlap>=.72 and b['x']<=slot['x']+.1 and b['y']<=slot['y']+.1:
            candidates.append((b['w']*b['h'],dict(b,shapeId=item['shape_id'])))
    return min(candidates,key=lambda v:v[0])[1] if candidates else None

def apply_page_box(node, before, after):
    """Convert absolute-page deltas to local group coordinates, retaining xfrm."""
    if not axis_aligned(node):raise ValueError('pptx_geometry_transform_unsupported')
    xf=node.find('p:spPr/a:xfrm',NS)
    off=xf.find('a:off',NS);ext=xf.find('a:ext',NS)
    for pos,size,attr in [('x','w','cx'),('y','h','cy')]:
        scale=int(ext.get(attr))/before[size]
        off.set(pos,str(round(int(off.get(pos))+(after[pos]-before[pos])*scale)))
        ext.set(attr,str(round(after[size]*scale)))

def normalize_frame_bounds(layout, frames, context, alignments=None, fields=None):
    """Clamp transparent source frames to their actual card/page, not the text.
    Text content is untouched; the ordinary fitter and AI handle remaining fit.
    """
    from copy import deepcopy
    from runtime.geometry_repair import editable
    result=deepcopy(frames);changes={};root=context.roots[layout['id']]
    for slot in layout['slots']:
        if not editable(root,slot,context,layout['id']):continue
        old={k:slot[k] for k in ('x','y','w','h')}
        owner=container_for(slot,context.slides[layout['id']],root)
        boundary=owner or context.page
        x=max(old['x'],boundary['x'],0);y=max(old['y'],boundary['y'],0)
        right=min(old['x']+old['w'],boundary['x']+boundary['w'],context.page['w'])
        bottom=min(old['y']+old['h'],boundary['y']+boundary['h'],context.page['h'])
        b=dict(x=x,y=y,w=right-x,h=bottom-y)
        choice=(alignments or {}).get(slot['key'],{});value=(fields or {}).get(slot['key'],'').strip()
        solo=owner and not any(other['key']!=slot['key'] and intersection(other,owner)>other['w']*other['h']*.5 for other in layout['slots'])
        centered=bool(solo and choice.get('vertical')=='center' and len(choice.get('reason',''))>=5 and value and len(value)<=160 and len(value.split('\n'))<=3 and not any(line.lstrip().startswith(('•','-')) for line in value.split('\n')))
        aligned=False
        if centered and inside(owner,context.page) and owner['w']*owner['h']<context.page['w']*context.page['h']*.8 and owner['h']>=slot.get('size',12)*2.2:
            margin=min(6,owner['h']*.08)
            target=dict(b,y=owner['y']+margin,h=owner['h']-2*margin)
            # Require a sole label and no new picture/chart/text collision.
            safe=all(item.get('shape_id') in (slot['shapeId'],owner['shapeId']) or item.get('object_kind')=='group' or item.get('hidden') or intersection(target,box(item))<=intersection(old,box(item))+.1 for item in context.slides[layout['id']])
            if safe:b=target;aligned=True
        if b['w']<max(12,slot.get('size',12)) or b['h']<5:continue
        if all(abs(b[k]-old[k])<.1 for k in b):continue
        key=slot['key'];frame=result[key]
        result[key]={**frame,'width_emu':round(frame['width_emu']*b['w']/old['w']),'height_emu':round(frame['height_emu']*b['h']/old['h']),'visible_width_emu':None}
        changes[key]=dict(shapeId=slot['shapeId'],**b,before=old,reason='ai_container_alignment' if aligned else 'source_container_bounds')
    return result,changes
