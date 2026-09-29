"""Upload boundary, stricter than the offline research parser's archive limits."""
import io
import posixpath
import zipfile
from urllib.parse import unquote
from lxml import etree

NS = {'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
      'pr': 'http://schemas.openxmlformats.org/package/2006/relationships',
      'ct': 'http://schemas.openxmlformats.org/package/2006/content-types'}

def xml(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('pptx_external_entity')
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))

def relationship_target(rel_part, target):
    """Resolve an internal OPC URI, including absolute paths and self-fragments."""
    source_dir = posixpath.dirname(posixpath.dirname(rel_part))
    raw = unquote(target.split('#', 1)[0])
    owner = posixpath.join(source_dir, posixpath.basename(rel_part)[:-5])
    resolved = owner if not raw and '#' in target else posixpath.normpath(raw.lstrip('/') if raw.startswith('/') else posixpath.join(source_dir, raw))
    if (not raw and '#' not in target) or resolved.startswith('../') or resolved in ('..', '.', '') or '\\' in raw or ':' in raw:
        raise ValueError('pptx_unsafe_relationship')
    return resolved

def read_package(payload, generated=False):
    if len(payload) > (160 if generated else 40) * 1024 * 1024:
        raise ValueError('pptx_too_large')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        infos = archive.infolist()
        if len(infos) > 10000 or sum(i.file_size for i in infos) > 200 * 1024 * 1024:
            raise ValueError('pptx_archive_limit')
        names = [i.filename for i in infos]
        if len(set(names)) != len(names):
            raise ValueError('pptx_duplicate_parts')
        for item in infos:
            name = item.filename
            if name.startswith('/') or '\\' in name or '..' in name.split('/') or item.flag_bits & 1:
                raise ValueError('pptx_unsafe_archive_path')
            if item.file_size / max(item.compress_size, 1) > 2000:
                raise ValueError('pptx_compression_limit')
            if any(s in name.lower() for s in ('vbaproject', 'activex/', '_xmlsignatures/')):
                raise ValueError('pptx_active_content_not_supported')
        parts = {i.filename: archive.read(i) for i in infos if not i.is_dir()}
    for name,data in parts.items():
        if name.startswith('ppt/embeddings/'):
            if not name.lower().endswith('.xlsx'):raise ValueError('pptx_embedded_object_not_supported')
            with zipfile.ZipFile(io.BytesIO(data)) as book:
                if len(book.infolist())>2000 or sum(i.file_size for i in book.infolist())>40*1024*1024:raise ValueError('pptx_embedded_archive_limit')
                for info in book.infolist():
                    if 'vbaproject' in info.filename.lower() or 'externallinks/' in info.filename.lower():raise ValueError('pptx_active_content_not_supported')
                    if info.filename.endswith(('.xml','.rels')):
                        item=xml(book.read(info))
                        if info.filename.endswith('.rels') and any(r.get('TargetMode')=='External' and not r.get('Type','').endswith('/hyperlink') for r in item):raise ValueError('pptx_external_resource_not_supported')
                        for formula in item.xpath('//*[local-name()="f"]/text()'):
                            if any(t in formula.upper() for t in ('WEBSERVICE','HYPERLINK','DDE','RTD','HTTP:','HTTPS:','FILE:','[')):raise ValueError('pptx_external_resource_not_supported')
    if not all(k in parts for k in ('ppt/presentation.xml', '[Content_Types].xml', 'ppt/_rels/presentation.xml.rels')):
        raise ValueError('pptx_invalid_package')
    for name, data in parts.items():
        if name.endswith(('.xml', '.rels')):
            root = xml(data)
            if name.endswith('.rels'):
                for rel in root:
                    if rel.get('Type','').endswith(('/oleObject','/control','/vbaProject')):raise ValueError('pptx_active_content_not_supported')
                    if rel.get('TargetMode') == 'External':
                        if not rel.get('Type', '').endswith('/hyperlink') or not rel.get('Target', '').startswith(('https://', 'http://', 'mailto:')):
                            raise ValueError('pptx_external_resource_not_supported')
                    else:
                        # Internal relationships must not escape the package.
                        target = relationship_target(name,rel.get('Target',''))
                        if target not in parts:raise ValueError('pptx_broken_relationship')
    return parts

def slide_order(parts):
    rels = {r.get('Id'): r.get('Target') for r in xml(parts['ppt/_rels/presentation.xml.rels'])}
    order = []
    for item in xml(parts['ppt/presentation.xml']).xpath('./p:sldIdLst/p:sldId', namespaces=NS):
        target = rels[item.get('{%s}id' % NS['r'])]
        order.append(target.lstrip('/') if target.startswith('/') else posixpath.normpath('ppt/' + target))
    if not 1 <= len(order) <= 100 or len(set(order)) != len(order):
        raise ValueError('pptx_slide_count_limit')
    return order
