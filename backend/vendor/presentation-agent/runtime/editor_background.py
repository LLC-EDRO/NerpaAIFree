"""Remove editable glyphs from a saved PDF, preserving images and vector artwork.

This is an editor-only bitmap. It never rewrites the saved PPTX or runs QA.
"""
from collections import Counter
from pathlib import Path
import pymupdf
from runtime.cached_blank import character_id
from runtime.render_quality import page_characters, located, normalized, rect


def editor_background(folder, request):
    output = Path(request['output'])
    output.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(Path(folder)/'previews/presentation.pdf') as source:
        page_number = request['page']
        if not isinstance(page_number, int) or not 0 <= page_number < len(source):
            raise ValueError('pptx_editor_page_invalid')
        with pymupdf.open() as doc:
            doc.insert_pdf(source, from_page=page_number, to_page=page_number)
            page = doc[0]
            chars = page_characters(page)
            selected = {}
            paths = []; appearances = {}; fonts = {}
            for field in request['fields']:
                if not normalized(field['text']):
                    paths.append(field['path'])
                    from runtime.editor_typography import empty_editor_typography
                    appearances[field['path']]=empty_editor_typography(field,output,fonts)
                    continue
                bounds = rect(field, page.rect.width/request['width'], page.rect.height/request['height'])
                found = located(chars, field['text'], bounds, set(), True)
                if not found:
                    continue  # Never erase another object's text by guessing a rectangle.
                for char in found:
                    selected[character_id(char)] = char
                paths.append(field['path'])
                from runtime.editor_typography import editor_typography
                appearances[field['path']]=editor_typography(page,found,field,page.rect.height/request['height'],output,fonts)
            for char in selected.values():
                area = char['box'] & page.rect
                if not area.is_empty:
                    page.add_redact_annot(area, fill=False)
            if selected:
                page.apply_redactions(images=0, graphics=0, text=0)
            expected = Counter(character_id(c) for c in chars)
            expected.subtract(selected.keys())
            if Counter(character_id(c) for c in page_characters(page)) != +expected:
                # A touching label must not disappear with the edited field.
                page = source[page_number]
                paths = []
            page.get_pixmap(matrix=pymupdf.Matrix(1600/page.rect.width, 1600/page.rect.width), alpha=False).save(output/'slide-0.png')
            return dict(paths=paths,appearances=appearances,fonts=list(fonts.values()))
