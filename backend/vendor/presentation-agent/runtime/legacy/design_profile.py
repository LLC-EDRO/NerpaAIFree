"""Legacy v1 source-derived design envelope for saved compositions only. No model-generated geometry or XML.

The supplied slide stays the canvas. Only local content may be replaced; brand
art, masters and layout parts are never modified. Uncertain objects reserve space.
"""
import hashlib
import json
from collections import Counter


from runtime.geometry import box, intersection, inside


def free_rect(bounds, obstacles, padding=10):
    # Split only at source object edges, not an approximate raster grid.
    candidates = [bounds]
    for raw in obstacles:
        o = dict(x=raw['x']-padding, y=raw['y']-padding, w=raw['w']+2*padding, h=raw['h']+2*padding)
        next_rects = []
        for c in candidates:
            if intersection(c, o) < .01:
                next_rects.append(c)
                continue
            for x,y,w,h in [(c['x'],c['y'],o['x']-c['x'],c['h']),
                            (o['x']+o['w'],c['y'],c['x']+c['w']-o['x']-o['w'],c['h']),
                            (c['x'],c['y'],c['w'],o['y']-c['y']),
                            (c['x'],o['y']+o['h'],c['w'],c['y']+c['h']-o['y']-o['h'])]:
                r=dict(x=x,y=y,w=w,h=h)
                if w>0 and h>0 and inside(r,c): next_rects.append(r)
        candidates=sorted(next_rects,key=lambda r:r['w']*r['h'],reverse=True)[:80]
    return max(candidates,key=lambda r:r['w']*r['h'],default=None)


def luminance(color):
    values=[int(color[i:i+2],16)/255 for i in (1,3,5)]
    values=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in values]
    return sum(v*w for v,w in zip(values,(.2126,.7152,.0722)))


def contrast(a,b):
    x,y=sorted([luminance(a),luminance(b)])
    return (y+.05)/(x+.05)


def blend(a,b,ratio):
    return '#'+''.join(f'{round(int(a[i:i+2],16)*ratio+int(b[i:i+2],16)*(1-ratio)):02X}' for i in (1,3,5))


def design_profile(data, layout, items, assignment, photos, background_color=None):
    if not layout['usable'] or not layout['slots']: return None
    # Tables carry row/column relationships. Keep the established native table
    # writer instead of flattening evidence into a qualitative composition.
    if any(s.get('cell') is not None for s in layout['slots']):return None
    width,height=data['width'],data['height']
    headers=[s for s in layout['slots'] if not s.get('cell') and s['role']=='header']
    heading=max(headers or [s for s in layout['slots'] if not s.get('cell')],key=lambda s:s['size'],default=None)
    if not heading:return None
    cover=layout['index']==0 and heading['y']+heading['h']>height*.31 and any(o.get('object_kind')=='picture' and box(o)['w']*box(o)['h']>=width*height*.85 for o in items)
    # Only a cover with a verified full-canvas background can use its central
    # negative space. Partial photo compositions and section dividers stay native.
    if not cover and heading['y']+heading['h']>height*.31:return None
    title={k:heading[k] for k in ('x','y','w','h')}
    remove={s['shapeId'] for s in layout['slots']}|(set() if cover else set(photos))|{c['shapeId'] for c in layout.get('charts',[])}
    roles={r['source_open_xml_shape_id']:r['role'] for r in assignment if r['source_level']=='slide'}
    obstacles=[]
    for o in items:
        if o.get('hidden') or o.get('children') or o.get('shape_id') in remove: continue
        b=box(o)
        if b['w']<=0 or b['h']<=0: continue
        # Full-canvas backgrounds are behind content. Every other retained shape
        # is a protected obstacle, even if the analyzer only calls it decoration.
        if b['w']*b['h']>=width*height*.85: continue
        if b['y']+b['h']<=title['y']+title['h']+1:
            if roles.get(o.get('shape_id')) in ('logo','icon'):
                title=free_rect(title,[b],12) or title
            continue
        obstacles.append(b)
    for r in assignment:
        if r['source_level']=='slide' or r['role'] not in ('logo','footer','page_number','decoration','background_image'): continue
        g=r.get('source_geometry') or {}
        b=box({'geometry':g})
        if b['w']>0 and b['h']>0 and b['w']*b['h']<width*height*.85: obstacles.append(b)
    margin=width*.05
    top=max(height*.19,heading['y']+heading['h']+height*.045)
    bounds=dict(x=margin,y=top,w=width-2*margin,h=height*.92-top)
    if cover:bounds=dict(x=margin,y=height*.32,w=width-2*margin,h=height*.5)
    area=free_rect(bounds,obstacles, max(8,width*.01))
    if not area or area['w']<width*.36 or area['h']<height*.28 or area['w']*area['h']<width*height*.19: return None
    body=[s for s in layout['slots'] if s['key']!=heading['key'] and not s.get('cell') and s['role']!='page_number']
    font=Counter(s['font'] for s in body).most_common(1)[0][0] if body else heading['font']
    palette=[c.upper() for c in data.get('styleProfile',{}).get('colors',[]) if isinstance(c,str) and len(c)==7 and c.startswith('#')]
    background=background_color or (data.get('styleProfile',{}).get('backgrounds') or ['#FFFFFF'])[0]
    # Use the local background when available, then inherited template colour.
    for o in items:
        if box(o)['w']*box(o)['h']>=width*height*.85 and (o.get('fill') or {}).get('color'):
            background=o['fill']['color']
    # A raster photo background has no single trustworthy contrast colour.
    # Keep the source-native composition unless it is the recognised cover.
    full=[o for o in items if box(o)['w']*box(o)['h']>=width*height*.85]
    if not cover and any(o.get('object_kind')=='picture' for o in full) and not any((o.get('fill') or {}).get('color') for o in full):return None
    ink=next((s['color'] for s in body if contrast(s['color'],background)>=4.5),None)
    ink=ink or max(palette or ['#000000','#FFFFFF'],key=lambda c:contrast(c,background))
    accents=[c for c in palette if max(int(c[i:i+2],16) for i in (1,3,5))-min(int(c[i:i+2],16) for i in (1,3,5))>35 and contrast(c,background)>=3]
    accent=accents[0] if accents else ink
    on_accent=max([background,ink,'#FFFFFF','#000000'],key=lambda c:contrast(c,accent))
    style=dict(font=font,headingFont=heading['font'],titleSize=heading['size'],titleBold=heading['bold'],titleColor=heading['color'],
               bodySize=min(24,max(18,round((Counter(s['size'] for s in body).most_common(1) or [(20,1)])[0][0]))),
               background=background,ink=ink,accent=accent,onAccent=on_accent,surface=blend(accent,background,.07),muted=blend(ink,background,.76))
    if cover:
        title=dict(x=area['x'],y=area['y'],w=area['w'],h=area['h']*.69)
        area=dict(x=area['x'],y=area['y']+area['h']*.76,w=area['w'],h=area['h']*.24)
        style['ink']=heading['color'];style['muted']=heading['color']
    # Text and its painted shape are separate responsibilities. A white title
    # inside a coloured box needs that source-native box after replacing text.
    painted_title=next((o for o in items if o.get('shape_id')==heading['shapeId'] and (o.get('fill') or {}).get('color')),None)
    result=dict(version=1,cover=cover,bodyBox=area,titleBox=title,style=style,removeShapeIds=sorted(remove),protectedBoxes=obstacles,
                preserveTitleShapeId=heading['shapeId'] if painted_title else None,
                canAddImage=not cover and area['w']>=width*.58 and area['h']>=height*.32)
    result['fingerprint']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()
    return result
