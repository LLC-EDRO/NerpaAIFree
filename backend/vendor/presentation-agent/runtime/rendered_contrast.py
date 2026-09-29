"""One bounded, measured colour repair. Copy and geometry never change."""
from copy import deepcopy
import json,re,zipfile
from lxml import etree
import pymupdf
from PIL import Image
from runtime.security import NS,xml,read_package
from runtime.render_quality import located,page_characters,normalized
from runtime.contrast import luminance


def page_color_context(page):
    # Remove text only in a disposable PDF, retaining photos and vector artwork.
    doc=pymupdf.open();doc.insert_pdf(page.parent,from_page=page.number,to_page=page.number)
    clean=doc[0];clean.add_redact_annot(clean.rect,fill=False)
    clean.apply_redactions(images=0,graphics=0,text=0)
    pix=clean.get_pixmap(matrix=pymupdf.Matrix(2,2),colorspace=pymupdf.csRGB,alpha=False)
    raster=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
    spans={s['seqno']:s for s in page.get_texttrace()}
    doc.close()
    return dict(chars=page_characters(page),spans=spans,raster=raster)


def glyph_colors(page,value,area,palette,context=None):
    context=context or page_color_context(page)
    found=located(context['chars'],value,area)
    if not found or len(found)!=len(normalized(value)):return {}
    spans=context['spans'];raster=context['raster']
    changes={}
    for index,char in enumerate(found):
        if not char['char'].isalnum():continue
        span=spans[char['seq']];color=span.get('color',())
        if len(color)==1:color=color*3
        if len(color)!=3:continue
        current='#'+''.join(f'{max(0,min(255,round(v*255))):02X}' for v in color)
        bounds=char['box'] & page.rect
        if bounds.is_empty:continue
        crop=raster.crop((max(0,int(bounds.x0*2)),max(0,int(bounds.y0*2)),min(raster.width,int(bounds.x1*2)+1),min(raster.height,int(bounds.y1*2)+1)))
        if not crop.width or not crop.height:continue
        # Conservative luminance bounds over the entire background below this
        # glyph. A mixed dark/light photo often has no safe candidate; keep it.
        extrema=crop.getextrema()
        low=luminance('#'+''.join(f'{v[0]:02X}' for v in extrema))
        high=luminance('#'+''.join(f'{v[1]:02X}' for v in extrema))
        def contrast(c):
            light=luminance(c)
            if low<=light<=high:return 1
            return (light+.05)/(high+.05) if light>high else (low+.05)/(light+.05)
        if contrast(current)>=2:continue
        candidates=[c for c in palette if re.fullmatch(r'#[0-9a-fA-F]{6}',c) and contrast(c)>=3]
        if candidates:changes[index]=max(candidates,key=contrast)
    return changes


def recolor_body(body,changes):
    """Map normalized PDF positions back to exact native runs, retaining styling."""
    offset=0;changed=False
    for run in list(body.findall('.//a:r',NS)):
        node=run.find('a:t',NS)
        if node is None:continue
        groups=[]
        for char in node.text or '':
            n=len(normalized(char));colors={changes.get(i) for i in range(offset,offset+n)};offset+=n
            color=next(iter(colors)) if len(colors)==1 else None
            if groups and groups[-1][0]==color:groups[-1][1]+=char
            else:groups.append([color,char])
        if not any(c for c,_ in groups):continue
        parent=run.getparent();at=parent.index(run)
        for color,value in groups:
            clone=deepcopy(run);clone.find('a:t',NS).text=value
            if color:
                props=clone.find('a:rPr',NS)
                if props is None:props=etree.Element('{%s}rPr'%NS['a']);clone.insert(0,props)
                for fill in props.xpath('./a:solidFill | ./a:gradFill | ./a:noFill | ./a:pattFill | ./a:blipFill | ./a:grpFill',namespaces=NS):props.remove(fill)
                fill=etree.Element('{%s}solidFill'%NS['a']);etree.SubElement(fill,'{%s}srgbClr'%NS['a'],val=color[1:])
                props.insert(1 if props.find('a:ln',NS) is not None else 0,fill)
            parent.insert(at,clone);at+=1
        parent.remove(run);changed=True
    return changed


def repair_rendered_contrast(folder,slides,pptx_path,pdf_path,quality):
    pages={p['slide']:p for p in quality['pages'] if p.get('warnings')}
    if not pages:return []
    analysis=json.loads((folder/'analysis.json').read_text());layouts={l['id']:l for l in analysis['layouts']}
    palette=sorted({s.get('color','#000000') for l in layouts.values() for s in l['slots']}|set(analysis.get('styleProfile',{}).get('colors',[])))
    parts=read_package(pptx_path.read_bytes());changes=[]
    with pymupdf.open(pdf_path) as pdf:
        for index,report in pages.items():
            native=slides[index]['native']
            if native.get('mode') not in ('source','rebuild'):continue
            layout=layouts[native['sourceSlideId']];part=f'ppt/slides/nerpaSlide{index+1}.xml'
            slots=layout['slots']
            if native.get('rebuild'):
                from runtime.rebuild import profiles
                profile=profiles(folder)[native['sourceSlideId']]
                slots=rebuilt_contrast_slots(profile,native)
            root=xml(parts[part]);updated=False;color_context=None
            for warning in report['warnings']:
                if warning['reason']!='rendered_text_low_contrast':continue
                if 'color' in slides[index].get('textStyles',{}).get('native.fields.'+warning['key'],{}):continue
                slot=next((s for s in slots if s['key']==warning['key']),None)
                if not slot or 'cell' in slot or slot.get('role') in ('logo','brand','footer','page_number','decoration'):continue
                nodes=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]/p:txBody',namespaces=NS,id=str(slot['shapeId']))
                if len(nodes)!=1 or nodes[0].find('.//a:fld',NS) is not None:continue
                value=native['fields'][slot['key']]
                if normalized(''.join(nodes[0].xpath('.//a:t/text()',namespaces=NS)))!=normalized(value):continue
                sx=pdf[index].rect.width/analysis['width'];sy=pdf[index].rect.height/analysis['height']
                frame=native.get('geometryRepairs',{}).get(slot['key'],slot)
                area=pymupdf.Rect(frame['x']*sx,frame['y']*sy,(frame['x']+frame['w'])*sx,(frame['y']+frame['h'])*sy)
                if color_context is None:color_context=page_color_context(pdf[index])
                colors=glyph_colors(pdf[index],value,area,palette,color_context)
                if colors and recolor_body(nodes[0],colors):
                    updated=True;changes.append(dict(slide=index+1,key=slot['key'],reason='rendered_glyph_contrast',characters=len(colors),colors=sorted(set(colors.values()))))
            if updated:parts[part]=etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    if changes:
        with zipfile.ZipFile(pptx_path,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,data in parts.items():archive.writestr(name,data)
    return changes

def rebuilt_contrast_slots(profile,native):
    """Bind measured rebuilt positions back to actual native text objects."""
    anchors={a['key']:a for a in profile.get('textAnchors',[])}
    return [dict(s,shapeId=anchors[s['key']]['shapeId'],role=anchors[s['key']].get('role','body'))
        for s in native['rebuild']['texts'] if s['key'] in anchors]
