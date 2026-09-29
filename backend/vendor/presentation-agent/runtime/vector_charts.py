"""Bounded editable ring/pie repair. AI binds labels; geometry follows values.

Candidates require percentage labels and a single near-circular group of
closed curved sectors. Arbitrary art, logos and unsupported graphs are never
reinterpreted merely because they contain several coloured shapes.
"""
import math,re
from lxml import etree
from runtime.security import NS

def percentages(text):
    return [float(v.replace(',','.')) for v in re.findall(r'(?<![\d.,])([0-9]{1,3}(?:[.,][0-9]+)?)\s*%',text)]

def candidates(root,slots):
    labels=[dict(key=s['key'],text=s.get('text','')) for s in slots if len(percentages(s.get('text','')))==1]
    if not 2<=len(labels)<=12 or abs(sum(percentages(s['text'])[0] for s in labels)-100)>2:return []
    result=[]
    for group in root.findall('.//p:grpSp',NS):
        shapes=group.findall('p:sp',NS)
        if len(shapes)!=len(labels) or group.findall('p:grpSp',NS) or group.findall('p:pic',NS):continue
        transform=group.find('p:grpSpPr/a:xfrm',NS)
        extent=transform.find('a:chExt',NS) if transform is not None else None
        if extent is None:continue
        w,h=int(extent.get('cx',0)),int(extent.get('cy',0))
        if not w or not h or not .8<w/h<1.25:continue
        members=[]
        for shape in shapes:
            paths=shape.findall('p:spPr/a:custGeom/a:pathLst/a:path',NS)
            if (len(paths)!=1 or shape.find('p:spPr/a:solidFill',NS) is None
                or any((t.text or '').strip() for t in shape.findall('.//a:t',NS))):break
            path=paths[0]
            if any(path.find('a:'+tag,NS) is None for tag in ('close','lnTo','cubicBezTo')):break
            identity=shape.find('p:nvSpPr/p:cNvPr',NS)
            color=shape.find('p:spPr/a:solidFill/a:srgbClr',NS)
            if color is None:break
            members.append(dict(shapeId=int(identity.get('id')),color='#'+color.get('val') if color is not None else None))
        if len(members)==len(shapes):
            result.append(dict(groupId=int(group.find('p:nvGrpSpPr/p:cNvPr',NS).get('id')),sectors=members,sourceLabels=labels))
    # Several candidate groups sharing one label set are ambiguous. Do not
    # guess associations or reshape arbitrary decoration.
    return result if len(result)==1 else []

def values_for(plan,fields):
    values=[]
    for binding in plan['bindings']:
        parsed=percentages(fields.get(binding['fieldKey'],''))
        if len(parsed)!=1 or not 0<=parsed[0]<=100:raise ValueError('pptx_vector_chart_value_ambiguous')
        values.append(parsed[0])
    if abs(sum(values)-100)>.2:raise ValueError('pptx_vector_chart_total')
    return values

def validate_plans(profile,scene,fields):
    expected=profile.get('vectorCharts',[]);plans=scene.get('graphicCharts',[])
    if len(plans)!=len(expected):raise ValueError('pptx_vector_chart_mapping_missing')
    for plan in plans:
        candidate=next((c for c in expected if c['groupId']==plan['groupId']),None)
        if candidate is None:raise ValueError('pptx_vector_chart_unknown_group')
        ids=[b['shapeId'] for b in plan['bindings']];keys=[b['fieldKey'] for b in plan['bindings']]
        allowed_keys={a['key'] for a in profile['textAnchors'] if a['sourceKey'] in {s['key'] for s in candidate['sourceLabels']}}
        if (len(ids)!=len(set(ids)) or set(ids)!={s['shapeId'] for s in candidate['sectors']}
            or len(keys)!=len(set(keys)) or set(keys)!=allowed_keys):raise ValueError('pptx_vector_chart_invalid_binding')
        if plan['kind'] not in ('pie','donut') or not 0<=plan['holeRatio']<=.85:raise ValueError('pptx_vector_chart_invalid_kind')
        values_for(plan,fields)

def apply_plans(root,profile,native):
    scene=native['rebuild'];validate_plans(profile,scene,native['fields'])
    for plan in scene.get('graphicCharts',[]):
        group=root.xpath('.//p:grpSp[p:nvGrpSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(plan['groupId']))[0]
        transform=group.find('p:grpSpPr/a:xfrm',NS)
        origin=transform.find('a:chOff',NS);extent=transform.find('a:chExt',NS)
        w,h=int(extent.get('cx')),int(extent.get('cy'));diameter=min(w,h)
        x=int(origin.get('x'))+(w-diameter)/2;y=int(origin.get('y'))+(h-diameter)/2
        angle=-math.pi/2;values=values_for(plan,native['fields'])
        inner=plan['holeRatio'] if plan['kind']=='donut' else 0
        for binding,value in zip(plan['bindings'],values):
            shape=group.xpath('./p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(binding['shapeId']))[0]
            props=shape.find('p:spPr',NS);xf=props.find('a:xfrm',NS)
            xf.find('a:off',NS).attrib.update(dict(x=str(round(x)),y=str(round(y))))
            xf.find('a:ext',NS).attrib.update(dict(cx=str(diameter),cy=str(diameter)))
            for attr in ('rot','flipH','flipV'):xf.attrib.pop(attr,None)
            end=angle+2*math.pi*value/100;steps=max(1,math.ceil(value*1.2))
            angles=[angle+(end-angle)*i/steps for i in range(steps+1)]
            points=[(.5+.5*math.cos(a),.5+.5*math.sin(a)) for a in angles]
            points+=([(.5+.5*inner*math.cos(a),.5+.5*inner*math.sin(a)) for a in reversed(angles)] if inner else [(.5,.5)])
            geometry=props.find('a:custGeom',NS)
            paths=geometry.find('a:pathLst',NS)
            for child in list(paths):paths.remove(child)
            path=etree.SubElement(paths,'{%s}path'%NS['a'],w='1000000',h='1000000')
            for i,(px,py) in enumerate(points):
                command=etree.SubElement(path,'{%s}%s'%(NS['a'],'moveTo' if i==0 else 'lnTo'))
                etree.SubElement(command,'{%s}pt'%NS['a'],x=str(round(px*1000000)),y=str(round(py*1000000)))
            etree.SubElement(path,'{%s}close'%NS['a']);angle=end
            # Explicit colour keys make labels a legend; their old angular
            # positions no longer falsely imply the old sector proportions.
            from runtime.legacy.composition import add_shape
            label=next(t for t in scene['texts'] if t['key']==binding['fieldKey'])
            tree=root.find('p:cSld/p:spTree',NS)
            identity=max(int(n.get('id')) for n in root.findall('.//p:cNvPr',NS))+1
            marker=min(7,label['size']*.4)
            if label['x']>=marker+3:
                color=props.find('a:solidFill/a:srgbClr',NS).get('val')
                add_shape(tree,identity,'Chart legend',dict(x=label['x']-marker-3,y=label['y']+label['size']*.35,w=marker,h=marker),'#'+color)
