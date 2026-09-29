"""Deterministic font policy shared by native measurement and the rendered copy.

Never fetch fonts from an upload or mutate the original. A missing face is
replaced in a private working PPTX, and the substitution is exposed in the UI.
"""
import hashlib
import zipfile
import os
import json
import re
from pathlib import Path
from functools import lru_cache
from PIL import ImageFont
from fontTools.ttLib import TTFont
from app.text.native import NativeFontResolver, _font_coverage
from app.text.measurement import FontResolution
from runtime.security import xml


class TemplateFontResolver(NativeFontResolver):
    LIBRARY = Path(os.environ.get('PRESENTATION_FONT_DIR',Path(__file__).resolve().parents[3]/'data/presentation-fonts'))
    FONT_ROOTS = (*NativeFontResolver.FONT_ROOTS, Path.home()/'.local/share/fonts', Path.home()/'.fonts',LIBRARY/'open',LIBRARY/'licensed')
    @classmethod
    @lru_cache(maxsize=1)
    def _build_index(cls):
        # Font filenames are not family names (ArialHB, custom asset hashes, etc.).
        found = {}
        cached = installed_font_index()
        if cached is not None:
            return {k: tuple(Path(p) for p in v) for k, v in cached['index'].items()}
        for root in cls.FONT_ROOTS:
            if not root.is_dir():
                continue
            for path in sorted(root.rglob('*')):
                if path.suffix.lower() not in {'.ttf', '.otf', '.ttc'}:
                    continue
                try:
                    record=font_metadata(path)
                    for key in record['names']:
                        found.setdefault(key, []).append(path)
                except Exception:
                    continue
        return {k: tuple(v) for k, v in found.items()}

    @lru_cache(maxsize=512)
    def resolve(self, family, *, bold=False, italic=False):
        target = self._normalize(family or '')
        # Start only from an installed metadata alias, never a fuzzy family
        # prefix (e.g. Sans Narrow must not become Sans).
        anchors = self._font_index.get(target, ())
        anchors = sorted(anchors, key=lambda p: font_metadata(p)['italic'])
        base=[p for p in anchors if target in font_metadata(p)['base_names']]
        if base:
            width=5 if any(font_metadata(p)['width']==5 for p in base) else font_metadata(base[0])['width']
            anchors=[p for p in base if font_metadata(p)['width']==width]
        for path in anchors:
            record=font_metadata(path)
            explicit=target not in record['base_names']
            weight=record['weight'] if explicit else 400
            weight=max(weight,700) if bold else weight
            slanted=italic or explicit and record['italic']
            # PowerPoint can store a face name and a separate B/I switch.
            # Resolve those switches within the SAME typographic family/width.
            for candidate in self._font_index.get(self._normalize(record['family']), ()):
                face=font_metadata(candidate)
                if (self._normalize(face['family'])==self._normalize(record['family'])
                    and face['width']==record['width'] and face['weight']==weight
                    and face['italic']==slanted):
                    return FontResolution(family, face['family'], candidate, 'exact')
        return FontResolution(family, family, None, 'unavailable', 'Source font face unavailable')


@lru_cache(maxsize=1)
def installed_font_index():
    path = Path('/font-cache/font-index.json')
    if not path.is_file():
        return None
    records = json.loads(path.read_text())
    for record in records['records'].values():
        record['names'] = set(record['names'])
        record['base_names'] = set(record['base_names'])
    return records


@lru_cache(maxsize=4096)
def font_metadata(path):
    cached = installed_font_index()
    if cached is not None and str(path) in cached['records']:
        return cached['records'][str(path)]
    with TTFont(path,fontNumber=0,lazy=True) as font:
        names=font['name']; normalize=NativeFontResolver._normalize
        values=lambda ids:{n.toUnicode() for n in names.names if n.nameID in ids}
        family=names.getBestFamilyName()
        style=names.getBestSubFamilyName()
        base_names={normalize(family)}
        aliases=values({1,4,6,16})|{family+' '+style}
        # Legacy family names often contain the explicit weight. Only the
        # typographic family is the base family for Regular/Bold selection.
        aliases.add(family)
        # Some genuine static faces have incomplete OS/2/head style flags.
        # Read the other OpenType signals as Fontconfig/FreeType do. Do not
        # modify the user's font or synthesize an italic/bold face.
        italic=bool(font['OS/2'].fsSelection & (1|512) or font['head'].macStyle & 2
                    or getattr(font.get('post'),'italicAngle',0)
                    or re.search(r'\b(?:italic|oblique)\b',style,re.I))
        return dict(family=family,names={normalize(n) for n in aliases},aliases=sorted(aliases),base_names=base_names,
                    weight=font['OS/2'].usWeightClass,width=font['OS/2'].usWidthClass,italic=italic)


def font_face_aliases(families):
    """Verified full-face aliases for the renderer; no invented substitutes."""
    resolver=TemplateFontResolver()
    for name in sorted(set(families)):
        face=resolver.resolve(name)
        if not face.path:
            continue
        record=font_metadata(face.path)
        if resolver._normalize(name) not in record['base_names']:
            yield name,record


def requested_font_names(source):
    from runtime.security import read_package
    parts=read_package(source.read_bytes())
    names=set()
    for name,data in parts.items():
        if name.startswith('ppt/') and name.endswith('.xml'):
            for node in xml(data).iter():
                family=node.get('typeface')
                if family and not family.startswith('+') and len(family)<=150:names.add(family)
    return sorted(names)[:80]


def font_report(requirements):
    resolver=TemplateFontResolver()
    return [dict(family=family,faces=[dict(bold=b,italic=i,status=resolver.resolve(family,bold=b,italic=i).status)
            for b,i in sorted(faces)]) for family,faces in sorted(requirements.items()) if family and not family.startswith('+')]


def font_environment_fingerprint(families):
    resolver=TemplateFontResolver();entries=[];digests={}
    # Fallback candidates affect a missing family's effective rendering too.
    names=sorted(set(families)|{'Inter','Arial','Georgia','Carlito','DejaVu Sans','DejaVu Serif','Courier New','DejaVu Sans Mono'})
    for family in names:
        for bold,italic in ((False,False),(True,False),(False,True),(True,True)):
            face=resolver.resolve(family,bold=bold,italic=italic)
            if face.path and face.path not in digests:digests[face.path]=hashlib.sha256(face.path.read_bytes()).hexdigest()
            entries.append([family,bold,italic,digests.get(face.path)])
    return hashlib.sha256(json.dumps(['font-policy-v3',entries],ensure_ascii=False).encode()).hexdigest()


def choose_fonts(requirements):
    resolver = TemplateFontResolver()
    changes = []
    for family, faces in sorted(requirements.items()):
        if not family or family.startswith('+'):
            continue
        def available(candidate):
            for bold, italic in faces:
                face = resolver.resolve(candidate, bold=bold, italic=italic)
                if face.path is None or not all(ord(c) in _font_coverage(str(face.path)) for c in 'ТестTest'):
                    return False
            return True
        if available(family):
            continue
        name = family.casefold()
        if any(s in name for s in ('courier','consolas','mono')):
            candidates = ('Courier New','DejaVu Sans Mono','Inter','Arial')
        elif any(s in name for s in ('times','georgia','cambria','garamond','serif')) and 'sans' not in name:
            candidates = ('Georgia','Times New Roman','DejaVu Serif','Inter','Arial')
        elif 'calibri' in name or 'carlito' in name:
            candidates = ('Carlito','Arial','Inter','DejaVu Sans')
        else:
            candidates = ('Inter','Arial','DejaVu Sans','Georgia')
        replacement = next((f for f in candidates if available(f)), None)
        if replacement:
            changes.append(dict(original=family,replacement=replacement))
    return changes


def choose_bullet_fonts(paragraphs):
    """A missing symbol is not a missing body font. Replace only the marker face.
    Require the exact marker glyph and all actually used B/I variants."""
    resolver = TemplateFontResolver()
    requirements = {}
    for p in paragraphs:
        if p.bullet_text and not p.bullet_auto:
            requirements.setdefault((p.bullet_font or p.font_family, p.bullet_text), set()).add((p.bold, p.italic))
    changes = []
    for (family, glyph), faces in requirements.items():
        def available(candidate):
            for bold, italic in faces:
                face = resolver.resolve(candidate, bold=bold, italic=italic)
                if not face.path or any(ord(c) not in _font_coverage(str(face.path)) for c in glyph):
                    return False
            return True
        if available(family):
            continue
        replacement = next((f for f in ('DejaVu Sans', 'Arial', 'DejaVu Serif') if available(f)), None)
        if replacement:
            changes.append(dict(original=family, replacement=replacement, scope='bullet', character=glyph))
    return changes


def recover_marker_fonts(parts, model):
    """Materialize an inherited marker font only at the affected paragraph.
    Source IDs locate XML; no font family is guessed from neighbouring text."""
    from lxml import etree
    from runtime.security import NS
    from runtime.text_frames import source_text_frame
    from runtime.tables import cell_frame
    def flatten(items):
        for item in items:
            yield item
            yield from flatten(item.children)
    tables = {t.linked_object_id: t for t in model.tables if t.source_level == 'slide'}
    targets = []
    for slide in model.slides:
        for shape in flatten(slide.objects):
            if shape.hidden or shape.shape_id is None:
                continue
            if shape.rich_text and shape.text_frame and shape.object_kind == 'shape':
                targets.extend((shape, None, i, p) for i, p in enumerate(source_text_frame(shape).paragraphs))
            if shape.object_kind == 'table' and shape.source_object_id in tables:
                table = tables[shape.source_object_id]
                for r, row in enumerate(table.rows):
                    for c, cell in enumerate(row.cells):
                        if not cell.h_merge and not cell.v_merge:
                            targets.extend((shape, (r,c), i, p) for i,p in enumerate(cell_frame(table, cell, shape).paragraphs))
    substitutions = choose_bullet_fonts([p for _,_,_,p in targets])
    roots, changes = {}, []
    for shape, cell, index, paragraph in targets:
        family = paragraph.bullet_font or paragraph.font_family
        match = next((c for c in substitutions if c['original'] == family and c['character'] == paragraph.bullet_text), None)
        if not match:
            continue
        root = roots.setdefault(shape.source_part, xml(parts[shape.source_part]))
        tag = 'graphicFrame' if cell is not None else 'sp'
        shapes = root.xpath(f'.//p:{tag}[*/p:cNvPr[@id=$id]]', namespaces=NS, id=str(shape.shape_id))
        if len(shapes) != 1:
            continue
        if cell is None:
            paragraphs = shapes[0].findall('p:txBody/a:p', NS)
        else:
            rows = shapes[0].xpath('.//a:tbl/a:tr', namespaces=NS)
            if cell[0] >= len(rows): continue
            cells = rows[cell[0]].findall('a:tc', NS)
            if cell[1] >= len(cells): continue
            paragraphs = cells[cell[1]].findall('a:txBody/a:p', NS)
        if index >= len(paragraphs):
            continue
        p = paragraphs[index]
        prop = p.find('a:pPr', NS)
        if prop is None:
            prop = etree.Element('{%s}pPr' % NS['a'])
            p.insert(0, prop)
        for node in list(prop):
            if etree.QName(node).localname in ('buFont', 'buFontTx'):
                prop.remove(node)
        font = etree.Element('{%s}buFont' % NS['a'], typeface=match['replacement'])
        # DrawingML sequence: spacing, colour, size, font, marker, tabs, run props.
        after = {'buNone','buAutoNum','buChar','buBlip','tabLst','defRPr','extLst'}
        offset = next((i for i,n in enumerate(prop) if etree.QName(n).localname in after), len(prop))
        prop.insert(offset, font)
        changes.append(dict(**match, part=shape.source_part, shapeId=shape.shape_id, paragraph=index,
                            **({'cell':list(cell)} if cell is not None else {})))
    result = dict(parts)
    for part in {c['part'] for c in changes}:
        result[part] = etree.tostring(roots[part], xml_declaration=True, encoding='UTF-8', standalone=True)
    return result, changes


def materialize_table_fonts(parts, model, changes):
    """A table run may inherit a missing face with no typeface XML to replace.
    Materialize its measured fallback so parser, fitter and PowerPoint agree."""
    from lxml import etree
    from runtime.security import NS
    from runtime.tables import cell_frame
    def walk(items):
        for item in items:
            yield item
            yield from walk(item.children)
    shapes={s.source_object_id:s for slide in model.slides for s in walk(slide.objects)}
    mapping={c['original']:c['replacement'] for c in changes};roots={}
    for table in model.tables:
        shape=shapes.get(table.linked_object_id)
        if not shape or table.source_level!='slide':continue
        for r,row in enumerate(table.rows):
            for c,cell in enumerate(row.cells):
                for index,paragraph in enumerate(cell_frame(table,cell,shape).paragraphs):
                    family=mapping.get(paragraph.font_family)
                    if not family:continue
                    root=roots.setdefault(shape.source_part,xml(parts[shape.source_part]))
                    nodes=root.xpath('.//p:graphicFrame[p:nvGraphicFramePr/p:cNvPr/@id=$id]//a:tbl/a:tr',namespaces=NS,id=str(shape.shape_id))
                    if r>=len(nodes):continue
                    cells=nodes[r].findall('a:tc',NS)
                    if c>=len(cells):continue
                    paragraphs=cells[c].findall('a:txBody/a:p',NS)
                    if index>=len(paragraphs):continue
                    p=paragraphs[index];props=p.find('a:pPr',NS)
                    if props is None:props=etree.Element('{%s}pPr'%NS['a']);p.insert(0,props)
                    default=props.find('a:defRPr',NS)
                    if default is None:
                        default=etree.Element('{%s}defRPr'%NS['a'])
                        props.insert(next((i for i,n in enumerate(props) if etree.QName(n).localname=='extLst'),len(props)),default)
                    for prop in [default,*p.findall('a:r/a:rPr',NS),*p.findall('a:fld/a:rPr',NS)]:
                        for tag,following in [('latin',{'ea','cs','sym','hlinkClick','hlinkMouseOver','rtl','extLst'}),('ea',{'cs','sym','hlinkClick','hlinkMouseOver','rtl','extLst'}),('cs',{'sym','hlinkClick','hlinkMouseOver','rtl','extLst'})]:
                            node=prop.find('a:'+tag,NS)
                            if node is not None:continue  # Explicit fonts use the regular substitution pass.
                            node=etree.Element('{%s}%s'%(NS['a'],tag),typeface=family)
                            prop.insert(next((i for i,n in enumerate(prop) if etree.QName(n).localname in following),len(prop)),node)
    return {**parts,**{name:etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True) for name,root in roots.items()}}

def prepare_font_copy(parts, folder, changes):
    mapping = {c['original']: c['replacement'] for c in changes}
    result = dict(parts)
    if mapping:
        for name, data in parts.items():
            if not name.startswith('ppt/') or not name.endswith('.xml'):
                continue
            root = xml(data)
            changed = False
            for node in root.iter():
                family = node.get('typeface')
                if family in mapping:
                    node.set('typeface', mapping[family]); changed = True
            if changed:
                from lxml import etree
                result[name] = etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    path = folder/'prepared.pptx'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, data in result.items():
            archive.writestr(name, data)
    return path


def effective_source(source, folder, data):
    path = folder/'prepared.pptx' if data.get('preparedSha256') else source
    expected = data.get('preparedSha256') or data['sha256']
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('pptx_source_changed')
    return path
