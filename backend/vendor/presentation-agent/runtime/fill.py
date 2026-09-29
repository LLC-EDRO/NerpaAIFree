"""Native OOXML fill. Clone slides, retain source visuals, collect unused output parts."""
import io
import re
import json
import hashlib
import math
import posixpath
import zipfile
from copy import deepcopy
from lxml import etree
from PIL import Image, ImageOps
from app.presentation.parser.archive import relationship_part
from app.text.replacement import replacement_paragraph_sources
from runtime.security import NS, xml, read_package
from runtime.analysis import validate_fields
from runtime.charts import fill_chart
from runtime.fonts import effective_source
from runtime.layout_metadata import layout_metadata
from runtime.tables import expanded_table_layout, resize_table_rows
from runtime.sample_furniture import clean_sample_furniture
from runtime.package_cleanup import compact_package

def serialize(root):
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)

def update_document_title(parts, title):
    """Output metadata must name the new work, not the source template product.
    Preserve the source author's attribution and every other core property.
    """
    if 'docProps/core.xml' not in parts or not isinstance(title,str) or not title.strip():return
    root=xml(parts['docProps/core.xml'])
    key='{http://purl.org/dc/elements/1.1/}title'
    node=root.find(key)
    if node is None:node=etree.SubElement(root,key)
    node.text=title.strip()
    parts['docProps/core.xml']=serialize(root)

def shape_node(root, shape_id):
    found = root.xpath('.//p:cNvPr[@id=$id]', namespaces=NS, id=str(shape_id))
    if len(found) != 1:
        raise ValueError('pptx_shape_identity_mismatch')
    return found[0].getparent().getparent()

def source_image_layout(layout, metadata, native):
    if native.get('mode') not in ('source','rebuild'):return layout
    result=dict(layout)
    for key in ('imageShapeId','imageBox','imageAspectRatio','imageKind'):
        if key in metadata:result[key]=metadata[key]
    if metadata.get('imageIsArtwork'):
        result.pop('imageShapeId',None)
    return result

def source_image_assignments(layout, metadata, slide):
    if 'images' not in slide:
        return [{**layout,'image':slide['image']}] if slide.get('image') else []
    if slide['native'].get('mode') not in ('source','rebuild'):raise ValueError('pptx_multiple_images_require_source')
    if slide['native'].get('rebuild'):
        from runtime.rebuild import fields as rebuilt_fields
        layout={**layout,'slots':rebuilt_fields(slide['native']['rebuild'])}
    slots=metadata.get('visualSlots',[]) if slide.get('visualSlotsVersion')==1 else metadata.get('imageSlots',[])
    allowed={s['shapeId']:s for s in slots if not s.get('protected')}
    assignments=[];used=set();indices=set()
    for photo in slide['images']:
        identity=photo.get('shapeId');index=photo.get('slotIndex')
        if identity not in allowed or identity in used or not isinstance(index,int) or index in indices or index<0 or index>=len(slots) or slots[index]['shapeId']!=identity:
            raise ValueError('pptx_image_slot_ambiguous')
        slot=allowed[identity]
        if slot.get('requiresOpaque') and photo.get('background')=='transparent':raise ValueError('pptx_image_backdrop_requires_opaque')
        used.add(identity);indices.add(index)
        region=slot['box']
        if slide.get('visualSlotsVersion')==1 and photo.get('background')!='transparent':
            from runtime.photo_safety import replacement_region
            region=replacement_region(layout,slot,slide['native'].get('fields',{})) or slot['box']
        assignments.append(dict(imageShapeId=identity,imageKind=slot['kind'],imageBox=slot['box'],safeImageBox=region if region!=slot['box'] else None,imageAspectRatio=slot['aspectRatio'],image=photo['image'],requiresOpaque=slot.get('requiresOpaque',False),background=photo.get('background'),confirmedImageFrame=(slot.get('detection') or {}).get('method')=='explicit_image_instruction'))
    return assignments

def source_image_blip(node, kind, confirmed_shape=False):
    if kind=='placeholder':
        # Fill the existing shape, retaining identity, geometry, transform,
        # preset/custom mask, z-order, line and effects. No new p:sp/p:pic.
        placeholder=node.find('p:nvSpPr/p:nvPr/p:ph',NS)
        if (placeholder is None or placeholder.get('type')!='pic') and not (
            confirmed_shape and node.tag=='{%s}sp'%NS['p'] and node.xpath('./p:spPr/a:prstGeom[@prst="rect" or @prst="roundRect"]',namespaces=NS)
        ):raise ValueError('pptx_image_slot_ambiguous')
        props=node.find('p:spPr',NS)
        if props is None:raise ValueError('pptx_image_slot_ambiguous')
        for child in list(props):
            if etree.QName(child).localname in ('noFill','solidFill','gradFill','blipFill','pattFill','grpFill'):props.remove(child)
        fill=etree.Element('{%s}blipFill'%NS['a'])
        blip=etree.SubElement(fill,'{%s}blip'%NS['a'])
        stretch=etree.SubElement(fill,'{%s}stretch'%NS['a'])
        etree.SubElement(stretch,'{%s}fillRect'%NS['a'])
        pos=next((i for i,n in enumerate(props) if etree.QName(n).localname not in ('xfrm','prstGeom','custGeom')),len(props))
        props.insert(pos,fill)
        return blip
    blips=node.xpath('.//a:blip',namespaces=NS)
    if len(blips)!=1:raise ValueError('pptx_image_slot_ambiguous')
    return blips[0]

def clear_source_photo(picture, result, relationships, kind='picture'):
    # Clear sample photo content without deleting/replacing its source object.
    # Keep masks, geometry, outline, effects and stacking order exactly intact.
    media='ppt/media/nerpaEmptyPhoto.png'
    if media not in result:
        stream=io.BytesIO();Image.new('RGBA',(1,1),(0,0,0,0)).save(stream,format='PNG');result[media]=stream.getvalue()
    rid='nerpaEmptyPhoto'
    while any(r.get('Id')==rid and r.get('Target')!='../media/nerpaEmptyPhoto.png' for r in relationships):rid+='x'
    if not any(r.get('Id')==rid for r in relationships):
        etree.SubElement(relationships,'{%s}Relationship'%NS['pr'],Id=rid,Type=NS['r']+'/image',Target='../media/nerpaEmptyPhoto.png')
    blip=source_image_blip(picture,kind);blip.set('{%s}embed'%NS['r'],rid)
    for child in blip.xpath('./a:extLst',namespaces=NS):blip.remove(child)

def crop_source_placeholder(data, aspect):
    """Some viewers ignore a:srcRect on a shape fill (unlike p:pic).
    Crop only the generated bitmap, never resize/rebuild the native frame.
    This keeps the exported PPTX and PDF preview consistent without distortion.
    """
    if not math.isfinite(aspect) or aspect<=0:raise ValueError('pptx_image_slot_ambiguous')
    with Image.open(io.BytesIO(data)) as original:
        picture=ImageOps.exif_transpose(original)
        width,height=picture.size
        if width/height>aspect:
            cropped_width=max(1,min(width,round(height*aspect)))
            left=(width-cropped_width)//2
            picture=picture.crop((left,0,left+cropped_width,height))
        else:
            cropped_height=max(1,min(height,round(width/aspect)))
            top=(height-cropped_height)//2
            picture=picture.crop((0,top,width,top+cropped_height))
        buffer=io.BytesIO();picture.save(buffer,format='PNG')
        return buffer.getvalue()

def contain_source_cutout(data, aspect, safe_region=None, force_region=False):
    """Fit an alpha cutout wholly inside the existing source picture frame.
    The bitmap changes, never its OOXML shape, mask, effects or drawing order.
    Opaque historical photographs retain their previous crop behaviour.
    """
    if not math.isfinite(aspect) or aspect<=0:raise ValueError('pptx_image_slot_ambiguous')
    with Image.open(io.BytesIO(data)) as original:
        picture=ImageOps.exif_transpose(original).convert('RGBA')
        alpha=picture.getchannel('A')
        if alpha.getextrema()[0]==255 and not force_region:return None
        bounds=alpha.getbbox()
        if bounds is None:return None
        width,height=picture.size
        if width/height>aspect:width=max(1,round(height*aspect))
        else:height=max(1,round(width/aspect))
        # Remove only transparent margins. Leave air around the complete subject,
        # including for round/portrait masks, instead of slicing it like a photo.
        region=safe_region or dict(x=0,y=0,w=1,h=1)
        rw,rh=width*region['w'],height*region['h']
        padding=1 if force_region else .84
        subject=ImageOps.contain(picture.crop(bounds),(max(1,round(rw*padding)),max(1,round(rh*padding))),Image.Resampling.LANCZOS)
        canvas=Image.new('RGBA',(width,height),(0,0,0,0))
        canvas.alpha_composite(subject,(round(width*region['x']+(rw-subject.width)/2),round(height*region['y']+(rh-subject.height)/2)))
        buffer=io.BytesIO();canvas.save(buffer,format='PNG')
        return buffer.getvalue()

def styled_text_parts(runs, value):
    """Keep source emphasis/colour spans without splitting generated words.

    Only visual proportions are reused; source text and hyperlinks are discarded.
    """
    source = [(run, ''.join(run.xpath('./a:t/text()', namespaces=NS))) for run in runs]
    source = [(run, text) for run, text in source if text]
    if len(source) < 2 or not value: return []
    boundaries = [m.end() for m in re.finditer(r'\S+\s*', value)]
    if not boundaries: return []
    total = sum(len(text) for _, text in source)
    if len(boundaries) == 1:
        return [(max(source, key=lambda item: len(item[1]))[0], value)]
    result, start, weight = [], 0, 0
    for i, (run, text) in enumerate(source):
        weight += len(text)
        end = len(value) if i == len(source)-1 else min([start] + [b for b in boundaries if b >= start], key=lambda b: abs(b-len(value)*weight/total))
        if end > start: result.append((run, value[start:end]))
        start = end
    return result


def replace_text(shape, text, default_font_size=None, force_font_size=None):
    body = shape.find('p:txBody', NS)
    if body is None: body = shape.find('a:txBody', NS)
    if body is None:
        raise ValueError('pptx_text_body_missing')
    # Only the filled copy is fixed to its original bounds. spAutoFit otherwise
    # lets the viewer grow/shrink the SHAPE and break the supplied composition.
    body_properties = body.find('a:bodyPr', NS)
    if body_properties is not None:
        for mode in body_properties.findall('a:spAutoFit', NS):
            mode.tag = '{%s}noAutofit' % NS['a']
    originals = list(body.findall('a:p', NS))
    if not originals:
        raise ValueError('pptx_paragraph_style_missing')
    desired = text.split('\n')
    selected = replacement_paragraph_sources([bool(''.join(p.xpath('.//a:t/text()', namespaces=NS)).strip()) for p in originals], desired)
    for paragraph in originals:
        body.remove(paragraph)
    for value, source_index in zip(desired, selected):
        p = deepcopy(originals[source_index])
        runs = p.findall('a:r', NS)
        exemplar = next((r for r in runs if ''.join(r.xpath('./a:t/text()', namespaces=NS)).strip()), runs[0] if runs else None)
        # Retain exact paragraph/default/run properties, remove old text/field/hyperlink payload.
        for node in list(p):
            if etree.QName(node).localname in ('r', 'br', 'fld'):
                p.remove(node)
        r = deepcopy(exemplar) if exemplar is not None else etree.Element('{%s}r' % NS['a'])
        # An empty native paragraph carries its insertion style in endParaRPr.
        # Without copying it, the first generated text silently falls back to
        # the theme face instead of the font/bold/size measured by the parser.
        if not any(''.join(run.xpath('./a:t/text()', namespaces=NS)).strip() for run in runs):
            end_style = p.find('a:endParaRPr', NS)
            if end_style is not None:
                props = deepcopy(end_style)
                props.tag = '{%s}rPr' % NS['a']
                explicit = r.find('a:rPr', NS)
                if explicit is not None:
                    props.attrib.update(explicit.attrib)
                    for child in explicit:
                        for inherited in list(props):
                            if inherited.tag == child.tag: props.remove(inherited)
                        props.append(deepcopy(child))
                    r.remove(explicit)
                r.insert(0, props)
        if default_font_size or force_font_size:
            props = r.find('a:rPr', NS)
            if props is None: props = etree.Element('{%s}rPr'%NS['a']); r.insert(0,props)
            if force_font_size or not props.get('sz'): props.set('sz',str(round((force_font_size or default_font_size)*100)))
        for node in list(r):
            if etree.QName(node).localname != 'rPr':
                r.remove(node)
        for node in r.xpath('.//a:hlinkClick|.//a:hlinkMouseOver', namespaces=NS):
            node.getparent().remove(node)
        etree.SubElement(r, '{%s}t' % NS['a']).text = value
        end = p.find('a:endParaRPr', NS)
        if end is not None:
            # Removed sample runs must not leave a larger invisible paragraph
            # marker that disagrees with the physical fit calculation.
            props = r.find('a:rPr', NS)
            if props is not None and props.get('sz'):
                end.set('sz',props.get('sz'))
        styled = styled_text_parts(runs, value)
        if styled:
            for original, chunk in styled:
                run = deepcopy(r)
                props = run.find('a:rPr', NS)
                if props is not None: run.remove(props)
                original_props = original.find('a:rPr', NS)
                if original_props is not None: run.insert(0, deepcopy(original_props))
                props = run.find('a:rPr', NS)
                if force_font_size or default_font_size:
                    if props is None:
                        props = etree.Element('{%s}rPr'%NS['a']); run.insert(0, props)
                    if force_font_size or not props.get('sz'): props.set('sz', str(round((force_font_size or default_font_size)*100)))
                for link in run.xpath('.//a:hlinkClick|.//a:hlinkMouseOver', namespaces=NS): link.getparent().remove(link)
                run.find('a:t', NS).text = chunk
                p.insert(list(p).index(end) if end is not None else len(p), run)
        else:
            p.insert(list(p).index(end) if end is not None else len(p), r)
        body.append(p)

def fill(source, folder, slides, images, output, with_notes=False, allow_quality_warnings=False):
    analysis = json.loads((folder/'analysis.json').read_text())
    if hashlib.sha256(source.read_bytes()).hexdigest() != analysis['sha256']:
        raise ValueError('pptx_source_changed')
    parts = read_package(effective_source(source,folder,analysis).read_bytes())
    expansions = {}
    issues = validate_fields(folder, slides, expansions)
    if issues and not allow_quality_warnings:
        return dict(issues=issues)
    layouts = {l['id']: l for l in analysis['layouts']}
    palette=list(dict.fromkeys([c for c in analysis.get('styleProfile',{}).get('colors',[]) if isinstance(c,str)]+[s.get('color') for l in layouts.values() for s in l['slots'] if s.get('color')]))
    color_changes=[]
    from runtime.page_numbers import renumber_pages, pagination_padding
    padding=pagination_padding((xml(parts[l['part']]) for l in layouts.values()),analysis['width'],analysis['height'])
    metadata=layout_metadata(folder,include_legacy=any(s['native'].get('composition') for s in slides))
    from runtime.rebuild import profiles as rebuild_profiles,apply as apply_rebuild
    rebuilding=rebuild_profiles(folder) if any(s['native'].get('rebuild') for s in slides) else {}
    presentation = xml(parts['ppt/presentation.xml'])
    rels = xml(parts['ppt/_rels/presentation.xml.rels'])
    content_types = xml(parts['[Content_Types].xml'])
    slide_list = presentation.find('p:sldIdLst', NS)
    for child in list(slide_list): slide_list.remove(child)
    for child in list(rels):
        if child.get('Type', '').endswith('/slide'): rels.remove(child)
    # Never include unselected example slide text and old presenter notes in the result.
    removed_parts={n.get('PartName','').lstrip('/') for n in content_types if n.get('ContentType') in ('application/vnd.openxmlformats-officedocument.presentationml.slide+xml','application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml')}
    removed_parts.update(relationship_part(name) for name in list(removed_parts))
    result = {k:v for k,v in parts.items() if k not in removed_parts}
    update_document_title(result,slides[0].get('title') if slides else None)
    for name, data in list(result.items()):
        if not all(s['native'].get('preserveTemplate') for s in slides) and name.startswith(('ppt/slideMasters/', 'ppt/slideLayouts/')) and name.endswith('.xml'):
            inherited = xml(data)
            clean_sample_furniture(inherited)
            result[name] = serialize(inherited)
    for child in list(content_types):
        if child.get('PartName', '').lstrip('/') in removed_parts: content_types.remove(child)
    for child in presentation.findall('p:custShowLst', NS): presentation.remove(child)
    def override(part, kind):
        etree.SubElement(content_types, '{%s}Override' % NS['ct'], PartName='/'+part, ContentType=kind)
    if not any(n.get('Extension') == 'png' for n in content_types):
        etree.SubElement(content_types, '{%s}Default' % NS['ct'], Extension='png', ContentType='image/png')
    note_master = next((k for k in parts if k.startswith('ppt/notesMasters/') and k.endswith('.xml')), None)
    if (with_notes or any(s.get('sourceReferences') for s in slides)) and not note_master:
        note_master = 'ppt/notesMasters/nerpaNotesMaster.xml'
        result[note_master] = serialize(xml(f'<p:notesMaster xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld><p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/></p:notesMaster>'.encode()))
        override(note_master, 'application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml')
        rid = 'nerpaNotesMaster'
        etree.SubElement(rels, '{%s}Relationship' % NS['pr'], Id=rid, Type=NS['r']+'/notesMaster', Target='notesMasters/nerpaNotesMaster.xml')
        notes_list = etree.Element('{%s}notesMasterIdLst' % NS['p'])
        etree.SubElement(notes_list, '{%s}notesMasterId' % NS['p'], {'{%s}id' % NS['r']:rid})
        presentation.insert(1, notes_list)
    mapping = {}
    for i, slide in enumerate(slides):
        layout = layouts[slide['native']['sourceSlideId']]
        mapping.setdefault(layout['part'], f'ppt/slides/nerpaSlide{i+1}.xml')
    for i, slide in enumerate(slides):
        layout = layouts[slide['native']['sourceSlideId']]
        layout = source_image_layout(layout,metadata[layout['id']],slide['native'])
        part = f'ppt/slides/nerpaSlide{i+1}.xml'
        root = xml(parts[layout['part']])
        if expansions.get(i):
            from runtime.safe_text_expansion import apply_expansion, expanded_layout
            apply_expansion(root,expansions[i])
            layout=expanded_layout(layout,expansions[i])
        if not slide['native'].get('preserveTemplate'): clean_sample_furniture(root)
        root.attrib.pop('show',None)  # A selected hidden source slide becomes a visible output slide.
        rel_part = relationship_part(layout['part'])
        sr = xml(parts[rel_part]) if rel_part in parts else etree.Element('{%s}Relationships' % NS['pr'])
        adaptive=slide['native'].get('composition')
        rebuilt=slide['native'].get('rebuild')
        photo_assignments=[] if adaptive else source_image_assignments(layout,metadata[layout['id']],slide)
        filled_photo_ids={p.get('imageShapeId') for p in photo_assignments}
        ordinal=slide['native'].get('ordinal',i+1)
        if not isinstance(ordinal,int) or not 1<=ordinal<=40:raise ValueError('pptx_invalid_ordinal')
        if rebuilt:
            apply_rebuild(root,rebuilding[layout['id']],slide['native'])
        elif adaptive:
            from runtime.legacy.composition import apply_composition
            apply_composition(root,metadata[layout['id']]['adaptive'],slide['native'],images.get(slide.get('image')),result,sr,i,content_types)
        elif slide['native'].get('tableRows'):
            layout,_ = expanded_table_layout(layout, {}, slide['native']['tableRows'],slide['native'].get('tableRowWeights'),slide['native'].get('tableColumns'))
            resize_table_rows(root,slide['native']['tableRows'],slide['native'].get('tableRowWeights'),slide['native'].get('tableColumns'))
        if expansions.get(i):
            from runtime.cell_padding import apply_cell_padding
            apply_cell_padding(root,expansions[i])
        for slot in ([] if adaptive or rebuilt else layout['slots']):
            if slide['native'].get('preserveTemplate') and slide['native']['fields'][slot['key']]==slot.get('text',''): continue
            target=shape_node(root,slot['shapeId'])
            if 'cell' in slot:
                row,col=slot['cell']
                target=target.xpath('.//a:tbl/a:tr',namespaces=NS)[row].findall('a:tc',NS)[col]
            replace_text(target, slide['native']['fields'][slot['key']],slot.get('defaultFontSize') or slot.get('size'),slide['native'].get('textSizes',{}).get(slot['key']))
        if not adaptive and not rebuilt:
            from runtime.text_alignment import apply_alignment
            apply_alignment(root,layout,slide['native'])
        if not adaptive and ordinal>1 and not slide['native'].get('preserveTemplate'):
            # All source photographs are sample content, not free extra images.
            # Preserve only the explicitly replaced photo; logos/art remain.
            for identity in metadata[layout['id']].get('photoShapeIds',[]):
                if identity in filled_photo_ids:continue
                if slide['native'].get('mode')=='source' and identity in metadata[layout['id']].get('textBackdropShapeIds',[]):continue
                picture=shape_node(root,identity)
                if slide['native'].get('mode')=='source':clear_source_photo(picture,result,sr)
                else:picture.getparent().remove(picture)
            if slide['native'].get('mode')=='source':
                # Unused native picture placeholders also have a sample fill
                # (often grey hatching). Clear that content, not their shapes,
                # instead of leaving empty photo tiles or duplicating one image.
                for identity in metadata[layout['id']].get('photoPlaceholderShapeIds',[]):
                    if identity in filled_photo_ids:continue
                    clear_source_photo(shape_node(root,identity),result,sr,'placeholder')
        for identity in metadata[layout['id']].get('pageNumberShapeIds',[]):
            page=shape_node(root,identity)
            texts=page.findall('.//a:t',NS)
            for j,t in enumerate(texts):t.text=str(ordinal) if j==0 else ''
        for spec in ([] if adaptive or slide['native'].get('preserveSource') else layout.get('charts',[])):
            from runtime.chart_contrast import source_background
            try:background=source_background(folder/'previews'/'presentation.pdf',layout['index'],spec)
            except (OSError,RuntimeError,ValueError):background=None
            fill_chart(spec,slide['native']['charts'][spec['key']],parts,result,root,sr,i,content_types,palette,background)
        for photo in photo_assignments:
            if not photo.get('imageShapeId'):
                raise ValueError('pptx_no_image_slot')
            data = images[photo['image']]
            if photo.get('requiresOpaque'):
                with Image.open(io.BytesIO(data)) as bitmap:
                    if bitmap.convert('RGBA').getchannel('A').getextrema()[0]<255:raise ValueError('pptx_image_backdrop_requires_opaque')
            target=photo.get('imageAspectRatio') or photo['imageBox']['w']/photo['imageBox']['h']
            baked_crop=slide['native'].get('mode') in ('source','rebuild') and photo.get('imageKind')=='placeholder'
            safe_region=None
            if photo.get('safeImageBox'):
                b=photo['imageBox'];safe=photo['safeImageBox']
                safe_region=dict(x=(safe['x']-b['x'])/b['w'],y=(safe['y']-b['y'])/b['h'],w=safe['w']/b['w'],h=safe['h']/b['h'])
            if slide.get('visualSlotsVersion')==1 and photo.get('background')=='transparent':
                from runtime.photo_safety import safe_cutout_box
                if rebuilt:
                    from runtime.rebuild import fields as rebuilt_fields
                    text_slots=rebuilt_fields(rebuilt)
                else:text_slots=layout.get('slots',[])
                b=photo['imageBox'];safe=safe_cutout_box(b,text_slots,slide['native']['fields'])
                if safe is None:raise ValueError('pptx_image_no_safe_region')
                safe_region=dict(x=(safe['x']-b['x'])/b['w'],y=(safe['y']-b['y'])/b['h'],w=safe['w']/b['w'],h=safe['h']/b['h'])
            cutout=contain_source_cutout(data,target,safe_region,bool(photo.get('safeImageBox'))) if slide['native'].get('mode') in ('source','rebuild') else None
            if cutout is not None:data=cutout;baked_crop=True
            elif baked_crop:data=crop_source_placeholder(data,target)
            media = 'ppt/media/nerpa-'+hashlib.sha256(data).hexdigest()+'.png'
            result[media] = data
            rid = 'nerpaGeneratedImage'
            while any(r.get('Id') == rid for r in sr): rid += 'x'
            etree.SubElement(sr, '{%s}Relationship' % NS['pr'], Id=rid, Type=NS['r']+'/image', Target=posixpath.relpath(media, posixpath.dirname(part)))
            node = shape_node(root, photo['imageShapeId'])
            blip = source_image_blip(node,photo.get('imageKind'),photo.get('confirmedImageFrame',False))
            blip.set('{%s}embed' % NS['r'], rid)
            # Fit new 16:9 media to the source picture frame without stretching it.
            # Original coordinates/mask/effects remain; the crop adapts to the new bitmap.
            with Image.open(io.BytesIO(data)) as generated:
                ratio=generated.width/generated.height
            fill_node=blip.getparent()
            crop=fill_node.find('a:srcRect',NS)
            if crop is None: crop=etree.Element('{%s}srcRect'%NS['a']);fill_node.insert(list(fill_node).index(blip)+1,crop)
            horizontal=0 if baked_crop else round(max(0,(1-target/ratio)*50000))
            vertical=0 if baked_crop else round(max(0,(1-ratio/target)*50000))
            for side,value in [('l',horizontal),('r',horizontal),('t',vertical),('b',vertical)]:crop.set(side,str(value))
            # Drop alternate SVG payload only on the explicitly replaced picture.
            for alt in blip.xpath('./a:extLst', namespaces=NS): blip.remove(alt)
        for relation in list(sr):
            kind = relation.get('Type', '').split('/')[-1]
            if kind in ('notesSlide', 'comments', 'commentAuthors'):
                sr.remove(relation)
            elif kind == 'slide':
                old = posixpath.normpath(posixpath.join(posixpath.dirname(layout['part']), relation.get('Target')))
                if old in mapping:
                    relation.set('Target', posixpath.relpath(mapping[old], posixpath.dirname(part)))
                else:
                    for node in root.xpath('.//*[@r:id=$id]', namespaces=NS, id=relation.get('Id')):
                        node.getparent().remove(node)
                    sr.remove(relation)
        if with_notes or slide.get('sourceReferences'):
            note_part = f'ppt/notesSlides/nerpaNotes{i+1}.xml'
            notes = xml(f'<p:notes xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id="2" name="Notes"/><p:cNvSpPr/><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t/></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>'.encode())
            refs = slide.get('sourceReferences') or []
            sources_text = '\n\n'.join(str(s.get('title', ''))+'\n'+str(s.get('url', '')) for s in refs)
            notes.find('.//a:t', NS).text = '\n\n'.join(v for v in [slide.get('notes', '') if with_notes else '', 'Источники / Sources\n'+sources_text if refs else ''] if v)
            result[note_part] = serialize(notes)
            nr = etree.Element('{%s}Relationships' % NS['pr'])
            for rid, kind, target in [('slide', 'slide', part), ('master', 'notesMaster', note_master)]:
                etree.SubElement(nr, '{%s}Relationship' % NS['pr'], Id=rid, Type=NS['r']+'/'+kind, Target=posixpath.relpath(target, posixpath.dirname(note_part)))
            result[relationship_part(note_part)] = serialize(nr)
            etree.SubElement(sr, '{%s}Relationship' % NS['pr'], Id='nerpaNotes', Type=NS['r']+'/notesSlide', Target=posixpath.relpath(note_part, posixpath.dirname(part)))
            override(note_part, 'application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml')
        if slide['native'].get('preserveTemplate'):
            from runtime.contrast import repair_source_contrast
            color_changes.extend(dict(slide=i+1,**change) for change in repair_source_contrast(root,layout,palette,folder/'previews'/'presentation.pdf'))
            from runtime.page_numbers import renumber_pages
            renumber_pages(root,analysis['width'],analysis['height'],
                           [slide['native'].get('sourceOrdinal'),layout['index']+1],ordinal,
                           slide['native'].get('deckSlideCount',len(slides)),slide['native'].get('sourceSlideCount'),padding)
        from runtime.manual_text import apply_manual_text
        apply_manual_text(root,layout,slide,rebuilding.get(layout['id']))
        result[part], result[relationship_part(part)] = serialize(root), serialize(sr)
        rid = f'nerpaSlide{i+1}'
        etree.SubElement(rels, '{%s}Relationship' % NS['pr'], Id=rid, Type=NS['r']+'/slide', Target='slides/'+posixpath.basename(part))
        etree.SubElement(slide_list, '{%s}sldId' % NS['p'], {'id':str(256+i), '{%s}id' % NS['r']:rid})
        override(part, 'application/vnd.openxmlformats-officedocument.presentationml.slide+xml')
    result['ppt/presentation.xml'], result['ppt/_rels/presentation.xml.rels'], result['[Content_Types].xml'] = serialize(presentation), serialize(rels), serialize(content_types)
    result, cleanup = compact_package(result)
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in result.items(): archive.writestr(name, data)
    from runtime.safe_text_expansion import expansion_report
    return dict(issues=issues, pptxWritten=True, slideCount=len(slides), packageCleanup=cleanup,fieldChanges=expansion_report(expansions),colorChanges=color_changes)
