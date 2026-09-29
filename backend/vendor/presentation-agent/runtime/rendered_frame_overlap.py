"""Validate visible text instead of treating invisible editing boxes as ink."""
from runtime.security import NS,read_package,xml
from runtime.rebuild import profiles

def editing_frame(shape):
    # Explicitly unpainted only. Inherited/filled/outlined shapes stay strict.
    props=shape.find('p:spPr',NS)
    return props is not None and props.find('a:noFill',NS) is not None and props.find('a:ln/a:noFill',NS) is not None and props.find('a:effectLst/*',NS) is None and props.find('a:effectDag/*',NS) is None

def verified_pair(issue,clear_keys,render_issues):
    key=issue.get('key');other=issue.get('blocker',{}).get('key')
    return issue.get('details')==['rebuild_overlap'] and key in clear_keys and other in clear_keys and not any(i.get('key') in (key,other) for i in render_issues)

def resolve_editing_frame_overlaps(folder,slides,pptx_path,native_issues,quality):
    if not any(i.get('details')==['rebuild_overlap'] for i in native_issues):return native_issues,[]
    # Missing/failed QA is not evidence of a collision-free render.
    checked={p['slide'] for p in quality.get('pages',[]) if p.get('recomposed') and p.get('checkedFields',0)>0}
    context=profiles(folder);parts=read_package(pptx_path.read_bytes());clear={};remaining=[];resolved=[]
    for issue in native_issues:
        index=issue.get('slide')
        if index not in checked:remaining.append(issue);continue
        if index not in clear:
            native=slides[index]['native'];root=xml(parts[f'ppt/slides/nerpaSlide{index+1}.xml'])
            anchors=context[native['sourceSlideId']].get('textAnchors',[])
            clear[index]=set()
            for anchor in anchors:
                nodes=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(anchor['shapeId']))
                if len(nodes)==1 and editing_frame(nodes[0]):clear[index].add(anchor['key'])
        page_issues=[i for i in quality['issues'] if i.get('slide')==index]
        if verified_pair(issue,clear[index],page_issues):
            resolved.append(dict(slide=index,key=issue['key'],otherKey=issue['blocker']['key'],reason='transparent_frame_overlap_verified'))
        else:remaining.append(issue)
    return remaining,resolved
