"""Read-only acceptance of native pictures against the approved assembled deck.
Usage: python3 test/image-preservation-acceptance.py <project-directory>
"""
import hashlib
import json
from pathlib import Path
import posixpath
import sys
from zipfile import ZipFile
from xml.etree import ElementTree as ET

NS = {
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}

def relations(z, part):
    path = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part) + '.rels')
    return {n.get('Id'): posixpath.normpath(posixpath.join(posixpath.dirname(part), n.get('Target'))) for n in ET.fromstring(z.read(path))}

def slides(z):
    rels = relations(z, 'ppt/presentation.xml')
    return [rels[n.get('{%s}id' % NS['r'])] for n in ET.fromstring(z.read('ppt/presentation.xml')).findall('p:sldIdLst/p:sldId', NS)]

def picture(z, part, shape_id):
    root = ET.fromstring(z.read(part))
    node = next(n for n in root.findall('.//p:pic', NS) if n.find('p:nvPicPr/p:cNvPr', NS).get('id') == str(shape_id))
    rid = node.find('.//a:blip', NS).get('{%s}embed' % NS['r'])
    return ET.tostring(node), hashlib.sha256(z.read(relations(z, part)[rid])).hexdigest()

root = Path(sys.argv[1])
p = json.loads((root / 'project.json').read_text())
assert p['status'] == 'complete' and not p['busy'], 'Project is not complete'
revision = p['revision']
output = root / f'output-r{revision}'
quality = json.loads((output / 'render-quality.json').read_text())
assert not quality['issues'], quality['issues']
assert quality['pptxSha256'] == hashlib.sha256((output / 'presentation.pptx').read_bytes()).hexdigest()
preserved = []
changed = []
with ZipFile(root / f'assembled-r{revision}/presentation.pptx') as source, ZipFile(output / 'presentation.pptx') as result:
    assert result.testzip() is None
    before, after = slides(source), slides(result)
    assert len(before) == len(after) == p['result']['slides']
    for c in p['visuals']['choices']:
        i, identity = c['slideIndex'], c['shapeId']
        # This acceptance compares native p:pic; placeholder fills have separate tests.
        if c['slot']['kind'] != 'picture': continue
        a, b = picture(source, before[i], identity), picture(result, after[i], identity)
        if c.get('applied'):
            assert a[1] != b[1], (i, identity, 'expected replacement')
            changed.append({'slide': i + 1, 'shapeId': identity})
        else:
            assert a == b, (i, identity, 'original altered')
            preserved.append({'slide': i + 1, 'shapeId': identity, 'role': c['slot']['sourceRole']})
state = json.loads((output / 'repair-state.json').read_text())
report = {'slides': p['result']['slides'], 'preserved': preserved, 'changed': changed, 'renderIssues': quality['issues'], 'repairRounds': state['rounds'], 'exportsInRepairHistory': len(state['history'])}
print(json.dumps(report, ensure_ascii=False, indent=2))
