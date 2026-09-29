"""Resolve text colours through the same paragraph inheritance as text fitting."""
from lxml import etree
from runtime.security import NS, xml
from runtime.background import related
from app.presentation.parser.colors import resolve_color, parse_color_map
from app.text.native import _merge, _xml


def inherited_text_color(shape, parts):
    if not shape.rich_text or not shape.text_frame:
        return None
    part = shape.source_part
    layout = related(parts, part, 'slideLayout')
    master = related(parts, layout, 'slideMaster') if layout else None
    theme = related(parts, master, 'theme') if master else None
    colors, mapping = {}, {}
    if theme in parts:
        scheme = xml(parts[theme]).find('a:themeElements/a:clrScheme', NS)
        for entry in scheme if scheme is not None else []:
            value = resolve_color(entry, {}).value
            if value: colors[etree.QName(entry).localname] = value
    for level in (master, layout, part):
        if level in parts:
            root = xml(parts[level])
            mapping.update(parse_color_map(root))
            mapping.update(parse_color_map(root, override=True))
    for paragraph in shape.rich_text.paragraphs:
        properties = etree.Element('properties')
        for layer in shape.text_frame.style_layers:
            for key in ('defPPr', f'lvl{paragraph.level + 1}pPr'):
                if value := layer.paragraph_styles.get(key):
                    _merge(properties, _xml(value))
        if paragraph.properties.raw_xml:
            _merge(properties, _xml(paragraph.properties.raw_xml))
        defaults = properties.find('a:defRPr', NS)
        value = resolve_color(defaults, colors, mapping).value if defaults is not None else None
        for run in paragraph.runs:
            if not run.text.strip(): continue
            explicit = resolve_color(_xml(run.raw_properties_xml), colors, mapping).value if run.raw_properties_xml else None
            return explicit or value or run.effective_style.color
        if value: return value
    return None


def inherited_vertical_alignment(shape):
    frame=shape.get('text_frame') if isinstance(shape,dict) else shape.text_frame
    layers=(frame.get('style_layers',[]) if isinstance(frame,dict) else frame.style_layers) if frame else []
    anchor='t'
    for layer in layers:
        attrs=layer.get('body_attributes',{}) if isinstance(layer,dict) else layer.body_attributes
        anchor=attrs.get('anchor',anchor)
    return {'ctr':'center','b':'bottom'}.get(anchor,'top')
