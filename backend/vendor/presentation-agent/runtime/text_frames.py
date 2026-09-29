"""Service capabilities layered over the unchanged upstream text fitter.

AutoFit is not a reason to discard an otherwise ordinary source slide. We
require the payload to fit at the *unscaled* source font size and spacing, so
neither shrinking the text nor growing its shape is necessary. Keep the source
AutoFit XML, dimensions and style untouched in the exported copy.

DrawingML normAutofit can only shrink text/line spacing within the ranges below:
https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.drawing.normalautofit
Unknown effects, missing fonts and unsupported geometry remain blocking.
"""
import math
from collections import OrderedDict
from PIL import ImageFont
from lxml import etree
from app.text.native import source_text_frame as upstream_frame, NativeTextFrame, NativeParagraph, NativeTextFitter, EMU_PER_POINT, _merge, _xml, _font_coverage
from runtime.fonts import TemplateFontResolver


class TemplateParagraph(NativeParagraph):
    bullet_text: str = ''
    bullet_auto: bool = False
    bullet_font: str | None = None
    bullet_scale: float = 1
    bullet_scheme: str = 'arabicPeriod'
    bullet_start: int = 1


NUMBER_SCHEMES = {'arabicPeriod': ('', '.'), 'arabicParenR': ('', ')'),
                  'arabicParenBoth': ('(', ')'), 'arabicPlain': ('', '')}


def number_label(scheme, number):
    prefix, suffix = NUMBER_SCHEMES[scheme]
    return f'{prefix}{number}{suffix}'


def apply_bullet_properties(paragraph, properties):
    """Same native list rules for shapes and table cells. Return false when
    the marker cannot be measured; never silently discard unknown numbering."""
    nodes = {etree.QName(n).localname:n for n in properties}
    if 'buNone' in nodes:
        return True
    bullet, auto = nodes.get('buChar'), nodes.get('buAutoNum')
    if 'buBlip' in nodes or (auto is not None and auto.get('type') not in NUMBER_SCHEMES):
        return False
    if bullet is not None or auto is not None:
        paragraph.bullet_text = bullet.get('char', '') if bullet is not None else '1.'
        paragraph.bullet_auto = auto is not None
        if auto is not None:
            paragraph.bullet_scheme = auto.get('type')
            paragraph.bullet_start = int(auto.get('startAt', '1'))
            paragraph.bullet_text = number_label(paragraph.bullet_scheme, paragraph.bullet_start)
        font = nodes.get('buFont')
        paragraph.bullet_font = font.get('typeface') if font is not None else None
        scale, points = nodes.get('buSzPct'), nodes.get('buSzPts')
        paragraph.bullet_scale = percentage(scale.get('val'), 1) if scale is not None else (float(points.get('val'))/100/paragraph.font_size_pt if points is not None and paragraph.font_size_pt else 1)
    return True


class TemplateTextFrame(NativeTextFrame):
    autofit_ink_bounds: bool = False
    visible_width_emu: int | None = None
    paragraphs: list[TemplateParagraph] = []


class TemplateTextFitter(NativeTextFitter):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.measurement.resolver = TemplateFontResolver()
        self.paragraph_count = 1
        # The sandbox's fonts are immutable. Alternative candidates usually
        # change one field and repeat every other frame/text pair in the batch.
        self._fits = OrderedDict()

    def fit(self, frame, paragraphs):
        self.paragraph_count = max(1, len(paragraphs))
        key=(frame.model_dump_json(),tuple(paragraphs))
        if key in self._fits:
            self._fits.move_to_end(key)
            return self._fits[key].model_copy(deep=True)
        # The upstream safety margin wraps earlier than LibreOffice. If a
        # source frame extends off-page, those invented wraps can hide clipped
        # ink. Require each explicit paragraph to fit the visible width; keep
        # the original frame and its wrapping untouched in the exported PPTX.
        measured_frame = frame.model_copy(update={'wrap': False}) if frame.visible_width_emu is not None else frame
        report = super().fit(measured_frame, paragraphs)
        if len(paragraphs) > 1 and len(report.paragraphs) == len(paragraphs):
            # Baseline advances between paragraphs must remain intact. Only
            # the two OUTER font paddings lie outside the visible text block;
            # counting them rejects even the template's own three-line sample.
            # Do not subtract padding per paragraph (unsafe for mixed styles).
            trim = 0
            for edge in (0, -1):
                record = report.paragraphs[edge]
                source = frame.paragraphs[record.source_paragraph_index]
                text = paragraphs[edge]
                if not text.strip() or getattr(source, 'bullet_text', ''):
                    continue  # Blank boundary lines and list glyphs retain their full box.
                dpi = self.measurement.dpi
                font = ImageFont.truetype(record.font_path, max(1, round(record.font_size_pt * dpi / 72)))
                _, top, _, bottom = font.getbbox(text.replace('\n', ' '), anchor='ls')
                ascent, descent = font.getmetrics()
                trim += max(0, ascent + top if edge == 0 else descent - bottom) * 72 / dpi * EMU_PER_POINT
            report.measured_height_emu = max(0, math.ceil(report.measured_height_emu - trim))
        # Insets already reserve the source's physical top/bottom padding.
        # A second percentage inset falsely rejects 15pt ink inside a 15.3pt
        # content box. Keep the conservative horizontal wrap margin, but test
        # vertical ink against the real usable height, without growing the box.
        report.available_height_emu = max(0, frame.height_emu - frame.top_emu - frame.bottom_emu)
        if (report.reason_codes == ['source_text_frame_overflow']
                and report.measured_width_emu <= report.available_width_emu
                and report.measured_height_emu <= report.available_height_emu):
            report.status = 'fits'
            report.reason_codes = []
        if report.reason_codes == ['native_bullet_gutter_overflow']:
            report.status = 'overflow'
        # Measure wrapping against the ORIGINAL frame. Clipping the width
        # before layout would invent line breaks the exported shape never uses.
        if frame.visible_width_emu is not None and report.status != 'unverifiable':
            report.available_width_emu=min(report.available_width_emu,frame.visible_width_emu)
            if report.measured_width_emu>report.available_width_emu:
                report.status='overflow'
                if 'source_text_outside_page' not in report.reason_codes:
                    report.reason_codes.append('source_text_outside_page')
        if len(self._fits)>=512:self._fits.popitem(last=False)
        self._fits[key]=report.model_copy(deep=True)
        return report
    def probe(self, frame, text='Тест'):
        # Every source paragraph can be selected when writing/editing, not only
        # the first one. Catch unsupported list/style levels before payment.
        return [self.fit(frame.model_copy(update={'paragraphs':[p]}),[text]) for p in frame.paragraphs]

    def _paragraph(self, paragraph, text, index, frame, width):
        # Keep upstream wrapping, exact face/glyph checks, spacing and width.
        record = super()._paragraph(paragraph, text, index, frame, width)
        if getattr(paragraph, 'bullet_text', '') and text.strip():
            label = number_label(paragraph.bullet_scheme, paragraph.bullet_start + self.paragraph_count - 1) if paragraph.bullet_auto else paragraph.bullet_text
            family = paragraph.bullet_font or paragraph.font_family
            face = self.measurement.resolver.resolve(family, bold=paragraph.bold, italic=paragraph.italic)
            if face.path is None or any(ord(c) not in _font_coverage(str(face.path)) for c in label):
                raise ValueError('native_bullet_font_unavailable')
            metric = self.measurement.measure(label, family=family, font_size_pt=paragraph.font_size_pt*paragraph.bullet_scale,
                available_width_emu=width, bold=paragraph.bold, italic=paragraph.italic, wrap=False)[0]
            # Hanging indent is the reserved label gutter, not free body width.
            gutter = -paragraph.indent_emu
            if gutter <= 0 or metric.width_emu + EMU_PER_POINT > gutter:
                raise ValueError('native_bullet_gutter_overflow')
        dpi = self.measurement.dpi
        font = ImageFont.truetype(record.font_path, max(1, round(record.font_size_pt * dpi / 72)))
        # Ascent+descent is a baseline pitch, not visible glyph height. Counting
        # all the font's unused ascender/descender space falsely rejects tight
        # source titles/subtitles even when the glyphs are inside the box.
        # This applies to fixed-size frames too, not only AutoFit frames.
        # getbbox includes actual accents/descenders. Use the union of all glyphs
        # (conservative for wrapped lines), retaining the full baseline pitch
        # between lines and every explicit paragraph gap.
        _, top, _, bottom = font.getbbox(text.replace('\n', ' '), anchor='ls')
        ascent, descent = font.getmetrics()
        base = (ascent + descent) * 72 / dpi * EMU_PER_POINT
        ink = max(0, bottom - top) * 72 / dpi * EMU_PER_POINT
        # An explicitly cleared optional slot has no visible text. Blank lines
        # within a nonempty value still retain their baseline pitch below.
        if not text.strip() and self.paragraph_count == 1:
            return record.model_copy(update={'height_emu': 0, 'width_emu': 0})
        if self.paragraph_count > 1:
            # Retain full paragraph baseline advances, including blank lines.
            # Subtracting ascender/descent padding for EACH paragraph would
            # accumulate an unsafe underestimate in lists/mixed-size blocks.
            return record
        return record.model_copy(update={'height_emu': max(0, math.ceil(record.height_emu - base + ink))})


FIT_VERSION = 7


def overflow_guidance(report, value):
    """Report the physical constraint instead of endlessly shrinking strings."""
    width = report.measured_width_emu > report.available_width_emu
    height = report.measured_height_emu > report.available_height_emu
    wrapped = any(p.line_count > 1 for p in report.paragraphs)
    result = dict(fitVersion=FIT_VERSION,
                  overflowAxes=[axis for axis, failed in (('width', width), ('height', height)) if failed],
                  paragraphCount=len(value.split('\n')),
                  measuredHeightPt=round(report.measured_height_emu / EMU_PER_POINT, 2),
                  availableHeightPt=round(report.available_height_emu / EMU_PER_POINT, 2))
    if height and not wrapped:
        result['message'] = 'Не хватает высоты для отдельных абзацев. Объедини строки в связный текст или уменьши число пунктов; сокращение слов при прежнем числе строк не устранит эту ошибку.'
    if width or wrapped:
        ratio = min(1, report.available_width_emu / max(1, report.measured_width_emu),
                    report.available_height_emu / max(1, report.measured_height_emu))
        result['suggestedMaxChars'] = max(1, math.floor(len(value) * min(.75, ratio * .85)))
    return result


def visible_frame_width(frame, slot, page_width):
    """Visible ink budget; wrapping still uses the original native frame.

    A partially off-page frame is not itself a reason to reject a template.
    Its alignment anchor determines how much replacement text stays visible.
    """
    if slot['x'] >= 0 and slot['x'] + slot['w'] <= page_width:
        return None
    # Geometry is in page points; the fitter measures in local EMU. Preserve
    # the source group transform when converting back to its measurement space.
    scale = slot['w'] / (frame['width_emu'] / EMU_PER_POINT)
    if scale <= 0:
        return None
    left = slot['x'] + frame.get('left_emu', 0) / EMU_PER_POINT * scale
    right = slot['x'] + (frame['width_emu'] - frame.get('right_emu', 0)) / EMU_PER_POINT * scale
    align = slot.get('align', 'left')
    if align in ('right', 'r'):
        available = right if right <= page_width else 0
    elif align in ('center', 'ctr'):
        middle = (left + right) / 2
        available = 2 * min(middle, page_width - middle)
    else:
        available = page_width - left if left >= 0 else 0
    return max(0, math.floor(min(right - left, available) / scale * EMU_PER_POINT))


def percentage(value, default):
    if value is None:
        return default
    result = float(value[:-1]) / 100 if value.endswith('%') else int(value) / 100000
    if not math.isfinite(result):
        raise ValueError('invalid_percentage')
    return result


def source_text_frame(shape, geometry=None):
    # Match our writer, which replaces a paragraph with its first nonempty run.
    # Old manual line breaks/other runs are removed, not retained by the writer.
    normalized = shape.model_copy(deep=True) if hasattr(shape, 'model_copy') else shape
    if getattr(normalized, 'rich_text', None):
        for paragraph in normalized.rich_text.paragraphs:
            runs = [r for r in paragraph.runs if not r.is_line_break]
            exemplar = next((r for r in runs if r.text.strip()), runs[0] if runs else None)
            paragraph.runs = [exemplar] if exemplar else []
            if exemplar:
                exemplar.is_field = False
                # The filled end-of-paragraph marker inherits the new run.
                # The parser's effective run size may be a theme default while
                # lvl1pPr on the actual layout defines a different size. Our
                # writer replaces the end marker's metrics with the selected
                # run; only its explicit values may override that inheritance.
                paragraph.end_style = exemplar.raw_style
    frame = TemplateTextFrame.model_validate(upstream_frame(normalized).model_dump())
    if geometry and 'nonrectangular_text_frame_unsupported' in frame.reason_codes:
        insets = geometry.insets(shape, frame.width_emu, frame.height_emu)
        if insets is not None:
            # DrawingML body insets are relative to the preset's text rectangle.
            for name, value in zip(('left_emu', 'top_emu', 'right_emu', 'bottom_emu'), insets):
                setattr(frame, name, getattr(frame, name) + value)
            frame.reason_codes.remove('nonrectangular_text_frame_unsupported')
    full = getattr(shape, 'geometry_full', None)
    if full and full.local_bbox and full.local_bbox.width_emu and full.local_bbox.height_emu:
        # Transforming a group/shape transforms text and frame together. Measure
        # in local coordinates; export keeps the original transform unchanged.
        frame.reason_codes = [r for r in frame.reason_codes if r != 'transformed_text_frame_unsupported']
    if getattr(shape, 'rich_text', None) and shape.text_frame:
        for original, paragraph in zip(shape.rich_text.paragraphs, frame.paragraphs):
            properties = etree.Element('properties')
            for layer in shape.text_frame.style_layers:
                for key in ('defPPr', f'lvl{original.level+1}pPr'):
                    if value := layer.paragraph_styles.get(key): _merge(properties, _xml(value))
            if original.properties.raw_xml: _merge(properties, _xml(original.properties.raw_xml))
            # Tabs in old text do not affect replacement text without tabs.
            if apply_bullet_properties(paragraph, properties):
                paragraph.reason_codes = [r for r in paragraph.reason_codes if r != 'paragraph_bullets_or_tabs_unsupported']
            if paragraph.bullet_font:
                paragraph.bullet_font = shape.text_frame.font_aliases.get(paragraph.bullet_font, paragraph.bullet_font)
            # Ordinary word justification uses the same wrapping/line height;
            # source XML and its alignment are retained. Distributed/RTL modes
            # still require a different measurement engine and remain blocked.
            if properties.get('algn') in ('just', 'justLow') and properties.get('rtl','0') not in ('1','true'):
                paragraph.reason_codes = [r for r in paragraph.reason_codes if r != 'paragraph_alignment_unsupported']
    if 'dynamic_autofit_unsupported' not in frame.reason_codes:
        return frame
    mode = None
    try:
        for layer in shape.text_frame.style_layers:
            for raw in layer.body_children:
                node = etree.fromstring(raw.encode(), parser=etree.XMLParser(resolve_entities=False, no_network=True))
                if etree.QName(node).localname in {'noAutofit', 'normAutofit', 'spAutoFit'}:
                    mode = node
        name = etree.QName(mode).localname if mode is not None else None
        if name == 'normAutofit':
            if set(mode.attrib) - {'fontScale', 'lnSpcReduction'} or len(mode):
                return frame
            if not 0 < percentage(mode.get('fontScale'), 1) <= 1:
                return frame
            if not 0 <= percentage(mode.get('lnSpcReduction'), 0) <= 1:
                return frame
        elif name == 'spAutoFit':
            if mode.attrib or len(mode):
                return frame
        else:
            return frame
    except (ValueError, TypeError, etree.XMLSyntaxError):
        return frame
    frame.reason_codes.remove('dynamic_autofit_unsupported')
    frame.autofit_ink_bounds = True
    return frame
