"""Read-only purpose signals for new and already uploaded native templates."""
import io
import json
from PIL import Image
from runtime.security import read_package, xml, NS
from runtime.fonts import effective_source
from runtime.geometry import box, intersection
from runtime.native_budget import measured_profile, TemplateTextFitter
from runtime.preflight import layout_readiness
from runtime.frame_store import read_frames
from runtime.text_regions import reserved_regions, text_limits, constrained_frame
from runtime.image_markers import labelled_image_frames

def objects(items):
    for item in items:
        yield item
        yield from objects(item.get('children', []))

def has_text(item):
    rich=item.get('rich_text') or {}
    return any(r.get('text','').strip() for p in rich.get('paragraphs',[]) for r in p.get('runs',[]))

def artwork(data, media):
    if media.lower().endswith(('.svg','.emf','.wmf')):
        return True
    try:
        with Image.open(io.BytesIO(data)) as original:
            # Sampling must not manufacture extra antialiasing colours, otherwise
            # a crisp brand pattern is falsely classified as a photograph.
            small=original.convert('RGB').resize((64,64),resample=Image.Resampling.NEAREST).quantize(colors=16)
            # Near-flat fills / two-colour brand patterns, not photographs.
            counts=sorted((count for count,_ in small.getcolors()),reverse=True)
            # A solid placeholder may intentionally be a photo slot; do not
            # reject it merely for being empty. Require a two-colour pattern.
            return len(counts)>1 and counts[1]/4096 > .08 and sum(counts[:2])/4096 > .94
    except (OSError,ValueError):
        return False

def opaque_picture(item, parts):
    """Prove opacity, not just a bounding-box intersection. Transparent artwork,
    masks and rotated shapes stay with the actual-render validator.
    """
    if not parts or item.get('object_kind')!='picture':return False
    geometry=item.get('geometry_full') or {}
    if geometry.get('rotation_deg') or any(t.get('rotation_deg') for t in geometry.get('group_transform_chain',[])):return False
    try:
        root=xml(parts[item['source_part']])
        pictures=root.xpath('.//p:pic[p:nvPicPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(item['shape_id']))
        if len(pictures)!=1:return False
        picture=pictures[0]
        # Effects may alter alpha or clip the rectangular media surface.
        if picture.xpath('.//a:blip/*[not(self::a:extLst or self::a:alphaModFix[not(@amt) or @amt="100000"])]|.//a:effectLst/*|.//a:effectDag|.//a:custGeom|.//a:prstGeom[@prst!="rect"]',namespaces=NS):return False
        with Image.open(io.BytesIO(parts[item['media_id']])) as image:
            return image.convert('RGBA').getchannel('A').getextrema()==(255,255)
    except (KeyError,OSError,ValueError):return False

def opaque_placeholder(item, parts):
    """Only explicit solid rectangular paint is proof. Inherited/transparent
    fills and nonrectangular masks require the actual PDF paint check."""
    if not parts or item.get('object_kind')!='shape' or item.get('placeholder_type')!='pic':return False
    geometry=item.get('geometry_full') or {}
    if geometry.get('rotation_deg') or any(t.get('rotation_deg') for t in geometry.get('group_transform_chain',[])):return False
    part=parts.get(item.get('source_part'))
    if part is None:return False
    root=xml(part)
    nodes=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(item['shape_id']))
    if len(nodes)!=1:return False
    node=nodes[0]
    if node.xpath('./p:spPr/a:custGeom|./p:spPr/a:prstGeom[@prst!="rect"]|./p:spPr/a:effectLst/*|./p:spPr/a:effectDag',namespaces=NS):return False
    fills=node.xpath('./p:spPr/a:solidFill',namespaces=NS)
    return bool(fills) and not fills[0].xpath('.//a:alpha[@val!="100000"]|.//a:alphaMod|.//a:alphaOff',namespaces=NS)

def foreground_pictures(items, slots, parts=None):
    items=[o for o in items if o.get('source_level','slide')=='slide']
    order={o.get('shape_id'):i for i,o in enumerate(items)}
    for o in items:
        if o.get('hidden') or not (opaque_placeholder(o,parts) or opaque_picture(o,parts)):continue
        for slot in slots:
            if order[o['shape_id']]>order.get(slot['shapeId'],len(items)) and intersection(box(o),slot)>1:
                yield o,slot

def text_occlusions(items, slots, parts=None):
    """Reject proven complete coverage, not intersecting empty frame area.
    Partial coverage supplies a constrained budget; actual glyph paint remains
    the final authority for transparency, complex masks and uncertain fills.
    """
    issues=[]
    for o,slot in foreground_pictures(items,slots,parts):
        if slot['w']*slot['h']>0 and intersection(box(o),slot)>=slot['w']*slot['h']*.999:
            issues.append(dict(key=slot['key'],reason='unverifiable',geometryVersion=2,imageShapeId=o['shape_id'],
                               details=['source_picture_placeholder_occludes_text' if o.get('placeholder_type')=='pic' else 'source_opaque_picture_occludes_text']))
    return issues

def picture_text_limits(items, slots, parts, root, limits):
    """Budget partial occlusion without moving text, images or native layers.
    Right-edge limits apply to explicit lines, never invented automatic wraps."""
    for item,slot in foreground_pictures(items,slots,parts):
        nodes=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(slot['shapeId']))
        if len(nodes)!=1 or 'cell' in slot:continue
        node=nodes[0];body=node.find('p:txBody/a:bodyPr',NS)
        if body is None or body.get('anchor','t')!='t' or body.get('vert','horz')!='horz' or body.get('rot','0')!='0':continue
        if node.xpath('./p:spPr/a:xfrm[@rot!="0"]|ancestor::p:grpSp/p:grpSpPr/a:xfrm[@rot!="0"]',namespaces=NS):continue
        b=box(item);limit=dict(limits.get(slot['key']) or {k:slot[k] for k in ('x','y','w','h')})
        gap=2
        if b['x']>slot['x'] and slot.get('align','left')=='left':
            limit['w']=min(limit['w'],b['x']-slot['x']-gap)
            limit['explicitLines']=True
        elif b['y']>slot['y']:
            limit['h']=min(limit['h'],b['y']-slot['y']-gap)
        else:continue # Complex anchors/interior occlusions are verified on render.
        if limit['w']<=0 or limit['h']<=0:continue
        limit.update(blockerShapeId=item['shape_id'],blockerRole='foreground_picture')
        limits[slot['key']]=limit
    return limits

def text_backdrop(picture, slots, items):
    """A picture behind a complete text field is part of its contrast surface,
    not a vacant photo tile. Clearing it would destroy source typography even
    with zero generated photos. Use source paint order, including group children.
    A foreground picture or a small edge intersection is not a backdrop.
    """
    painted=[o for o in items if o.get('source_level','slide')=='slide']
    order={o.get('shape_id'):i for i,o in enumerate(painted)}
    position=order.get(picture.get('shape_id'))
    if position is None:return False
    frame=box(picture)
    return any(s.get('text','').strip() and order.get(s['shapeId'],-1)>position
               and s['w']*s['h']>0 and intersection(frame,s)>=s['w']*s['h']*.90
               for s in slots)

def slot_metadata(slots, items, frames, fitter, profiles=None, limits=None):
    """Expose source group ancestry without changing original objects or text."""
    identities={o.get('shape_id'):o for o in items if o.get('source_level','slide')=='slide'}
    result=[]
    for slot in slots:
        value=dict(slot)
        limit=(limits or {}).get(slot['key'])
        group=identities.get(slot['shapeId'],{}).get('parent_group_path')
        if group:value['groupPath']=group
        else:value.pop('groupPath',None)
        if slot['key'] in frames:
            frame=constrained_frame(frames[slot['key']],slot,limit,fitter).model_dump()
            if limit and (frame['height_emu'] < frames[slot['key']]['height_emu'] or frame.get('visible_width_emu') is not None):
                value['textRegion']=limit
            # Identical geometry and typography recur throughout a template.
            # Cache only within this analysis/immutable font environment.
            key=json.dumps({k:v for k,v in frame.items() if k not in ('source_element_id','source_fingerprint')},sort_keys=True)
            profile=profiles.get(key) if profiles is not None else None
            if profile is None:
                profile=measured_profile(frame,slot['maxChars'],fitter)
                if profiles is not None and 'textFit' in profile:profiles[key]=profile
            value.update(profile)
        result.append(value)
    return result

def visual_slots(items, layout, roles, parts):
    """Native addresses for explicit image selection. Size/flat colours are hints,
    not proof that a thematic cutout or full-bleed photo must be protected."""
    result=[]
    labelled=labelled_image_frames(items,layout,roles,parts)
    for item in items:
        if item.get('hidden') or item.get('source_level','slide')!='slide':continue
        kind=item.get('object_kind')
        detection=labelled.get(item.get('shape_id'))
        placeholder=kind=='shape' and (item.get('placeholder_type')=='pic' or detection)
        if kind!='picture' and not placeholder:continue
        if placeholder and has_text(item) and not detection:continue
        frame=box(item)
        if frame['w']<=0 or frame['h']<=0:continue
        identity=item.get('shape_id')
        try:
            root=xml(parts[item['source_part']])
            nodes=root.xpath('.//p:cNvPr[@id=$id]',namespaces=NS,id=str(identity))
            if len(nodes)!=1:continue
            node=nodes[0].getparent().getparent()
            if not placeholder and len(node.xpath('.//a:blip',namespaces=NS))!=1:continue
        except (KeyError,ValueError):continue
        role=roles.get(identity,'unknown')
        local=(item.get('geometry_full') or {}).get('local_bbox') or {}
        aspect=local['width_emu']/local['height_emu'] if local.get('height_emu') and local.get('width_emu') else frame['w']/frame['h']
        backdrop=False if detection else text_backdrop(item,layout['slots'],items)
        result.append(dict(shapeId=identity,kind='placeholder' if placeholder else 'picture',box=frame,aspectRatio=aspect,
            sourceRole=role,protected=role in ('logo','icon','footer','page_number'),requiresOpaque=backdrop,
            name=(item.get('name') or '')[:120],**(dict(detection=detection) if detection else {})))
    return sorted(result,key=lambda s:(s['box']['y'],s['box']['x'],s['shapeId']))

def layout_metadata(folder, include_budgets=False, include_legacy=False):
    data=json.loads((folder/'analysis.json').read_text())
    parsed_path=folder/'effective-parser.json'
    if not parsed_path.exists(): parsed_path=folder/'parser.json'
    from runtime.model_store import read_model
    model=read_model(parsed_path)
    parts=read_package(effective_source(folder/'source.pptx',folder,data).read_bytes())
    slides={s['slide_id']:s for s in model['slides']}
    analyzer=read_model(folder/'analyzer.json') if (folder/'analyzer.json').exists() else {}
    assignments={s['slide_id']:s['role_assignments'] for s in analyzer.get('slide_assignments',[])}
    artwork_cache={}
    frames=read_frames(folder,model,parts,data) if include_budgets and (folder/'frames.json').exists() else {}
    fitter=TemplateTextFitter()
    profiles={}
    result={}
    for layout in data['layouts']:
        slide=slides[layout['id']]
        items=list(objects(slide['objects']))
        picture=next((o for o in items if o.get('shape_id')==layout.get('imageShapeId') and o.get('object_kind')=='picture'),None)
        picture_box=layout.get('imageBox',{})
        full_background=picture_box.get('w',0)*picture_box.get('h',0)>data['width']*data['height']*.85
        media=picture.get('media_id','') if picture else ''
        roles={r['source_open_xml_shape_id']:r['role'] for r in assignments.get(layout['id'],[]) if r['source_level']=='slide'}
        reserved=reserved_regions(items,roles)
        limits=text_limits(layout['slots'],reserved,xml(parts[layout['part']]))
        limits=picture_text_limits(items,layout['slots'],parts,xml(parts[layout['part']]),limits)
        # Empty p:ph type="pic" is an explicit source image frame, not a free
        # area. Prefer it over a device bezel/illustration surrounding it.
        placeholders=[o for o in items if not o.get('hidden') and o.get('object_kind')=='shape'
                      and o.get('placeholder_type')=='pic' and not has_text(o)
                      and 0<box(o)['w']*box(o)['h']/(data['width']*data['height'])<.85
                      and not any(intersection(box(o),s)>1 for s in layout['slots'])]
        photos=[]
        for item in items:
            if item.get('hidden') or item.get('object_kind')!='picture': continue
            b=box(item); area=b['w']*b['h']/(data['width']*data['height'])
            m=item.get('media_id','')
            if m not in artwork_cache: artwork_cache[m]=m in parts and artwork(parts[m],m)
            framing=any(intersection(b,box(p))>=box(p)['w']*box(p)['h']*.90 for p in placeholders)
            if .035<area<.85 and not framing and roles.get(item.get('shape_id')) not in ('logo','decoration','background','background_image','footer','page_number') and not artwork_cache[m]: photos.append(item['shape_id'])
        source_frame=max(placeholders,key=lambda o:box(o)['w']*box(o)['h'],default=None)
        frame_box=box(source_frame) if source_frame else None
        backdrops=[o['shape_id'] for o in items if o.get('shape_id') in photos and text_backdrop(o,layout['slots'],items)]
        image_slots=[]
        for item in items:
            identity=item.get('shape_id')
            if identity in backdrops:continue
            if identity not in photos and item not in placeholders:continue
            frame=box(item)
            local=(item.get('geometry_full') or {}).get('local_bbox') or {}
            aspect=local['width_emu']/local['height_emu'] if local.get('height_emu') and local.get('width_emu') else frame['w']/frame['h']
            image_slots.append(dict(shapeId=identity,kind='placeholder' if item in placeholders else 'picture',box=frame,aspectRatio=aspect))
        # Primary photo first, then the remaining native frames. Never treat
        # one source image as four photos or charge for logos/decorative fills.
        primary=source_frame['shape_id'] if source_frame else layout.get('imageShapeId')
        image_slots.sort(key=lambda s:(s['shapeId']!=primary,-s['box']['w']*s['box']['h'],s['box']['y'],s['box']['x'],s['shapeId']))
        result[layout['id']]=dict(visuals=dict(unlabelledShapes=sum(1 for o in items if not o.get('hidden') and o.get('object_kind') in ('shape','connector') and not has_text(o))),
            imageIsArtwork=bool(picture and (picture.get('shape_id') in backdrops or full_background or (media in parts and artwork(parts[media],media)))),
            photoShapeIds=photos,photoPlaceholderShapeIds=[o['shape_id'] for o in placeholders],pageNumberShapeIds=[key for key,role in roles.items() if role=='page_number'],
            textBackdropShapeIds=backdrops,
            textOcclusions=text_occlusions(items,layout['slots'],parts),
            reservedRegions=reserved,textLimits=limits,
            slots=slot_metadata(layout['slots'],items,frames.get(layout['id'],{}),fitter,profiles,limits),
            imageSlotCount=len(image_slots),imageSlots=image_slots,
            visualSlots=visual_slots(items,layout,roles,parts),
            designMetadataVersion=20)
        if include_budgets:
            # Recheck previously uploaded manifests too; otherwise old layouts
            # remain selectable even after the checkout guard rejects them.
            result[layout['id']].update(layout_readiness(layout, frames.get(layout['id'], {}), fitter))
        blocked={issue['key'] for issue in result[layout['id']]['textOcclusions']}
        if layout['slots'] and all(slot['key'] in blocked for slot in layout['slots']):
            result[layout['id']]['usable']=False
            result[layout['id']]['warnings']=list(dict.fromkeys([*result[layout['id']].get('warnings',layout.get('warnings',[])),
                'Текстовые поля полностью закрыты изображением шаблона. Макет сохранён для просмотра.']))
        if include_legacy:
            # Only historical exports need the exact v1 profile/fingerprint.
            from runtime.background import source_background
            from runtime.legacy.design_profile import design_profile
            background=source_background(parts,layout['part'])
            result[layout['id']]['adaptive']=design_profile(data,layout,items,assignments.get(layout['id'],[]),photos,background) if background else None
        if source_frame:
            local=(source_frame.get('geometry_full') or {}).get('local_bbox') or {}
            aspect=local['width_emu']/local['height_emu'] if local.get('height_emu') and local.get('width_emu') else frame_box['w']/frame_box['h']
            result[layout['id']].update(imageShapeId=source_frame['shape_id'],imageBox=frame_box,imageAspectRatio=aspect,imageKind='placeholder',imageIsArtwork=False)
        elif image_slots:
            primary=image_slots[0]
            result[layout['id']].update(imageShapeId=primary['shapeId'],imageBox=primary['box'],imageAspectRatio=primary['aspectRatio'],imageKind=primary['kind'],imageIsArtwork=False)
        elif picture:
            result[layout['id']]['imageKind']='picture'
    return result
