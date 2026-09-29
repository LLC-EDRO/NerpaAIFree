"""One deck-wide visual context: preserve design, clear only replaceable text."""
import json, zipfile, hashlib, tempfile, shutil
from pathlib import Path
import pymupdf
from lxml import etree
from runtime.security import read_package, xml, NS
from runtime.fonts import effective_source
from runtime.rebuild import profiles
from runtime.render import render

def clear_editable_text(root, profile):
    ids={str(a['shapeId']) for a in profile['textAnchors']}
    ids.update(str(t['shapeId']) for t in profile['tables'])
    for shape in root.xpath('.//p:sp | .//p:graphicFrame',namespaces=NS):
        identity=shape.find('.//p:cNvPr',NS)
        if identity is not None and identity.get('id') in ids:
            for text in shape.findall('.//a:t',NS):text.text=''

def repair_context(folder, output, soffice):
    context=profiles(folder)
    analysis=json.loads((folder/'analysis.json').read_text())
    output.mkdir(parents=True,exist_ok=True)
    fallback=[]
    from runtime.cached_blank import cached_blank_page
    from runtime.frame_store import read_frames
    frames=read_frames(folder)
    try:
        with pymupdf.open(folder/'previews'/'presentation.pdf') as pdf:
            for index,layout in enumerate(analysis['layouts']):
                if not cached_blank_page(pdf[index],layout,context[layout['id']],analysis['width'],analysis['height'],output/f'slide-{index}.png',frames.get(layout['id'])):
                    fallback.append((index,layout['id']))
    except Exception:
        fallback=list(enumerate(l['id'] for l in analysis['layouts']))
    if not fallback:return dict(profiles=context,blankSlides=len(analysis['layouts']),cachedBlankSlides=len(analysis['layouts']),nativeBlankSlides=0)
    parts=read_package(effective_source(folder/'source.pptx',folder,analysis).read_bytes())
    for layout in analysis['layouts']:
        root=xml(parts[layout['part']]);clear_editable_text(root,context[layout['id']])
        parts[layout['part']]=etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    output.mkdir(parents=True,exist_ok=True)
    blank=output/'blank.pptx'
    with zipfile.ZipFile(blank,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,value in parts.items():archive.writestr(name,value)
    # Convert only unresolved pages. The immutable parsed geometry stays valid
    # after clearing text; no parser or analysis request is repeated here.
    from runtime.assemble import assemble
    with tempfile.TemporaryDirectory(prefix='blank-fallback-',dir=output.parent) as tmp:
        work=Path(tmp);metadata=dict(analysis,sha256=hashlib.sha256(blank.read_bytes()).hexdigest())
        metadata.pop('preparedSha256',None)
        (work/'analysis.json').write_text(json.dumps(metadata))
        assemble(blank,work,[identity for _,identity in fallback],work/'selected.pptx')
        count=render(work/'selected.pptx',work/'rendered',soffice)
        if count!=len(fallback):raise ValueError('pptx_preview_count_mismatch')
        for local,(index,_) in enumerate(fallback):shutil.copy2(work/'rendered'/f'slide-{local}.png',output/f'slide-{index}.png')
    return dict(profiles=context,blankSlides=len(analysis['layouts']),cachedBlankSlides=len(analysis['layouts'])-len(fallback),nativeBlankSlides=len(fallback))
