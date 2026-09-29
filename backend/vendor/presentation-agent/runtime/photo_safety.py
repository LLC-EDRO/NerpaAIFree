"""Native replacement footprints. Transparent cutouts use free space inside
existing frames; native transforms and text never move to accommodate them."""
from runtime.geometry import intersection

def safe_cutout_box(frame, slots, fields):
    candidates=[dict(frame)]
    for text in slots:
        if not fields.get(text['key'],'').strip():continue
        obstacle=dict(x=text['x']-2,y=text['y']-2,w=text['w']+4,h=text['h']+4)
        next_boxes=[]
        for b in candidates:
            if intersection(b,obstacle)<=0:
                next_boxes.append(b);continue
            left=max(b['x'],obstacle['x']);right=min(b['x']+b['w'],obstacle['x']+obstacle['w'])
            top=max(b['y'],obstacle['y']);bottom=min(b['y']+b['h'],obstacle['y']+obstacle['h'])
            next_boxes.extend([dict(x=b['x'],y=b['y'],w=left-b['x'],h=b['h']),
                dict(x=right,y=b['y'],w=b['x']+b['w']-right,h=b['h']),
                dict(x=b['x'],y=b['y'],w=b['w'],h=top-b['y']),
                dict(x=b['x'],y=bottom,w=b['w'],h=b['y']+b['h']-bottom)])
        candidates=sorted((b for b in next_boxes if b['w']>0 and b['h']>0),key=lambda b:b['w']*b['h'],reverse=True)[:64]
    if not candidates:return None
    best=candidates[0]
    return best if best['w']*best['h']>=frame['w']*frame['h']*.25 else None

def replacement_photo_issues(layout, metadata, slide):
    if slide.get('native',{}).get('mode')!='source':return []
    photos={p.get('shapeId'):p for p in slide.get('images',[]) if p.get('image')}
    if 'images' not in slide and slide.get('image'):
        photos[metadata.get('imageShapeId',layout.get('imageShapeId'))]={}
    native_visual=slide.get('visualSlotsVersion')==1
    frames=[dict(s) for s in metadata.get('visualSlots' if native_visual else 'imageSlots',[]) if s['shapeId'] in photos]
    issues=[];fields=slide['native'].get('fields',{});painted=[]
    for frame in frames:
        transparent=native_visual and photos[frame['shapeId']].get('background')=='transparent'
        area=frame['box']
        if transparent or (native_visual and frame.get('kind')=='picture' and not frame.get('requiresOpaque')):
            area=safe_cutout_box(area,layout.get('slots',[]),fields)
            if area is None:
                issues.append(dict(key='photo_'+str(frame['shapeId']),imageShapeId=frame['shapeId'],reason='overlap',details=['generated_photo_no_safe_region']))
                continue
        # A verified source underlay stays behind text. Final raster contrast
        # checks remain mandatory; a background's bounding box isn't occlusion.
        if not (native_visual and frame.get('requiresOpaque')):
            for text in layout.get('slots',[]):
                if fields.get(text['key'],'').strip() and intersection(area,text)>max(1,text['w']*text['h']*.08):
                    issues.append(dict(key=text['key'],imageShapeId=frame['shapeId'],reason='overlap',details=['generated_photo_overlaps_text']))
            painted.append((frame,area))
    for i,(frame,a) in enumerate(painted):
        for other,b in painted[i+1:]:
            if intersection(a,b)>max(1,min(a['w']*a['h'],b['w']*b['h'])*.08):
                issues.append(dict(key='photo_'+str(frame['shapeId']),imageShapeId=frame['shapeId'],reason='overlap',details=['generated_photos_overlap']))
    return issues

def replacement_region(layout, slot, fields):
    """Constrain a content photo to free space in its own frame. Underlays and
    branded masks are not movable content. Shared by validation and export."""
    if slot.get('requiresOpaque') or slot.get('kind')!='picture':return slot['box']
    return safe_cutout_box(slot['box'],layout.get('slots',[]),fields)
