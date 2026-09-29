"""Complete implicit notes-master dependencies required by desktop PowerPoint.

The XML schema permits an omitted notesStyle/theme relationship, but PowerPoint
can reject the resulting master before loading any slides. Keep existing notes,
styles and themes intact; fill only missing defaults. No slide is rewritten.
"""
import posixpath
from lxml import etree
from app.presentation.parser.archive import relationship_part
from runtime.security import NS, xml, relationship_target

THEME_REL = NS['r'] + '/theme'


def serialize(root):
    return etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)


def normalize_notes_masters(parts):
    result, changed = dict(parts), []
    masters = [name for name in parts if name.startswith('ppt/notesMasters/') and name.endswith('.xml')]
    if not masters:
        return result, changed

    def theme_target(owner):
        rel_name = relationship_part(owner)
        if rel_name not in parts:
            return None
        for rel in xml(parts[rel_name]):
            if rel.get('Type') == THEME_REL and rel.get('TargetMode') != 'External':
                target = relationship_target(rel_name, rel.get('Target', ''))
                if target in parts:
                    return target
        return None

    fallback_theme = theme_target('ppt/presentation.xml')
    if fallback_theme is None:
        fallback_theme = next((target for name in parts
                               if name.startswith('ppt/slideMasters/') and name.endswith('.xml')
                               if (target := theme_target(name)) is not None), None)
    for name in masters:
        root = xml(parts[name])
        if root.tag != '{%s}notesMaster' % NS['p'] or root.find('p:cSld', NS) is None:
            continue
        dirty = False
        if root.find('p:notesStyle', NS) is None:
            # Standard notes defaults, using the document's theme. Copying the
            # presentation text style could import hyperlinks with foreign rIds.
            style = etree.Element('{%s}notesStyle' % NS['p'])
            for level in range(1, 10):
                paragraph = etree.SubElement(style, '{%s}lvl%dpPr' % (NS['a'], level),
                                             marL=str((level - 1) * 457200), algn='l')
                props = etree.SubElement(paragraph, '{%s}defRPr' % NS['a'], sz='1200')
                fill = etree.SubElement(props, '{%s}solidFill' % NS['a'])
                etree.SubElement(fill, '{%s}schemeClr' % NS['a'], val='tx1')
                for tag, face in [('latin', '+mn-lt'), ('ea', '+mn-ea'), ('cs', '+mn-cs')]:
                    etree.SubElement(props, '{%s}%s' % (NS['a'], tag), typeface=face)
            extension = root.find('p:extLst', NS)
            root.insert(list(root).index(extension) if extension is not None else len(root), style)
            result[name] = serialize(root)
            dirty = True
        if fallback_theme is not None and theme_target(name) is None:
            rel_name = relationship_part(name)
            relationships = xml(parts[rel_name]) if rel_name in parts else etree.Element('{%s}Relationships' % NS['pr'], nsmap={None: NS['pr']})
            # An existing but broken theme link is a different validation error;
            # never append a second theme relationship or overwrite that link.
            if not any(rel.get('Type') == THEME_REL for rel in relationships):
                ids = {rel.get('Id') for rel in relationships}
                index = 1
                while 'rId%d' % index in ids:
                    index += 1
                etree.SubElement(relationships, '{%s}Relationship' % NS['pr'],
                                 Id='rId%d' % index, Type=THEME_REL,
                                 Target=posixpath.relpath(fallback_theme, posixpath.dirname(name)))
                result[rel_name] = serialize(relationships)
                dirty = True
        if dirty:
            changed.append(name)
    return result, changed
