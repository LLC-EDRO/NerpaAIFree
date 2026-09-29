"""One small measured typography correction, accepted only after a fresh render.

Large collisions, page clipping, tables and unknown shapes still go to the AI.
No wording, colours, objects, diagram edges or source design are replaced.
"""
from copy import deepcopy
import math,zipfile
from lxml import etree
from runtime.security import NS,xml,read_package
from runtime.render_quality import normalized
from runtime.rebuild import profiles


def fitted_size(spec,anchor,issue):
    overflow=issue.get('overflowPt',{});frame=issue.get('frameBox',{})
    if not overflow or not frame or issue.get('details')!=['rendered_text_outside_frame']:return None
    if max(overflow.values(),default=0)>max(3,spec['size']*.3):return None
    ratios=[1.]
    for axis,a,b in [('w','left','right'),('h','top','bottom')]:
        extra=overflow.get(a,0)+overflow.get(b,0)
        # Keep the native anchor. Two-sided padding also covers vertically
        # centered text whose baseline differs slightly from the native fitter.
        if extra:ratios.append((frame[axis]-2*extra-1)/frame[axis])
    size=math.floor(spec['size']*min(ratios)*10)/10
    minimum=anchor.get('minSize',spec['size'])
    return size if max(minimum,spec['size']*.9)<=size<spec['size']-.1 else None


def font_repair_group(scene,profile,spec,size):
    group=next((g for g in profile.get('metricAlignmentGroups',[]) if spec['key'] in g['keys']),None)
    keys=group['keys'] if group else [spec['key']]
    peers=[next((t for t in scene['texts'] if t['key']==key),None) for key in keys]
    anchors=[next((a for a in profile['textAnchors'] if a['key']==key),None) for key in keys]
    if any(p is None for p in peers) or any(a is None for a in anchors):return []
    target=min(size,*(p['size'] for p in peers))
    if any(target<max(a.get('minSize',p['size']),p['size']*.9) for p,a in zip(peers,anchors)):return []
    return [(p,a,target) for p,a in zip(peers,anchors) if target<p['size']-.1]


def repair_rendered_fit(folder,slides,pptx_path,quality):
    candidate=deepcopy(slides);changes=[];parts=None;context=None;handled=set()
    for issue in quality['issues']:
        index=issue.get('slide');key=issue.get('key')
        if not isinstance(index,int) or not 0<=index<len(slides):continue
        if (index,key) in handled:continue
        if any(i is not issue and i.get('slide')==index and i.get('key')==key for i in quality['issues']):continue
        native=candidate[index]['native']
        if not native.get('rebuild'):continue
        spec=next((s for s in native['rebuild']['texts'] if s['key']==key),None)
        if spec is None:continue
        if context is None:context=profiles(folder)
        anchor=next((a for a in context[native['sourceSlideId']]['textAnchors'] if a['key']==key),None)
        if anchor is None or anchor.get('role') in ('brand','logo','footer','page_number'):continue
        size=fitted_size(spec,anchor,issue)
        if size is None:continue
        group=font_repair_group(native['rebuild'],context[native['sourceSlideId']],spec,size)
        if not group or any('native.fields.'+peer['key'] in candidate[index].get('textStyles',{}) for peer,_,_ in group):continue
        if parts is None:parts=read_package(pptx_path.read_bytes())
        part=f'ppt/slides/nerpaSlide{index+1}.xml';root=xml(parts[part])
        patches=[]
        for peer,source,target in group:
            bodies=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]/p:txBody',namespaces=NS,id=str(source['shapeId']))
            if len(bodies)!=1 or bodies[0].find('.//a:fld',NS) is not None:break
            body=bodies[0]
            if normalized(''.join(body.xpath('.//a:t/text()',namespaces=NS)))!=normalized(native['fields'][peer['key']]):break
            runs=body.xpath('.//a:rPr | .//a:defRPr | .//a:endParaRPr',namespaces=NS)
            if not runs or any('sz' in r.attrib and abs(int(r.get('sz'))/100-peer['size'])>.01 for r in runs):break
            patches.append((peer,target,runs))
        # Apply a coupled row atomically. Never shrink one side because the
        # peer's XML is unsupported; the existing warning path remains usable.
        if len(patches)!=len(group):continue
        for peer,target,runs in patches:
            for run in runs:run.set('sz',str(round(target*100)))
            changes.append(dict(slide=index+1,key=peer['key'],reason='measured_frame_fit',fromSize=peer['size'],toSize=target))
            peer['size']=target
            handled.add((index,peer['key']))
        parts[part]=etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    if changes:
        with zipfile.ZipFile(pptx_path,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,data in parts.items():archive.writestr(name,data)
    return candidate,changes
