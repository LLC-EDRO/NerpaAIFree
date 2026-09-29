"""Typography of the delivered PDF, including renderer autofit, for the editor."""
from collections import Counter
from hashlib import sha256
from io import BytesIO
import statistics
from fontTools.ttLib import TTFont
from runtime.fonts import TemplateFontResolver, font_metadata


def editor_font(name, output, fonts):
    resolver=TemplateFontResolver()
    face=resolver.resolve(name)
    if not face.path:
        return name
    record=font_metadata(face.path)
    family='NerpaEditor-'+sha256(record['family'].encode()).hexdigest()[:16]
    for candidate in (face,resolver.resolve(record['family'],bold=True)):
        if not candidate.path:continue
        metadata=font_metadata(candidate.path)
        with TTFont(candidate.path,fontNumber=0,recalcTimestamp=False) as font:
            if getattr(font.get('OS/2'),'fsType',0)&2:continue
            stream=BytesIO();font.save(stream);data=stream.getvalue()
        key=sha256(data).hexdigest()+'.ttf'
        (output/key).write_bytes(data)
        fonts[key]=dict(key=key,family=family,weight=metadata['weight'],style='italic' if metadata['italic'] else 'normal')
    return family if any(f['family']==family for f in fonts.values()) else name


def editor_typography(page,found,field,scale,output,fonts):
    sequences=Counter(c['seq'] for c in found)
    spans={s['seqno']:s for s in page.get_texttrace()}
    span=spans[sequences.most_common(1)[0][0]]
    size=span['size']/scale
    origins=sorted(set(round(c['origin'][1]/scale,3) for c in found))
    gaps=[b-a for a,b in zip(origins,origins[1:]) if b-a>size*.5]
    line_height=statistics.median(gaps)/size if gaps else 1.08
    color=span.get('color',(0,0,0))
    if isinstance(color,(int,float)):color=(color,)*3
    if len(color)==1:color=color*3
    bottom=field['y']+field['h']-max(c['origin'][1]/scale for c in found)
    return dict(font=editor_font(span['font'],output,fonts),sourceFont=span['font'],size=size,
                bold=bool(span.get('flags',0)&16),color='#'+''.join(f'{round(max(0,min(1,c))*255):02X}' for c in color[:3]),
                lineHeight=line_height,baselineTop=min(c['origin'][1]/scale for c in found)-field['y'],
                baselineBottom=bottom,verticalAlign=field.get('verticalAlign','top'),
                insetX=max(0,(field['w']-field.get('usableWidth',field['w']))/2))


def empty_editor_typography(field,output,fonts):
    size=field.get('size',20)
    return dict(font=editor_font(field.get('font','Liberation Sans'),output,fonts),size=size,
                bold=bool(field.get('bold')),color=field.get('color','#000000'),lineHeight=1.08,
                baselineTop=size*.9,baselineBottom=size*.18,verticalAlign=field.get('verticalAlign','top'),
                insetX=max(0,(field['w']-field.get('usableWidth',field['w']))/2))
