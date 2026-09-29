"""Erase only identified editable glyphs from the already rendered source PDF.

Ambiguous text or collateral deletion falls back to native PPTX rendering for
that page. Artwork, photos, protected labels and typography are never guessed.
"""
from collections import Counter
import pymupdf
from runtime.render_quality import page_characters,located,normalized,rect


def character_id(char):
    return (char['char'],char['font'],*(round(v,2) for v in char['box']))


def cached_blank_page(source,layout,profile,width,height,destination,frames=None):
    with pymupdf.open() as doc:
        doc.insert_pdf(source.parent,from_page=source.number,to_page=source.number)
        page=doc[0];chars=page_characters(page)
        editable={a['shapeId'] for a in profile['textAnchors']}|{t['shapeId'] for t in profile['tables']}
        selected={}
        for slot in layout['slots']:
            if slot['shapeId'] not in editable or not normalized(slot.get('text','')):continue
            paragraphs=((frames or {}).get(slot.get('key')) or {}).get('paragraphs',[])
            markers={normalized(p.get('bullet_text','')) for p in paragraphs if not p.get('bullet_auto')}
            markers.discard('')
            found=located(chars,slot['text'],rect(slot,page.rect.width/width,page.rect.height/height),markers,any(p.get('bullet_auto') for p in paragraphs))
            if not found:return False
            for char in found:selected[character_id(char)]=char
        for char in selected.values():
            area=char['box'] & page.rect
            if not area.is_empty:page.add_redact_annot(area,fill=False)
        if selected:page.apply_redactions(images=0,graphics=0,text=0)
        expected=Counter(character_id(c) for c in chars)
        expected.subtract(selected.keys())
        # Exact remaining glyph identities prevent erasing a touching brand or
        # footer, including in malformed templates with overlapping text.
        if Counter(character_id(c) for c in page_characters(page)) != +expected:return False
        page.get_pixmap(matrix=pymupdf.Matrix(1280/page.rect.width,1280/page.rect.width),alpha=False).save(destination)
        return True
