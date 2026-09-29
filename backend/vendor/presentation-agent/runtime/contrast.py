"""Repair unreadable native copy against a proven flat source background."""
import re
from lxml import etree
from runtime.security import NS

def luminance(color):
    values=[int(color[i:i+2],16)/255 for i in (1,3,5)]
    linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in values]
    return sum(a*b for a,b in zip(linear,(.2126,.7152,.0722)))

def ratio(a,b):
    x,y=sorted((luminance(a),luminance(b)))
    return (y+.05)/(x+.05)

def readable_color(current,background,palette):
    if not re.fullmatch(r'#[0-9a-fA-F]{6}',current or '') or ratio(current,background)>=2:return None
    candidates=[c for c in palette if re.fullmatch(r'#[0-9a-fA-F]{6}',c or '') and ratio(c,background)>=4.5]
    return max(candidates,key=lambda c:ratio(c,background)) if candidates else None

def repair_source_contrast(root,layout,palette,pdf_path):
    if not pdf_path.exists():return []
    import pymupdf
    from PIL import Image
    from runtime.render_quality import contrast_background
    changes=[]
    with pymupdf.open(pdf_path) as pdf:
        if not 0<=layout['index']<len(pdf):return []
        page=pdf[layout['index']]
        pix=page.get_pixmap(matrix=pymupdf.Matrix(2,2),colorspace=pymupdf.csRGB,alpha=False)
        raster=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
        for slot in layout['slots']:
            if 'cell' in slot or slot.get('role') in ('logo','brand','decoration','page_number','footer'):continue
            nodes=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr/@id=$id]',namespaces=NS,id=str(slot['shapeId']))
            if len(nodes)!=1:continue
            body=nodes[0].find('p:txBody',NS)
            if body is None or not any(t.text and t.text.strip() for t in body.findall('.//a:t',NS)):continue
            area=pymupdf.Rect(slot['x'],slot['y'],slot['x']+slot['w'],slot['y']+slot['h'])&page.rect
            background=contrast_background(raster,area).get('backgroundColor')
            if not background:continue
            color=readable_color(slot.get('color'),background,palette)
            if not color:continue
            for props in body.xpath('.//a:rPr | .//a:defRPr | .//a:endParaRPr',namespaces=NS):
                # Preserve font, emphasis, effects and every run boundary.
                for fill in props.xpath('./a:solidFill | ./a:gradFill | ./a:noFill',namespaces=NS):props.remove(fill)
                fill=etree.Element('{%s}solidFill'%NS['a'])
                etree.SubElement(fill,'{%s}srgbClr'%NS['a'],val=color[1:])
                index=next((i for i,n in enumerate(props) if etree.QName(n).localname not in ('ln',)),len(props))
                props.insert(index,fill)
            changes.append(dict(key=slot['key'],before=slot['color'],after=color,background=background))
    return changes
