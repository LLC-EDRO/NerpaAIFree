"""Resolve the local OOXML background, including theme bgRef style references."""
import posixpath
from copy import deepcopy
from app.presentation.parser.archive import relationship_part
from app.presentation.parser.colors import resolve_color, parse_color_map
from runtime.security import NS, xml


def related(parts, part, kind):
    raw=parts.get(relationship_part(part))
    if not raw:return None
    for r in xml(raw):
        if r.get('Type','').endswith('/'+kind) and r.get('TargetMode')!='External':
            return posixpath.normpath(posixpath.join(posixpath.dirname(part),r.get('Target','')))


def source_background(parts, part):
    layout=related(parts,part,'slideLayout')
    master=related(parts,layout,'slideMaster') if layout else None
    theme=related(parts,master,'theme') if master else None
    levels=[xml(parts[p]) for p in [part,layout,master] if p in parts]
    bg=next((r.find('p:cSld/p:bg',NS) for r in levels if r.find('p:cSld/p:bg',NS) is not None),None)
    if bg is None:return '#FFFFFF'
    theme_root=xml(parts[theme]) if theme in parts else None
    colors={}
    if theme_root is not None:
        scheme=theme_root.find('a:themeElements/a:clrScheme',NS)
        for color in scheme if scheme is not None else []:
            resolved=resolve_color(color,{})
            if resolved.value:colors[etree_name(color)]=resolved.value
    mapping={}
    for r in reversed(levels):mapping.update(parse_color_map(r));mapping.update(parse_color_map(r,override=True))
    props=bg.find('p:bgPr',NS)
    if props is not None:
        solid=props.find('a:solidFill',NS)
        return resolve_color(solid,colors,mapping).value if solid is not None else None
    ref=bg.find('p:bgRef',NS)
    if ref is None or theme_root is None:return None
    index=int(ref.get('idx','0'))
    placeholder=resolve_color(ref,colors,mapping).value
    collection='bgFillStyleLst' if index>=1001 else 'fillStyleLst'
    offset=index-1001 if index>=1001 else index-1
    styles=theme_root.find('a:themeElements/a:fmtScheme/a:'+collection,NS)
    if styles is None or not 0<=offset<len(styles):return None
    fill=deepcopy(styles[offset])
    if etree_name(fill)!='solidFill':return None  # gradients/photos have no single safe colour
    for color in fill.findall('.//a:schemeClr',NS):
        if color.get('val')=='phClr' and placeholder:
            color.tag='{%s}srgbClr'%NS['a'];color.set('val',placeholder.lstrip('#'))
    return resolve_color(fill,colors,mapping).value


def etree_name(node):
    return node.tag.rsplit('}',1)[-1]
