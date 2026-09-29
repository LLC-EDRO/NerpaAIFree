"""Collect unreachable output parts without rebuilding native slide content.

Relationships can be implicit (themes, chart styles, notes masters, fonts, etc.).
Never infer their liveness from r:id alone. Only known explicit relationships
whose last XML reference was removed by filling are discarded before traversal.
The original package and opaque reachable payloads are not modified.
"""
from lxml import etree
from app.presentation.parser.archive import relationship_part
from runtime.security import NS, xml, relationship_target
from runtime.notes_compat import normalize_notes_masters

VERSION = 4
REL = NS['r'] + '/'
STRICT_REL = 'http://purl.oclc.org/ooxml/officeDocument/relationships'
EXPLICIT = {REL + kind for kind in ('image', 'chart', 'hyperlink')}
EXPLICIT_OWNERS = {NS['p'], NS['a'], 'http://schemas.openxmlformats.org/drawingml/2006/chart', 'urn:schemas-microsoft-com:vml'}


def normalize_presentation_catalogs(parts):
    """Omit empty optional master lists: PowerPoint repairs an empty notes list.

    Also handles already-generated packages. Do not rewrite slide content or
    remove nonempty catalogs, including notes used for source references.
    """
    result = dict(parts)
    name = 'ppt/presentation.xml'
    root = xml(result[name])
    removed = []
    for tag in ('notesMasterIdLst', 'handoutMasterIdLst', 'sldMasterIdLst'):
        for listing in root.findall('p:' + tag, NS):
            if len(listing) == 0:
                root.remove(listing)
                removed.append(tag)
    if removed:
        result[name] = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
    return result, removed


def normalize_layout_extensions(parts):
    """Some exporters write ExtensionListWithModification's `mod` attribute
    on a slide layout's plain ExtensionList. Preserve all extension children;
    remove only the unqualified attribute rejected by the Open XML schema."""
    result, changed = dict(parts), []
    for name, data in parts.items():
        if not name.startswith('ppt/slideLayouts/') or not name.endswith('.xml'):
            continue
        root = xml(data)
        if root.tag != '{%s}sldLayout' % NS['p']: continue
        for node in root.findall('p:extLst', NS):
            if 'mod' in node.attrib:
                del node.attrib['mod']
                changed.append(name)
        if name in changed:
            result[name] = etree.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    return result, changed


def compact_package(parts):
    result = dict(parts)
    roots = {}
    changed = set()
    removed_relationships = 0

    def tree(name):
        if name not in roots:
            roots[name] = xml(result[name])
        return roots[name]

    def discard(name, parent, child):
        parent.remove(child)
        changed.add(name)

    # Catalogs are not usage: a master lists every available source layout.
    # Keep only the layouts selected by actual output slides and their masters.
    presentation = 'ppt/presentation.xml'
    pres_rels = relationship_part(presentation)
    rels = tree(pres_rels)
    by_id = {r.get('Id'): r for r in rels}
    slide_ids = tree(presentation).find('p:sldIdLst', NS)
    selected = set()
    for node in slide_ids if slide_ids is not None else []:
        relation = by_id.get(node.get('{%s}id' % NS['r']))
        if relation is None or relation.get('Type') != REL + 'slide':
            raise ValueError('pptx_broken_relationship')
        selected.add(relationship_target(pres_rels, relation.get('Target', '')))

    def targets(owners, kind):
        found = set()
        for owner in owners:
            if owner not in result:
                raise ValueError('pptx_broken_relationship')
            name = relationship_part(owner)
            if name in result:
                for relation in tree(name):
                    if relation.get('Type') == REL + kind and relation.get('TargetMode') != 'External':
                        found.add(relationship_target(name, relation.get('Target', '')))
        return found

    if not selected:
        raise ValueError('pptx_invalid_package')
    layouts = targets(selected, 'slideLayout')
    masters = targets(layouts, 'slideMaster')
    notes = targets(selected, 'notesSlide')
    note_masters = targets(notes, 'notesMaster')

    def prune_catalog(owner, kind, allowed, list_name):
        nonlocal removed_relationships
        name = relationship_part(owner)
        if name not in result:
            return
        rels = tree(name)
        removed = set()
        for relation in list(rels):
            if relation.get('Type') == REL + kind and relationship_target(name, relation.get('Target', '')) not in allowed:
                removed.add(relation.get('Id'))
                discard(name, rels, relation)
                removed_relationships += 1
        listing = tree(owner).find('p:' + list_name, NS)
        if listing is not None:
            for node in list(listing):
                if node.get('{%s}id' % NS['r']) in removed:
                    discard(owner, listing, node)

    # An unusual source with an incomplete inheritance chain may rely on the
    # viewer's defaults. Keep its catalog rather than guessing what is unused.
    explicit_inheritance = all(targets({slide}, 'slideLayout') for slide in selected) and all(targets({layout}, 'slideMaster') for layout in layouts)
    if explicit_inheritance:
        prune_catalog(presentation, 'slideMaster', masters, 'sldMasterIdLst')
    if all(targets({note}, 'notesMaster') for note in notes):
        prune_catalog(presentation, 'notesMaster', note_masters, 'notesMasterIdLst')
    for master in masters if explicit_inheritance else []:
        prune_catalog(master, 'slideLayout', layouts, 'sldLayoutIdLst')

    # Only XML/VML owners with explicit reference semantics are pruned. Unknown
    # relationship types and binary owners remain conservative dependencies.
    for name in result:
        if not name.endswith('.rels'):
            continue
        rels = tree(name)
        owner = '' if name == '_rels/.rels' else name.replace('/_rels/', '/')[:-5]
        if owner not in result or not owner.endswith(('.xml', '.vml')):
            continue
        if etree.QName(tree(owner)).namespace not in EXPLICIT_OWNERS:
            continue
        references = {value for node in tree(owner).iter() for key, value in node.attrib.items()
                      if key.startswith(('{%s}' % NS['r'], '{%s}' % STRICT_REL))
                      or key == '{urn:schemas-microsoft-com:office:office}relid'}
        for relation in list(rels):
            kind = relation.get('Type', '')
            explicit = kind in EXPLICIT or (kind == REL + 'package' and tree(owner).tag == '{http://schemas.openxmlformats.org/drawingml/2006/chart}chartSpace')
            if explicit and relation.get('Id') not in references:
                discard(name, rels, relation)
                removed_relationships += 1

    # The source thumbnail depicts the OLD presentation; omitting an optional
    # thumbnail is safer than shipping a picture of an unselected source slide.
    if '_rels/.rels' not in result:
        raise ValueError('pptx_invalid_package')
    root_rels = tree('_rels/.rels')
    for relation in list(root_rels):
        if relation.get('Type') == 'http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail':
            discard('_rels/.rels', root_rels, relation)
            removed_relationships += 1

    reachable = {'[Content_Types].xml'}
    pending = ['']
    visited = set()
    while pending:
        owner = pending.pop()
        if owner in visited:
            continue
        visited.add(owner)
        if owner:
            if owner not in result:
                raise ValueError('pptx_broken_relationship')
            reachable.add(owner)
        name = relationship_part(owner) if owner else '_rels/.rels'
        if name not in result:
            continue
        reachable.add(name)
        for relation in tree(name):
            if relation.get('TargetMode') != 'External':
                pending.append(relationship_target(name, relation.get('Target', '')))
    if presentation not in reachable or not selected.issubset(reachable):
        raise ValueError('pptx_invalid_package')

    # Content type declarations describe parts; they do not make them reachable.
    types = tree('[Content_Types].xml')
    extensions = {name.rsplit('.', 1)[-1].lower() for name in reachable if '.' in name}
    for node in list(types):
        if node.tag == '{%s}Override' % NS['ct']:
            keep = relationship_target('_rels/.rels', node.get('PartName', '')) in reachable
        else:
            keep = node.get('Extension', '').lower() in extensions
        if not keep:
            discard('[Content_Types].xml', types, node)
    removed = set(result) - reachable
    for name in changed & reachable:
        result[name] = etree.tostring(roots[name], xml_declaration=True, encoding='UTF-8', standalone=True)
    output = {name: data for name, data in result.items() if name in reachable}
    output, removed_catalogs = normalize_presentation_catalogs(output)
    output, normalized_extensions = normalize_layout_extensions(output)
    output, normalized_notes = normalize_notes_masters(output)
    report = dict(version=VERSION, beforeParts=len(parts), afterParts=len(output),
                  removedParts=len(removed), removedMedia=sum(name.startswith('ppt/media/') for name in removed),
                  removedBytes=sum(len(parts[name]) for name in removed), removedRelationships=removed_relationships,
                  removedEmptyCatalogs=removed_catalogs, normalizedLayoutExtensions=normalized_extensions,
                  normalizedNotesMasters=normalized_notes)
    return output, report
