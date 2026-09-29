"""Proven image instructions inside native shape frames, not visual guessing.
No template names, slide ordinals or fixed object identities are used here.
"""
import re
from runtime.geometry import box,inside,intersection
from runtime.security import xml,NS


def image_instruction(text):
    text=re.sub(r'\s+',' ',text).strip().casefold().strip('[]():.! ')
    return bool(re.fullmatch(r'(?:вставить(?: сюда)?|добавить(?: сюда)?|место для) (?:фото|фотографию|фотографии|изображение|изображения|картинку|картинки)(?: сюда)?|(?:insert|add|place) (?:an? )?(?:photo|image|picture)(?: here)?|(?:photo|image|picture) placeholder',text))


def labelled_image_frames(items,layout,roles,parts):
    items=[o for o in items if o.get('source_level','slide')=='slide' and not o.get('hidden')]
    labels=[s for s in layout['slots'] if image_instruction(s.get('text','')) and 'cell' not in s]
    result={}
    for label in labels:
        candidates=[]
        for item in items:
            identity=item['shape_id']
            if item.get('object_kind')!='shape' or roles.get(identity) in ('logo','icon','footer','page_number'):continue
            geometry=item.get('geometry_full') or {}
            if geometry.get('rotation_deg') or any(t.get('rotation_deg') for t in geometry.get('group_transform_chain',[])):continue
            b=box(item)
            if b['w']<=0 or b['h']<=0 or not inside(label,b):continue
            root=xml(parts[item['source_part']])
            nodes=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(identity))
            if len(nodes)!=1:continue
            node=nodes[0]
            if not node.xpath('./p:spPr/a:prstGeom[@prst="rect" or @prst="roundRect"]',namespaces=NS):continue
            if not node.xpath('./p:spPr/a:solidFill',namespaces=NS):continue
            if node.xpath('./p:spPr/a:effectLst/*|./p:spPr/a:effectDag|./p:spPr/a:custGeom',namespaces=NS):continue
            own=identity==label['shapeId']
            if not own and (label['w']*label['h']>b['w']*b['h']*.25 or abs(label['x']+label['w']/2-b['x']-b['w']/2)>b['w']*.2 or abs(label['y']+label['h']/2-b['y']-b['h']/2)>b['h']*.2):continue
            markers=[s for s in labels if inside(s,b)]
            marker_ids={s['shapeId'] for s in markers}
            # A content card containing an instruction among real content is
            # not an empty photo frame. Do not erase any neighbouring content.
            if any(s['shapeId'] not in marker_ids and intersection(s,b)>1 for s in layout['slots']):continue
            if any(o['shape_id'] not in marker_ids|{identity} and inside(box(o),b) and box(o)['w']*box(o)['h']>1 for o in items if o.get('object_kind')!='group'):continue
            if not own:
                order={o['shape_id']:i for i,o in enumerate(items)}
                if order[identity]>=order.get(label['shapeId'],-1):continue
            candidates.append((b['w']*b['h'],item,markers))
        candidates.sort(key=lambda c:c[0])
        if not candidates:continue
        # Nested layout/background cards may surround the actual image frame.
        # Pick an unambiguous innermost frame, never two overlapping alternatives.
        _,chosen,markers=candidates[0]
        if any(not inside(box(chosen),box(other)) for _,other,_ in candidates[1:]):continue
        if len(candidates)>1 and abs(candidates[1][0]-candidates[0][0])<1:continue
        result[chosen['shape_id']]=dict(method='explicit_image_instruction',markerFieldKeys=[s['key'] for s in markers],markerShapeIds=[s['shapeId'] for s in markers],labels=[s['text'] for s in markers])
    return result
