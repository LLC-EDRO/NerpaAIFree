"""Last-resort native recomposition. Model JSON is data, never executable code."""
import hashlib,json,math,re
from functools import lru_cache
from copy import deepcopy
from lxml import etree
from runtime.text_protection import protected_furniture
from runtime.security import NS,xml,read_package
from runtime.fonts import effective_source,TemplateFontResolver
from runtime.geometry import box,inside,intersection
from runtime.model_store import read_model
from runtime.layout_metadata import objects as walk_objects
from runtime.legacy.composition import node,geometry,solid,add_text,text_frame
from runtime.text_frames import TemplateTextFitter
from runtime.typography import inherited_vertical_alignment
from runtime.text_containers import container_for
from runtime.source_composition import add_source_contacts, source_line_contact

def has_native_text(shape):
    return any((t.text or "").strip() for t in shape.findall(".//a:t",NS))

def visible_decoration(shape):
    if has_native_text(shape):return False
    props=shape.find('p:spPr',NS)
    # Empty transparent helper frames have no painted area and reserve no space.
    return not (props is not None and props.find('a:noFill',NS) is not None and props.find('a:ln/a:noFill',NS) is not None and props.find('a:effectLst/*',NS) is None)

def collision_repair_region(anchor, anchors, page):
    """A broken source overlap must not lock independent text to the broken box.
    Containers still own their labels; final bounds/obstacle validation still runs.
    """
    peers=[]
    for other in anchors:
        if other['key']==anchor['key']:continue
        w=min(anchor['x']+anchor['w'],other['x']+other['w'])-max(anchor['x'],other['x'])
        h=min(anchor['y']+anchor['h'],other['y']+other['h'])-max(anchor['y'],other['y'])
        if w>2 and h>2:peers.append(other['key'])
    if peers:
        anchor['sourceOverlapPeers']=peers
        if not anchor.get('container'):
            anchor['repairRegion']=dict(page)

@lru_cache(maxsize=4)
def profiles(folder):
    data=json.loads((folder/'analysis.json').read_text())
    parts=read_package(effective_source(folder/'source.pptx',folder,data).read_bytes())
    model=read_model(folder/'effective-parser.json' if (folder/'effective-parser.json').exists() else folder/'parser.json')
    analyzer=read_model(folder/'analyzer.json')
    assignments={a['slide_id']:a.get('role_assignments',[]) for a in analyzer.get('slide_assignments',[])}
    parsed={s['slide_id']:s for s in model['slides']}
    fonts=sorted({s.get('font','Arial') for l in data['layouts'] for s in l['slots']}) or ['Arial']
    resolver=TemplateFontResolver()
    fonts=[font for font in fonts if all(resolver.resolve(font,bold=bold,italic=False).path is not None for bold in (False,True))]
    if not fonts:raise ValueError('pptx_rebuild_font_unavailable')
    colors=sorted({s.get('color','#163248') for l in data['layouts'] for s in l['slots']} | set(data.get('styleProfile',{}).get('colors',[])))
    colors=[c for c in colors if isinstance(c,str) and re.fullmatch(r'#[0-9A-Fa-f]{6}',c)]
    result={}
    for layout in data['layouts']:
        root=xml(parts[layout['part']]);tree=root.find('p:cSld/p:spTree',NS)
        roles={r['source_open_xml_shape_id']:r['role'] for r in assignments.get(layout['id'],[]) if r['source_level']=='slide'}
        objects=list(walk_objects(parsed[layout['id']]['objects']));by_id={o['shape_id']:o for o in objects}
        local_assignments={r['source_open_xml_shape_id']:r for r in assignments.get(layout['id'],[]) if r['source_level']=='slide'}
        keep=[];protected=[];pictures=[];tables=[];decorations=[]
        for child in tree:
            ids=[int(n.get('id')) for n in child.findall('.//p:cNvPr',NS)]
            if not ids:continue
            identity=ids[0];obj=by_id.get(identity);b=box(obj) if obj else None
            background=b and b['w']*b['h']>=data['width']*data['height']*.85 and not has_native_text(child)
            if background or any(protected_furniture(local_assignments.get(i,{}),by_id.get(i,{})) for i in ids):
                keep.append(identity)
                if b and not background:protected.append(b)
                continue
            if child.tag=='{%s}pic'%NS['p'] and b:pictures.append(dict(shapeId=identity,box=b))
            table=child.find('.//a:tbl',NS)
            if table is not None:tables.append(dict(shapeId=identity,box=b,columns=len(table.findall('a:tblGrid/a:gridCol',NS))))
            if b and child.tag in ('{%s}sp'%NS['p'],'{%s}cxnSp'%NS['p']) and visible_decoration(child):
                decorations.append(dict(shapeId=identity,box=b))
        for shape in tree.xpath('.//p:sp | .//p:cxnSp',namespaces=NS):
            ids=shape.findall('.//p:cNvPr',NS)
            if not ids or not visible_decoration(shape):continue
            identity=int(ids[0].get('id'));obj=by_id.get(identity)
            if obj and identity not in keep and not any(d['shapeId']==identity for d in decorations):
                decorations.append(dict(shapeId=identity,box=box(obj)))
        # Pictures inside groups are real obstacles too. Source-space geometry
        # is already transformed by the parser; retain their native XML nodes.
        for shape in tree.findall('.//p:pic',NS):
            identity=int(shape.find('p:nvPicPr/p:cNvPr',NS).get('id'))
            obj=by_id.get(identity)
            if obj and not any(p['shapeId']==identity for p in pictures):
                pictures.append(dict(shapeId=identity,box=box(obj)))
        order={item['shape_id']:i for i,item in enumerate(objects) if item.get('source_level','slide')=='slide'}
        for decor in decorations:
            shapes=tree.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(decor['shapeId']))
            decor['outlineOnly']=bool(shapes and shapes[0].find('p:spPr/a:noFill',NS) is not None)
            decor['drawingOrder']=order.get(decor['shapeId'],-1)
        # Inherited furniture remains unchanged, therefore still reserves space.
        for r in assignments.get(layout['id'],[]):
            if r['source_level']!='slide' and r['role'] in ('logo','footer','page_number'):
                b=box({'geometry':r.get('source_geometry') or {}})
                if b['w']>0 and b['h']>0:protected.append(b)
        # Stable identities and original geometry describe the actual composition,
        # not just a palette. Every native object remains in the source XML tree.
        texts=[s for s in layout['slots'] if 'cell' not in s and s['shapeId'] not in keep]
        title=next((s for s in texts if s.get('role') in ('header','title')),texts[0] if texts else None)
        anchors=[dict(key='r_title' if s is title else 'r_'+s['key'],sourceKey=s['key'],shapeId=s['shapeId'],
            **{k:s[k] for k in ('x','y','w','h','size')},font=s.get('font','Arial'),color=s.get('color','#163248'),
            bold=s.get('bold',False),align=s.get('align','left'),role=s.get('role','body')) for s in texts]
        for anchor in anchors:
            anchor['verticalAlign']=inherited_vertical_alignment(by_id[anchor['shapeId']])
            shapes=tree.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(anchor['shapeId']))
            anchor['allowFrameOverflow']=bool(shapes and shapes[0].find('p:spPr/a:noFill',NS) is not None)
            transform=shapes[0].find('p:spPr/a:xfrm',NS) if shapes else None
            anchor['rotation']=float(transform.get('rot',0))/60000 if transform is not None else 0
            owner=container_for(anchor,objects,root)
            if owner:anchor['container']=owner
            margin_x=data['width']*.12; margin_y=data['height']*.12
            left=max(0,anchor['x']-margin_x);top=max(0,anchor['y']-margin_y)
            right=min(data['width'],anchor['x']+anchor['w']+margin_x)
            bottom=min(data['height'],anchor['y']+anchor['h']+margin_y)
            anchor['repairRegion']=owner or dict(x=left,y=top,w=max(1,right-left),h=max(1,bottom-top))
            anchor['minSize']=min(anchor['size'],max(10,data['height']*.022))
            anchor['underlayCandidates']=[d['shapeId'] for d in decorations
                if d['shapeId'] not in keep and d['drawingOrder']<order.get(anchor['shapeId'],-1)
                and (d['outlineOnly'] or intersection(anchor['repairRegion'],d['box'])>0)]
        for anchor in anchors:
            collision_repair_region(anchor,anchors,dict(x=0,y=0,w=data['width'],h=data['height']))
        retained=[dict(key='retained_'+str(s['shapeId']),value=s.get('text',''),**{k:s[k] for k in ('x','y','w','h')},font=s.get('font','Arial'),bold=s.get('bold',False)) for s in layout['slots'] if s['shapeId'] in keep and roles.get(s['shapeId'])!='page_number' and 'cell' not in s]
        from runtime.vector_charts import candidates
        value=dict(version=8,vectorCharts=candidates(root,layout['slots']),retainedTexts=retained,page=dict(x=0,y=0,w=data['width'],h=data['height']),fonts=fonts,colors=colors,
            keepShapeIds=keep,protectedBoxes=protected,pictures=pictures,tables=tables,
            textAnchors=anchors,decorations=decorations,
            charts=[dict(shapeId=c['shapeId'],key=c['key'],box=box(by_id[c['shapeId']]) if c['shapeId'] in by_id else {k:c[k] for k in ('x','y','w','h')}) for c in layout.get('charts',[])],
            examples=[dict(key=s['key'],font=s.get('font'),size=s['size'],color=s.get('color'),role=s['role']) for s in layout['slots'][:24]])
        value['fingerprint']=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
        # Derived hints must not invalidate already exported, editable scenes.
        add_source_contacts(value,layout['slots'],order)
        result[layout['id']]=value
    return result

def validate_design(profile,scene):
    if profile.get('version',1)<2:return []
    issues=[]
    anchors={s['key']:s for s in profile['textAnchors']}
    if len(scene['texts'])!=len(anchors) or {s['key'] for s in scene['texts']}!=set(anchors):raise ValueError('pptx_rebuild_source_fields_missing')
    for spec in scene['texts']:
        old=anchors[spec['key']]
        underlays=spec.get('allowUnderlayShapes',[])
        if len(set(underlays))!=len(underlays) or any(i not in old.get('underlayCandidates',[]) for i in underlays):
            raise ValueError('pptx_rebuild_invalid_underlay')
        if any(spec[k]!=old[k] for k in ('font','bold')):raise ValueError('pptx_rebuild_source_style_changed')
        alignment=spec.get('alignment',{})
        permit=isinstance(alignment,dict) and len(alignment.get('reason',''))>=5
        if spec['align']!=old['align'] and not (permit and alignment.get('horizontal')=='center' and spec['align']=='center'):raise ValueError('pptx_rebuild_source_alignment_changed')
        if spec.get('verticalAlign',old.get('verticalAlign','top'))!=old.get('verticalAlign','top') and not (permit and alignment.get('vertical')=='center' and spec.get('verticalAlign')=='center'):raise ValueError('pptx_rebuild_source_alignment_changed')
        if not old.get('minSize',max(min(old['size'],12),old['size']*.7))<=spec['size']<=old['size']:raise ValueError('pptx_rebuild_source_style_changed')
        if spec['color']!=old['color'] and (scene.get('colorRepairs',{}).get(spec['key'])!=spec['color'] or spec['color'] not in profile['colors']):raise ValueError('pptx_rebuild_source_style_changed')
        if intersection(old,spec)<=0 and not (old.get('repairRegion') and inside(spec,old['repairRegion'])):
            if profile.get('version',1)<5:raise ValueError('pptx_rebuild_source_anchor_lost')
            issues.append(dict(key=spec['key'],reason='overflow',details=['rebuild_source_anchor_lost'],
                frame={k:spec[k] for k in ('x','y','w','h')},allowedRegion=old.get('repairRegion',old),
                instruction='Перемести рамку внутрь allowedRegion, сохраняя её смысловой блок.'))
        for decor in profile['decorations']:
            b=decor['box']
            if source_line_contact(profile,spec,dict(b,shapeId=decor['shapeId'])):continue
            # AI may identify source decoration as a background/empty outline.
            # Only objects behind this text are eligible; foreground objects,
            # photos, charts and protected furniture cannot be waived.
            if decor['shapeId'] in underlays and (decor.get('outlineOnly') or inside(spec,b)):
                continue
            # The inferred owner is a painted background, not an obstruction.
            # A source frame may already protrude slightly outside its owner.
            if decor['shapeId']==old.get('container',{}).get('shapeId'):
                conflict=not inside(spec,b);detail='rebuild_outside_source_container'
            elif inside(old,b):
                conflict = not inside(spec,b)
                detail = 'rebuild_outside_source_container'
            else:
                conflict = intersection(spec,b)>intersection(old,b)+.5
                detail = 'rebuild_design_overlap'
            if conflict:
                issues.append(dict(key=spec['key'],reason='overflow',details=[detail],
                    frame={k:spec[k] for k in ('x','y','w','h')},blocker=b,blockerShapeId=decor['shapeId'],
                    instruction='Уменьши или перемести рамку внутри исходной области. Если места мало, сократи текст либо умеренно уменьши шрифт; не перекрывай соседний объект.'))
    for kind in ('pictures','charts'):
        for item in scene[kind]:
            old=next(o for o in profile[kind] if o['shapeId']==item['shapeId'])['box']
            if any(abs(item[k]-old[k])>.01 for k in ('x','y','w','h')):raise ValueError('pptx_rebuild_design_object_moved')
    return issues

def source_box(profile,spec):
    if spec.get('key'):
        return next((a for a in profile.get('textAnchors',[]) if a['key']==spec['key']),None)
    if spec.get('shapeId'):
        return next((a.get('box') for kind in ('pictures','charts','tables') for a in profile[kind] if a['shapeId']==spec['shapeId']),None)
    return spec

def fields(scene):
    result=list(scene['texts'])
    for table in scene['tables']:
        rows=table['rows'];cols=len(rows[0]);w=table['w']/cols;h=table['h']/len(rows)
        for r,row in enumerate(rows):
            for c,key in enumerate(row):result.append(dict(key=key,x=table['x']+c*w+5,y=table['y']+r*h+4,w=w-10,h=h-8,
                font=table['font'],size=table['size'],bold=r==0,color=table['color'],align='left') | table.get('cellStyles',{}).get(key,{}))
    return result

def validate(profile,native,index):
    scene=native['rebuild']
    from runtime.vector_charts import validate_plans
    validate_plans(profile,scene,native['fields'])
    if scene.get('version')!=1 or scene.get('fingerprint')!=profile['fingerprint']:raise ValueError('pptx_rebuild_profile_changed')
    design_issues=validate_design(profile,scene)
    if not 1<=len(scene['texts'])<=80 or len(scene['tables'])>8:raise ValueError('pptx_rebuild_limit')
    for name in ('pictures','tables','charts'):
        expected={o['shapeId'] for o in profile[name]};items=scene[name]
        if len(items)!=len(expected) or {o['shapeId'] for o in items}!=expected:raise ValueError('pptx_rebuild_native_object_missing')
    for table in scene['tables']:
        original=next(t for t in profile['tables'] if t['shapeId']==table['shapeId'])
        from runtime.tables import MAX_TABLE_ROWS
        columns=len(table['rows'][0])
        if not 2<=len(table['rows'])<=MAX_TABLE_ROWS or not 1<=columns<=original['columns'] or any(len(r)!=columns for r in table['rows']):raise ValueError('pptx_rebuild_table_grid')
    issues=[dict(slide=index,**issue) for issue in design_issues]
    boxes=scene['texts']+scene['tables']+scene['pictures']+scene['charts']
    for i,b in enumerate(boxes):
        if any(type(b.get(k)) not in (int,float) or not math.isfinite(b[k]) for k in ('x','y','w','h')) or b['w']<=0 or b['h']<=0:raise ValueError('pptx_rebuild_outside_page')
        if not inside(b,profile['page']):
            if profile.get('version',1)<5:raise ValueError('pptx_rebuild_outside_page')
            issues.append(dict(slide=index,key=b.get('key'),reason='overflow',details=['rebuild_outside_page'],frame={k:b[k] for k in ('x','y','w','h')},allowedRegion=profile['page']))
        for o in profile['protectedBoxes']+boxes[:i]:
            if intersection(b,o)<=.5:continue
            if source_line_contact(profile,b,o) or source_line_contact(profile,o,b):continue
            if profile.get('version',1)>=2:
                original_b=source_box(profile,b);original_o=source_box(profile,o)
                # A source card/photo behind its caption is intentional. Retain
                # this containment instead of forbidding the original design.
                if original_b and original_o:
                    if inside(original_b,original_o) and inside(b,o):continue
                    if inside(original_o,original_b) and inside(o,b):continue
                    if intersection(b,o)<=intersection(original_b,original_o)+.5:continue
                target=b if 'key' in b else o
                issues.append(dict(slide=index,key=target.get('key'),reason='overflow',details=['rebuild_overlap'],
                    frame={k:target.get(k) for k in ('x','y','w','h')},blocker=o if target is b else b,
                    instruction='Сохрани структуру слайда; уменьши рамку или коротко перефразируй текст, не заходя на соседний объект.'))
            else:raise ValueError('pptx_rebuild_overlap')
    for pic in scene['pictures']:
        original=next(o['box'] for o in profile['pictures'] if o['shapeId']==pic['shapeId'])
        if abs((pic['w']/pic['h'])/(original['w']/original['h'])-1)>.03:raise ValueError('pptx_rebuild_image_distorted')
    specs=fields(scene);keys=[s['key'] for s in specs]
    if len(set(keys))!=len(keys) or set(keys)!=set(native['fields']) or not any(s['key']=='r_title' for s in specs):raise ValueError('pptx_rebuild_fields')
    fitter=TemplateTextFitter()
    for s in specs:
        if not re.fullmatch(r'r_[A-Za-z0-9_]+',s['key']) or s['font'] not in profile['fonts'] or s['color'] not in profile['colors'] or type(s['size']) not in (int,float) or not 1<=s['size']<=144 or s['align'] not in ('left','center','right'):raise ValueError('pptx_rebuild_style')
        value=native['fields'][s['key']]
        if not isinstance(value,str) or len(value)>4000:raise ValueError('pptx_invalid_text')
        report=fitter.fit(text_frame(s),value.split('\n'))
        if report.status!='fits':
            from runtime.glyph_repair import replacement_guidance
            issues.append(dict(slide=index,key=s['key'],reason=report.status,details=report.reason_codes,
            measuredHeightPt=round(report.measured_height_emu/12700,2),availableHeightPt=round(report.available_height_emu/12700,2),
            measuredWidthPt=round(report.measured_width_emu/12700,2),availableWidthPt=round(report.available_width_emu/12700,2),
            **(replacement_guidance(s['font'],s['bold'],value) if 'native_source_font_missing_glyph' in report.reason_codes else {}),
            instruction='Увеличь рамку в свободное место или сократи текст, сохраняя факты и цель слайда.'))
    return issues

def reposition(shape,b):
    tag='p:xfrm' if shape.tag=='{%s}graphicFrame'%NS['p'] else 'p:spPr/a:xfrm'
    x=shape.find(tag,NS)
    if x is None:raise ValueError('pptx_rebuild_transform_missing')
    x.attrib.clear()
    for key,a,attr in [('x','a:off','x'),('y','a:off','y'),('w','a:ext','cx'),('h','a:ext','cy')]:x.find(a,NS).set(attr,str(round(b[key]*12700)))

def straightened_frame(shape, spec):
    """Invert the actual parent transforms, not the rotated source AABB.

    A rotated shape's AABB swaps width/height at 90 degrees. Using those as
    local scales moved the text away from the very frame sent to PDF QA.
    Keep group transforms and drawing order intact.
    """
    a,b,c,d,e,f=1.,0.,0.,1.,0.,0.
    for parent in shape.iterancestors():
        if parent.tag!='{%s}grpSp'%NS['p']:continue
        x=parent.find('p:grpSpPr/a:xfrm',NS)
        if x is None:continue
        off,ext,child,span=(x.find('a:'+name,NS) for name in ('off','ext','chOff','chExt'))
        if any(v is None for v in (off,ext,child,span)):continue
        sx=float(ext.get('cx'))/max(1,float(span.get('cx')))
        sy=float(ext.get('cy'))/max(1,float(span.get('cy')))
        ox,oy=float(off.get('x')),float(off.get('y'))
        cx,cy=ox+float(ext.get('cx'))/2,oy+float(ext.get('cy'))/2
        angle=math.radians(float(x.get('rot',0))/60000);co,si=math.cos(angle),math.sin(angle)
        fx=-1 if x.get('flipH') in ('1','true') else 1;fy=-1 if x.get('flipV') in ('1','true') else 1
        ga,gb,gc,gd=co*fx*sx,si*fx*sx,-si*fy*sy,co*fy*sy
        tx=ox-float(child.get('x'))*sx-cx;ty=oy-float(child.get('y'))*sy-cy
        ge,gf=cx+co*fx*tx-si*fy*ty,cy+si*fx*tx+co*fy*ty
        a,b,c,d,e,f=ga*a+gc*b,gb*a+gd*b,ga*c+gc*d,gb*c+gd*d,ga*e+gc*f+ge,gb*e+gd*f+gf
    det=a*d-b*c;size_det=abs(a*d)-abs(b*c)
    if abs(det)<1e-12 or abs(size_det)<1e-12:return None
    px=(spec['x']+spec['w']/2)*12700-e;py=(spec['y']+spec['h']/2)*12700-f
    cx,cy=(d*px-c*py)/det,(-b*px+a*py)/det
    w=(abs(d)*spec['w']-abs(c)*spec['h'])*12700/size_det
    h=(abs(a)*spec['h']-abs(b)*spec['w'])*12700/size_det
    if min(w,h)<=0:return None
    return dict(x=(cx-w/2)/12700,y=(cy-h/2)/12700,w=w/12700,h=h/12700)

def add_table(tree,identity,table,values):
    frame=node(tree,'p:graphicFrame');nv=node(frame,'p:nvGraphicFramePr');node(nv,'p:cNvPr',id=identity,name='Nerpa rebuilt table');node(nv,'p:cNvGraphicFramePr');node(nv,'p:nvPr');geometry(frame,table,'p:xfrm')
    gd=node(node(frame,'a:graphic'),'a:graphicData',uri='http://schemas.openxmlformats.org/drawingml/2006/table');tbl=node(gd,'a:tbl');node(tbl,'a:tblPr',firstRow=1,bandRow=1)
    rows=table['rows'];cols=len(rows[0]);grid=node(tbl,'a:tblGrid')
    for _ in range(cols):node(grid,'a:gridCol',w=round(table['w']/cols*12700))
    for r,row in enumerate(rows):
        tr=node(tbl,'a:tr',h=round(table['h']/len(rows)*12700))
        for key in row:
            tc=node(tr,'a:tc');body=node(tc,'a:txBody');node(body,'a:bodyPr');node(body,'a:lstStyle')
            for line in values[key].split('\n'):
                p=node(body,'a:p');pp=node(p,'a:pPr');node(node(pp,'a:lnSpc'),'a:spcPct',val=110000);run=node(p,'a:r');pr=node(run,'a:rPr',sz=round(table['size']*100),b=int(r==0));solid(pr,table['color']);node(pr,'a:latin',typeface=table['font']);node(run,'a:t').text=line
            props=node(tc,'a:tcPr',marL=63500,marR=63500,marT=50800,marB=50800)
            for edge in ('L','R','T','B'):solid(node(props,'a:ln'+edge,w=6350),table['color'])

def apply(root,profile,native):
    if profile.get('version',1)>=2:
        return apply_source_design(root,profile,native)
    scene=native['rebuild'];tree=root.find('p:cSld/p:spTree',NS);saved={}
    for child in list(tree):
        if child.tag not in {'{%s}%s'%(NS['p'],name) for name in ('sp','pic','graphicFrame','grpSp','cxnSp')}:continue
        ids=[int(n.get('id')) for n in child.findall('.//p:cNvPr',NS)]
        if not ids:continue
        for identity in ids:saved[identity]=deepcopy(child)
        if ids[0] not in profile['keepShapeIds']:tree.remove(child)
    for timing in root.findall('p:timing',NS):root.remove(timing)
    # Reuse real pictures and editable charts with their existing relationships.
    for spec in scene['pictures']+scene['charts']:
        shape=saved.get(spec['shapeId'])
        if shape is None:raise ValueError('pptx_rebuild_object_missing')
        reposition(shape,spec);tree.append(shape)
    identity=max([int(n.get('id')) for n in root.findall('.//p:cNvPr',NS)]+[1])+1
    for table in scene['tables']:add_table(tree,identity,table,native['fields']);identity+=1
    for spec in scene['texts']:add_text(tree,identity,spec,native['fields'][spec['key']]);identity+=1

def apply_source_design(root,profile,native):
    """Edit native objects in place: retain fills, lines, connectors, groups and z-order."""
    scene=native['rebuild'];validate_design(profile,scene)
    from runtime.vector_charts import apply_plans
    apply_plans(root,profile,native)
    anchors={s['key']:s for s in profile['textAnchors']}
    for spec in scene['texts']:
        old=anchors[spec['key']]
        matches=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(old['shapeId']))
        if len(matches)!=1:raise ValueError('pptx_rebuild_source_object_missing')
        shape=matches[0];x=shape.find('p:spPr/a:xfrm',NS)
        if x is None:
            props=shape.find('p:spPr',NS);geometry(props,old);x=props.find('a:xfrm',NS);props.remove(x);props.insert(0,x)
        if 'rotation' in spec:
            if spec['rotation']!=0:raise ValueError('pptx_rebuild_rotation_unsupported')
            x.set('rot','0')
        off=x.find('a:off',NS);ext=x.find('a:ext',NS)
        straight=straightened_frame(shape,spec) if spec.get('rotation')==0 else None
        # Convert page-space deltas into the local coordinates of a nested group.
        for pos,size,attr in [('x','w','cx'),('y','h','cy')]:
            if straight is not None:
                off.set(pos,str(round(straight[pos]*12700)));ext.set(attr,str(round(straight[size]*12700)));continue
            scale=int(ext.get(attr))/old[size] if old[size]>0 else 12700
            off.set(pos,str(round(int(off.get(pos))+(spec[pos]-old[pos])*scale)))
            ext.set(attr,str(round(spec[size]*scale)))
        temp=etree.Element('temp');add_text(temp,1,{**spec,'verticalAlign':spec.get('verticalAlign',old.get('verticalAlign','top'))},native['fields'][spec['key']])
        body=temp[0].find('p:txBody',NS);previous=shape.find('p:txBody',NS)
        if previous is not None:shape.replace(previous,body)
        else:shape.append(body)
    for spec in scene['tables']:
        frames=root.xpath('.//p:graphicFrame[p:nvGraphicFramePr/p:cNvPr/@id=$id]',namespaces=NS,id=str(spec['shapeId']))
        if len(frames)!=1:raise ValueError('pptx_rebuild_source_table_missing')
        frame=frames[0];reposition(frame,spec);table=frame.find('.//a:tbl',NS)
        if len(spec['rows'][0]) < len(table.findall('a:tblGrid/a:gridCol',NS)):
            from runtime.tables import resize_table_columns
            resize_table_columns(table,len(spec['rows'][0]))
        rows=table.findall('a:tr',NS);template=deepcopy(rows[-1])
        while len(rows)>len(spec['rows']):table.remove(rows.pop())
        while len(rows)<len(spec['rows']):
            row=deepcopy(template);table.append(row);rows.append(row)
        for col in table.findall('a:tblGrid/a:gridCol',NS):col.set('w',str(round(spec['w']/len(spec['rows'][0])*12700)))
        for row,keys in zip(rows,spec['rows']):
            row.set('h',str(round(spec['h']/len(rows)*12700)))
            for cell,key in zip(row.findall('a:tc',NS),keys):
                # Recomposition explicitly supplies one independent value per
                # grid cell. Source title-row merges must not hide new headers.
                for attr in ('gridSpan','rowSpan','hMerge','vMerge'):cell.attrib.pop(attr,None)
                field=next(s for s in fields(scene) if s['key']==key)
                temp=etree.Element('temp');add_text(temp,1,field,native['fields'][key])
                body=temp[0].find('p:txBody',NS);body.tag='{%s}txBody'%NS['a']
                cell.replace(cell.find('a:txBody',NS),body)
