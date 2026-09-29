"""Inspect the actual PDF paint stream after Impress, not estimated PPTX boxes.

Only source-mode slides have a stable field contract. Diagnostics contain keys
and reason codes, never user copy. No provider calls, OCR or new slide objects.
Ambiguous artwork intersections are warnings, not rectangular-image false bans.
"""
import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from PIL import Image
import pymupdf
from runtime.fonts import TemplateFontResolver, font_metadata, effective_source
from runtime.geometry import inside, intersection, box
from runtime.layout_metadata import objects
from runtime.security import read_package, xml, NS
from runtime.paint_clipping import paint_clips
from runtime.tables import expanded_table_layout

VERSION = 15


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text).casefold()
                   if not c.isspace() and c not in '\u00ad\u200b\u200c\u200d\ufeff')


def rect(value, sx=1, sy=1):
    return pymupdf.Rect(value['x']*sx, value['y']*sy,
                        (value['x']+value['w'])*sx, (value['y']+value['h'])*sy)



def rebuilt_reserved(profile,scene,sx=1,sy=1):
    anchors=profile.get('textAnchors',[])
    reserved=[dict(shapeId=0,role='protected',rect=rect(b,sx,sy)) for b in profile['protectedBoxes']]
    reserved += [dict(shapeId=b['shapeId'],role='native_object',rect=rect(b,sx,sy),
        excludeKeys=[a['key'] for a in anchors if intersection(a,b)>.5]) for b in scene['pictures']+scene['charts']]
    for decor in profile.get('decorations',[]):
        excluded=[]
        for anchor in anchors:
            # Backgrounds behind an anchor are checked by validate_design
            # against the original composition. Their rectangular hull is not
            # painted foreground (e.g. a ring with labels crossing its hull).
            # Actual glyph clipping, contrast and foreground occlusion are
            # still measured by inspect_page.
            if inside(anchor,decor['box']) or decor['shapeId'] in anchor.get('underlayCandidates',[]):
                excluded.append(anchor['key'])
        reserved.append(dict(shapeId=decor['shapeId'],role='decoration',rect=rect(decor['box'],sx,sy),
            excludeKeys=excluded))
    return reserved

def grow(r, amount):
    return pymupdf.Rect(r.x0-amount, r.y0-amount, r.x1+amount, r.y1+amount)


def font_name(value):
    return re.sub(r'[^\w]', '', re.sub(r'^[A-Z]{6}\+', '', value)).casefold()


@lru_cache(maxsize=256)
def expected_fonts(family, bold, italic):
    resolved = TemplateFontResolver().resolve(family, bold=bold, italic=italic)
    return {font_name(n) for n in font_metadata(resolved.path)['aliases']} if resolved.path else set()


def page_characters(page):
    chars = []
    for span in page.get_texttrace():
        # Type 3 is invisible OCR/search text. A hidden layer is not delivery.
        if span['type'] == 3 or span.get('opacity', 1) < .05:
            continue
        for code, glyph, origin, bounds in span['chars']:
            text = normalized(chr(code))
            if not text:
                continue
            r = pymupdf.Rect(bounds)
            # Font em boxes include ascenders/descenders, not all of them ink.
            # Use their central region for high-confidence collision tests.
            ink = pymupdf.Rect(r.x0+.1*r.width, r.y0+.2*r.height, r.x1-.1*r.width, r.y1-.15*r.height)
            for c in text:
                chars.append(dict(char=c, box=r, ink=ink, seq=span['seqno'], font=span['font'],
                                  origin=origin, glyph=glyph, direction=span['dir']))
    return chars


def located(chars, value, area, paragraph_markers=(), numbered=False):
    # Keep paint order: it also handles rotated text and native paragraph order.
    selected = [c for c in chars if (c['box'] & grow(area, 3)).get_area() > c['box'].get_area()*.45]
    haystack = ''.join(c['char'] for c in selected)
    needle = normalized(value)
    start = haystack.find(needle)
    if start<0:
        # A renderer may insert a discretionary hyphen at a wrapped line end.
        # Never erase in-line hyphens in ranges/units to manufacture a match.
        alternative=[c for i,c in enumerate(selected) if not (c['char']=='-' and i+1<len(selected)
            and abs(c['direction'][1])<.01 and abs(selected[i+1]['origin'][1]-c['origin'][1])>2)]
        at=''.join(c['char'] for c in alternative).find(needle)
        if at>=0:return alternative[at:at+len(needle)]
    if start >= 0:
        return selected[start:start+len(needle)]
    # Native list markers are painted between paragraphs, outside editable text.
    # Match all paragraphs in order, permitting only source marker glyphs in gaps.
    paragraphs = [normalized(p) for p in value.split('\n') if normalized(p)]
    if (paragraph_markers or numbered) and len(paragraphs) > 1:
        cursor, found = 0, []
        for index, paragraph in enumerate(paragraphs):
            at = haystack.find(paragraph, cursor)
            if at < 0:
                return []
            gap = selected[cursor:at]
            if index and gap:
                bullet = all(c['char'] in paragraph_markers for c in gap)
                # Native Arabic list numbers are separate paint before each
                # paragraph. Accept only a number in the LEFT list gutter on
                # that paragraph's baseline, never arbitrary digits in copy.
                number = (numbered and re.fullmatch(r'(?:\d+[.)]?|\(\d+\))', ''.join(c['char'] for c in gap))
                          and all(abs(c['origin'][1]-selected[at]['origin'][1]) < .75 for c in gap)
                          and max(c['box'].x1 for c in gap) < selected[at]['box'].x0 - 1)
                if not bullet and not number:
                    return []
            found.extend(selected[at:at+len(paragraph)])
            cursor = at+len(paragraph)
        return found
    return []


def source_cell_fonts(chars, slot, area):
    # Table styles can inherit a theme face that the cached parser resolved to
    # a default. The source PDF is rendered in the same immutable environment;
    # matching its exact cell text gives the actual original face, not a guess.
    # Never infer from nearby cells or accept a font merely seen elsewhere.
    if 'cell' not in slot or not normalized(slot.get('text', '')):
        return set()
    return {font_name(c['font']) for c in located(chars, slot['text'], area)
            if c['char'].isalnum() and c['glyph'] != 0}


def blockers(page):
    """Only proven opaque foreground paint; never a transparent PNG's AABB."""
    result = []
    for d in page.get_drawings():
        items = d.get('items', [])
        bounds = None
        if len(items)==1 and items[0][0]=='re': bounds=pymupdf.Rect(items[0][1])
        elif len(items)>=4 and all(i[0]=='l' for i in items):
            # Impress often emits a rectangle as five line segments, starting
            # halfway along an edge, rather than the PDF `re` operator.
            points=[i[1] for i in items]+[items[-1][2]]
            closed=all(abs(items[i][2]-items[(i+1)%len(items)][1])<.05 for i in range(len(items)))
            area=abs(sum(a.x*b.y-b.x*a.y for a,b in zip(points,points[1:])))/2
            candidate=d['rect']
            if closed and abs(area-candidate.get_area())<.1:bounds=candidate
        if bounds is not None and d.get('fill') is not None and d.get('fill_opacity',1)>=.99:
            result.append((d['seqno'], bounds))
    masked = {i[0] for i in page.get_images(full=True) if i[1]}
    opaque = [pymupdf.Rect(i['bbox']) for i in page.get_image_info(xrefs=True)
              if i.get('xref') and i['xref'] not in masked]
    for seq, (kind, bounds) in enumerate(page.get_bboxlog()):
        r = pymupdf.Rect(bounds)
        if kind == 'fill-image' and any(all(abs(a-b)<.2 for a,b in zip(r,b)) for b in opaque):
            result.append((seq, r))
    if not result:
        return result
    clips = paint_clips(page, page.get_bboxlog())
    return [(seq, bounds & clips[seq]) for seq, bounds in result
            if clips.get(seq) is not None and not (bounds & clips[seq]).is_empty]


def contrast_background(raster,area):
    if raster is None or area.is_empty:return {}
    crop=raster.crop((max(0,int(area.x0*2)),max(0,int(area.y0*2)),min(raster.width,int(area.x1*2)),min(raster.height,int(area.y1*2))))
    if not crop.width or not crop.height:return {}
    colors=crop.getcolors(maxcolors=1024)
    if not colors:return {}
    count,rgb=max(colors,key=lambda item:item[0])
    coverage=count/(crop.width*crop.height)
    if coverage<.85:return {}
    return dict(backgroundColor='#' + ''.join(f'{v:02X}' for v in rgb),backgroundCoverage=round(coverage,3))


def inspect_page(page, contracts, charts=(), reserved=()):
    chars = page_characters(page)
    paint = page.get_bboxlog()
    run_lengths = {}
    for char in chars: run_lengths[char['seq']] = run_lengths.get(char['seq'], 0)+1
    covers = blockers(page)
    issues, warnings, matched = [], [], {}
    raster = None

    def indistinguishable(char):
        # Conservative visible-pixel test. The channel extrema give an UPPER
        # bound on local contrast, so textured/ambiguous artwork is not banned.
        # Include surrounding pixels: a narrow solid glyph is not an empty box.
        nonlocal raster
        if not char['char'].isalnum():return False
        if raster is None:
            pix=page.get_pixmap(matrix=pymupdf.Matrix(2,2),colorspace=pymupdf.csRGB,alpha=False)
            raster=Image.frombytes('RGB',(pix.width,pix.height),pix.samples)
        bounds=grow(char['box'],1.5)&page.rect
        # Off-page glyphs already produce rendered_text_outside_page below.
        # An empty intersection has reversed bounds in MuPDF; passing it to
        # Pillow raises ValueError and discards the entire repairable report.
        if bounds.is_empty:return False
        left=max(0,min(raster.width,int(bounds.x0*2)))
        top=max(0,min(raster.height,int(bounds.y0*2)))
        right=max(0,min(raster.width,int(bounds.x1*2)+1))
        bottom=max(0,min(raster.height,int(bounds.y1*2)+1))
        if right<=left or bottom<=top:return False
        crop=raster.crop((left,top,right,bottom))
        if not crop.width or not crop.height:return False
        ranges=crop.getextrema()
        def luminance(values):
            linear=[v/255/12.92 if v/255<=.04045 else ((v/255+.055)/1.055)**2.4 for v in values]
            return sum(v*w for v,w in zip(linear,(.2126,.7152,.0722)))
        low=luminance([v[0] for v in ranges]);high=luminance([v[1] for v in ranges])
        return (high+.05)/(low+.05)<1.5

    def issue(key, code, reason='unverifiable', value=''):
        if any(i['key']==key and code in i['details'] for i in issues):
            return
        issues.append(dict(key=key, reason=reason, details=[code],
                           **(dict(suggestedMaxChars=max(1, int(len(value)*.8))) if reason=='overflow' else {})))

    for spec in contracts:
        key, value, area = spec['key'], spec['value'], spec['rect']
        if not normalized(value):
            continue
        found = located(chars, value, area, spec.get('paragraphMarkers', ()), spec.get('numbered', False))
        if not found:
            issue(key, 'rendered_text_missing')
            continue
        matched[key] = found
        # Reserve the whole text block, including spaces between its lines.
        # Otherwise another caption/icon can sit between glyph baselines and
        # evade pixel-overlap checks while visibly breaking the composition.
        occupied = pymupdf.Rect(found[0]['ink'])
        for char in found[1:]: occupied |= char['ink']
        for obstacle in reserved:
            if key in obstacle.get('excludeKeys',()):continue
            overlap = occupied & obstacle['rect']
            if not overlap.is_empty and overlap.width > 1 and overlap.height > 1:
                issue(key, 'rendered_text_overlaps_reserved_object', 'overflow', value)
                next(i for i in issues if i['key']==key and 'rendered_text_overlaps_reserved_object' in i['details']).update(blockerShapeId=obstacle['shapeId'], blockerRole=obstacle['role'])
        fonts = spec.get('fonts', set())
        if fonts and any(c['char'].isalpha() and font_name(c['font']) not in fonts for c in found):
            issue(key, 'rendered_font_mismatch')
        if any(c['glyph']==0 or c['char']=='\ufffd' for c in found):
            issue(key, 'rendered_missing_glyph')
        if any(not grow(page.rect, .8).contains(c['ink']) for c in found):
            issue(key, 'rendered_text_outside_page', 'overflow', value)
        if any(not grow(area, 2).contains(c['ink']) for c in found):
            issue(key, 'rendered_text_outside_frame', 'overflow', value)
            next(i for i in issues if i['key']==key and 'rendered_text_outside_frame' in i['details']).update(
                renderedTextBox=dict(x=occupied.x0,y=occupied.y0,w=occupied.width,h=occupied.height),
                frameBox=dict(x=area.x0,y=area.y0,w=area.width,h=area.height),
                overflowPt=dict(left=max(0,area.x0-occupied.x0),top=max(0,area.y0-occupied.y0),right=max(0,occupied.x1-area.x1),bottom=max(0,occupied.y1-area.y1)))
        occluded=[c for c in found if any(seq>c['seq'] and cover.contains(c['ink']) for seq,cover in covers)]
        if occluded:
            # A partial collision may be resolved by a fact-preserving wording
            # inside this very field; a completely covered field needs a layout.
            issue(key, 'rendered_text_occluded', 'overflow' if len(occluded)<len(found)*.8 else 'unverifiable', value)
        letters=[c for c in found if c['char'].isalnum()]
        faded=[c for c in letters if indistinguishable(c)]
        if faded:
            # Raster contrast is a heuristic, not proof of missing content.
            # Never shorten copy or stop a deck because of its palette.
            warnings.append(dict(key=key,reason='rendered_text_low_contrast',
                affectedRatio=round(len(faded)/max(1,len(letters)),3),
                **contrast_background(raster,area & page.rect),
                instruction='Проверь изображение: при необходимости выбери читаемый цвет из палитры шаблона. Текст и геометрию сохраняй.'))

    # Compare actual letters in distinct filled fields, not intersecting empty
    # text frames or their em-box whitespace. Table cells remain separate keys.
    def painted_lines(found):
        # The central glyph boxes above intentionally ignore font whitespace,
        # but can miss a heading's bottom strokes touching the next field's
        # capitals. MuPDF also supplies the actual painted bounds of each run.
        # Use only complete, horizontal, single-baseline runs so a paragraph's
        # empty inter-line space or a neighbouring column cannot become ink.
        lines = []
        for seq in {c['seq'] for c in found}:
            run = [c for c in found if c['seq'] == seq]
            if len(run) != run_lengths[seq]: continue
            if any(abs(c['direction'][1]) > .01 for c in run): continue
            if max(c['origin'][1] for c in run)-min(c['origin'][1] for c in run) > .5: continue
            kind, bounds = paint[seq]
            if kind not in ('fill-text', 'stroke-text'): continue
            # get_bboxlog adds a 1 pt safety border; it is not painted ink.
            ink = grow(pymupdf.Rect(bounds), -1)
            if not ink.is_empty: lines.append((seq, ink))
        return lines

    keys = list(matched)
    # Logos, links and master/footer text can be visible without being editable
    # contract fields. Compare their actual glyphs too, never their empty boxes.
    assigned = {id(c) for found in matched.values() for c in found}
    retained = [c for c in chars if id(c) not in assigned and c['char'].isalnum()
                and not any(seq>c['seq'] and cover.contains(c['ink']) for seq,cover in covers)]
    for key, found in matched.items():
        if any(a['seq'] != b['seq'] and (a['ink'] & b['ink']).get_area() > .3*min(a['ink'].get_area(),b['ink'].get_area())
               for a in found if a['char'].isalnum() for b in retained if b['ink'].intersects(a['ink'])):
            issue(key, 'rendered_text_overlaps_source_text', 'overflow', next(s['value'] for s in contracts if s['key']==key))
    lines = {key: painted_lines(found) for key, found in matched.items()}
    for i, key in enumerate(keys):
        left = matched[key]
        lb = pymupdf.Rect(left[0]['ink'])
        for c in left[1:]: lb |= c['ink']
        for other in keys[i+1:]:
            rb = pymupdf.Rect(matched[other][0]['ink'])
            for c in matched[other][1:]: rb |= c['ink']
            shared = lb & rb
            block_collision = not shared.is_empty and shared.width > 1 and shared.height > 1
            right = [c for c in matched[other] if c['ink'].intersects(lb)]
            glyph_collision = any(a['seq']!=b['seq'] and (a['ink'] & b['ink']).get_area() > .3*min(a['ink'].get_area(),b['ink'].get_area())
                                  for a in left for b in right)
            line_collision = any(a_seq != b_seq and (a & b).height > .5
                                 and (a & b).width > min(a.height, b.height)*.5
                                 for a_seq, a in lines[key] for b_seq, b in lines[other])
            if glyph_collision or line_collision or block_collision:
                a_spec=next(s for s in contracts if s['key']==key)
                b_spec=next(s for s in contracts if s['key']==other)
                # A native card frame containing a smaller caption is the
                # overflowing field. Keep its already valid caption unchanged.
                repair_keys = [key] if a_spec['rect'].contains(b_spec['rect']) else [other] if b_spec['rect'].contains(a_spec['rect']) else [key,other]
                for repair_key in repair_keys:
                    issue(repair_key, 'rendered_text_overlap', 'overflow', next(s['value'] for s in contracts if s['key']==repair_key))

    for chart in charts:
        chart_labels=[]
        for kind, value in chart['labels']:
            if not normalized(value): continue
            found = located(chars, value, chart.get('titleRect',chart['rect']) if kind=='title' else chart['rect'])
            if not found:
                # Axes may intentionally skip ticks. Surface this for targeted
                # review instead of declaring all hidden ticks lost data.
                if kind == 'title': issue(chart['key'], 'rendered_chart_title_missing')
                else: warnings.append(dict(key=chart['key'], reason='rendered_chart_label_unverified'))
            elif any(not grow(page.rect, .8).contains(c['ink']) for c in found):
                issue(chart['key'], 'rendered_chart_label_clipped')
            if found:chart_labels.append(found)
        for i,left in enumerate(chart_labels):
            for right in chart_labels[i+1:]:
                if any(a['seq']!=b['seq'] and (a['ink'] & b['ink']).get_area() > .3*min(a['ink'].get_area(),b['ink'].get_area()) for a in left for b in right):
                    issue(chart['key'],'rendered_chart_label_overlap')
    # Transparent reconstructed text frames are editing guides, not clipping
    # masks. Visible text in otherwise empty space is not a failed slide.
    # Run ALL collision/page/font/visibility checks first. Table cells and
    # original styled frames keep their bounds: crossing those changes meaning.
    for candidate in list(issues):
        if candidate['details'] != ['rendered_text_outside_frame']:continue
        spec=next(s for s in contracts if s['key']==candidate['key'])
        if not spec.get('allowFrameOverflow'):continue
        if any(i is not candidate and i['key']==candidate['key'] for i in issues):continue
        issues.remove(candidate)
        warnings.append(dict(key=candidate['key'],reason='rendered_text_outside_frame_visible',
                             overflowPt=candidate.get('overflowPt'),renderedTextBox=candidate.get('renderedTextBox')))
    return dict(issues=issues, warnings=list({(w['key'],w['reason']):w for w in warnings}.values()),
                checkedFields=sum(bool(normalized(s['value'])) for s in contracts), checkedCharts=len(charts))


def inspect_render(folder, slides, pdf_path):
    analysis = json.loads((folder/'analysis.json').read_text())
    layouts = {l['id']:l for l in analysis['layouts']}
    frames = json.loads((folder/'frames.json').read_text())
    parser_path = folder/'effective-parser.json'
    if not parser_path.exists(): parser_path = folder/'parser.json'
    from runtime.model_store import read_model
    parsed = {s['slide_id']:s for s in read_model(parser_path)['slides']}
    from runtime.layout_metadata import layout_metadata
    metadata=layout_metadata(folder)
    parts = read_package(effective_source(folder/'source.pptx',folder,analysis).read_bytes())
    from runtime.page_numbers import pagination_padding,expected_page_fields
    padding=pagination_padding((xml(parts[l['part']]) for l in layouts.values()),analysis['width'],analysis['height'])
    issues, pages = [], []
    from runtime.rebuild import profiles as rebuild_profiles,fields as rebuild_fields
    rebuild_context=rebuild_profiles(folder) if any(s['native'].get('rebuild') for s in slides) else {}
    expansions = {}
    if any(s['native'].get('safeTextExpansion') is True for s in slides):
        from runtime.analysis import validate_fields
        validate_fields(folder, slides, expansions)
    # The existing source render supplies the native title band, including
    # unusual title positions. A matching legend is not proof of a visible title.
    with pymupdf.open(pdf_path) as pdf, pymupdf.open(folder/'previews'/'presentation.pdf') as source_pdf:
        if len(pdf)!=len(slides): raise ValueError('pptx_export_count_mismatch')
        for index, (page, slide) in enumerate(zip(pdf, slides)):
            native = slide['native']
            layout=layouts[native['sourceSlideId']]
            page_fields=expected_page_fields(xml(parts[layout['part']]),analysis['width'],analysis['height'],native,layout['index'],len(slides),padding,metadata[layout['id']].get('pageNumberShapeIds',[]))
            if native.get('rebuild'):
                profile=rebuild_context[native['sourceSlideId']]
                sx,sy=page.rect.width/analysis['width'],page.rect.height/analysis['height']
                contracts=[dict(key=s['key'],value=native['fields'][s['key']],rect=rect(s,sx,sy),allowFrameOverflow=(next((a.get('allowFrameOverflow',False) for a in profile.get('textAnchors',[]) if a['key']==s['key']),False) if profile.get('version',1)>=2 else any(t['key']==s['key'] for t in native['rebuild']['texts'])),fonts=expected_fonts(s['font'],s['bold'],False)) for s in ({**field,**slide.get('textStyles',{}).get('native.fields.'+field['key'],{})} for field in rebuild_fields(native['rebuild']))]
                contracts += [dict(key=t['key'],value=t['value'],rect=rect(t,sx,sy),allowFrameOverflow=True,fonts=expected_fonts(t['font'],t['bold'],False)) for t in profile.get('retainedTexts',[]) if t['value'].strip()]
                anchor_ids={a['key']:a['shapeId'] for a in profile.get('textAnchors',[])+profile.get('retainedTexts',[]) if 'shapeId' in a}
                for contract in contracts:
                    if anchor_ids.get(contract['key']) in page_fields:contract['value']=page_fields[anchor_ids[contract['key']]]
                chart_contracts=[]
                for spec in layouts[native['sourceSlideId']].get('charts',[]):
                    placement=next(c for c in native['rebuild']['charts'] if c['shapeId']==spec['shapeId'])
                    value=native['charts'][spec['key']]
                    chart_contracts.append(dict(key=spec['key'],rect=rect(placement,sx,sy),labels=([('title',value['title'])] if spec['hasTitle'] else [])+[('category',v) for v in value['categories']]))
                result=inspect_page(page,contracts,chart_contracts,rebuilt_reserved(profile,native['rebuild'],sx,sy))
                issues.extend(dict(slide=index,**i) for i in result.pop('issues'));pages.append(dict(slide=index,recomposed=True,**result));continue
            if native.get('mode')!='source':
                pages.append(dict(slide=index,status='legacy_not_checked'));continue
            layout = layouts[native['sourceSlideId']]
            layout, slide_frames = expanded_table_layout(layout, frames[layout['id']], native.get('tableRows'), native.get('tableRowWeights'),native.get('tableColumns'))
            sx,sy = page.rect.width/analysis['width'],page.rect.height/analysis['height']
            source_page = source_pdf[layout['index']] if layout['index'] < len(source_pdf) else None
            source_chars = page_characters(source_page) if source_page is not None else []
            contracts = []
            for slot in layout['slots']:
                fonts = set()
                for p in slide_frames[slot['key']]['paragraphs']:
                    fonts.update(expected_fonts(p.get('font_family') or slot['font'], bool(p.get('bold')), bool(p.get('italic'))))
                if source_page is not None:
                    original_fonts = source_cell_fonts(source_chars, slot, rect(slot,source_page.rect.width/analysis['width'],source_page.rect.height/analysis['height']))
                    if original_fonts:
                        fonts = original_fonts
                fitted_slot = {**expansions.get(index, {}).get(slot['key'], slot),**slide.get('textStyles',{}).get('native.fields.'+slot['key'],{})}
                manual=slide.get('textStyles',{}).get('native.fields.'+slot['key'],{})
                if 'bold' in manual:
                    fonts=set().union(*(expected_fonts(p.get('font_family') or slot['font'],manual['bold'],bool(p.get('italic'))) for p in slide_frames[slot['key']]['paragraphs']))
                markers = {normalized(p.get('bullet_text', '')) for p in slide_frames[slot['key']]['paragraphs'] if not p.get('bullet_auto')}
                markers.discard('')
                numbered = any(p.get('bullet_auto') for p in slide_frames[slot['key']]['paragraphs'])
                contracts.append(dict(key=slot['key'], value=page_fields.get(slot['shapeId'],native['fields'][slot['key']]) if 'cell' not in slot else native['fields'][slot['key']], rect=rect(fitted_slot,sx,sy), fonts=fonts, paragraphMarkers=markers, numbered=numbered))
            by_id = {o['shape_id']:o for o in objects(parsed[layout['id']]['objects'])}
            charts = []
            for spec in layout.get('charts', []):
                value = native['charts'][spec['key']]
                source = xml(parts[spec['part']])
                ns={'c':'http://schemas.openxmlformats.org/drawingml/2006/chart'}
                labels = [('title',value.get('title',''))] if spec['hasTitle'] else []
                axes = source.xpath('.//c:catAx[not(c:delete[@val="1"]) and not(c:tickLblPos[@val="none"])]',namespaces=ns)
                if axes: labels.extend(('category',v) for v in value['categories'])
                if source.find('.//c:legend',ns) is not None:
                    labels.extend(('legend',v) for v in (value['categories'] if 'pieChart' in spec['type'] or 'doughnutChart' in spec['type'] else [s['name'] for s in value['series']]))
                chart_rect=rect(box(by_id[spec['shapeId']]),sx,sy)
                chart=dict(key=spec['key'],rect=chart_rect,labels=labels)
                original_title=''.join(source.xpath('.//c:title//a:t/text()|.//c:title//c:strCache/c:pt/c:v/text()',namespaces={**ns,'a':NS['a']}))
                if original_title and layout['index']<len(source_pdf):
                    original=located(source_chars,original_title,chart_rect)
                    if original:
                        band=pymupdf.Rect(original[0]['box'])
                        for c in original[1:]:band|=c['box']
                        chart['titleRect']=pymupdf.Rect(chart_rect.x0,band.y0-3,chart_rect.x1,band.y1+max(3,band.height*.5))
                charts.append(chart)
            reserved=[dict(shapeId=o['shapeId'],role=o['role'],rect=rect(o['box'],sx,sy)) for o in metadata[layout['id']].get('reservedRegions',[])]
            result = inspect_page(page,contracts,charts,reserved)
            issues.extend(dict(slide=index,**i) for i in result.pop('issues'))
            pages.append(dict(slide=index,**result))
    return dict(version=VERSION, pdfSha256=hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
                pptxSha256=hashlib.sha256(pdf_path.with_suffix('.pptx').read_bytes()).hexdigest(), issues=issues, pages=pages)
