import unittest
from runtime.package_cleanup import compact_package, normalize_presentation_catalogs, normalize_layout_extensions, REL
from runtime.security import NS, xml


def package(with_notes=False):
    def rels(items):
        body = ''.join(f'<Relationship Id="{id}" Type="{REL}{kind}" Target="{target}"/>'
                       for id, kind, target in items)
        return f'<Relationships xmlns="{NS["pr"]}">{body}</Relationships>'.encode()

    parts = {
        '[Content_Types].xml': f'<Types xmlns="{NS["ct"]}"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/></Types>'.encode(),
        '_rels/.rels': rels([('r1', 'officeDocument', 'ppt/presentation.xml')]),
        'ppt/presentation.xml': f'<p:presentation xmlns:p="{NS["p"]}" xmlns:r="{NS["r"]}"><p:notesMasterIdLst><p:notesMasterId r:id="n1"/></p:notesMasterIdLst><p:sldIdLst><p:sldId id="256" r:id="s1"/></p:sldIdLst></p:presentation>'.encode(),
        'ppt/_rels/presentation.xml.rels': rels([('s1', 'slide', 'slides/slide1.xml'), ('n1', 'notesMaster', 'notesMasters/notesMaster1.xml')]),
        'ppt/slides/slide1.xml': f'<p:sld xmlns:p="{NS["p"]}"/>'.encode(),
        'ppt/notesMasters/notesMaster1.xml': f'<p:notesMaster xmlns:p="{NS["p"]}"/>'.encode(),
    }
    if with_notes:
        parts.update({
            'ppt/slides/_rels/slide1.xml.rels': rels([('n1', 'notesSlide', '../notesSlides/notesSlide1.xml')]),
            'ppt/notesSlides/notesSlide1.xml': f'<p:notes xmlns:p="{NS["p"]}"/>'.encode(),
            'ppt/notesSlides/_rels/notesSlide1.xml.rels': rels([('n1', 'notesMaster', '../notesMasters/notesMaster1.xml')]),
        })
    return parts


class PackageCleanupTest(unittest.TestCase):
    def test_removing_last_unused_notes_master_removes_catalog(self):
        source = package()
        output, report = compact_package(source)
        self.assertIsNone(xml(output['ppt/presentation.xml']).find('p:notesMasterIdLst', NS))
        self.assertNotIn('ppt/notesMasters/notesMaster1.xml', output)
        self.assertEqual(report['removedEmptyCatalogs'], ['notesMasterIdLst'])
        self.assertEqual(report['version'], 4)
        self.assertEqual(output['ppt/slides/slide1.xml'], source['ppt/slides/slide1.xml'])
        self.assertIsNotNone(xml(source['ppt/presentation.xml']).find('p:notesMasterIdLst', NS))

    def test_used_source_reference_notes_are_preserved(self):
        source = package(with_notes=True)
        output, report = compact_package(source)
        for name in ('ppt/presentation.xml', 'ppt/notesMasters/notesMaster1.xml', 'ppt/notesSlides/notesSlide1.xml'):
            self.assertEqual(output[name], source[name])
        self.assertEqual(report['removedEmptyCatalogs'], [])

    def test_existing_empty_catalogs_are_removed_without_touching_other_parts(self):
        source = package()
        source['ppt/presentation.xml'] = f'<p:presentation xmlns:p="{NS["p"]}"><p:sldMasterIdLst/><p:notesMasterIdLst/><p:handoutMasterIdLst/></p:presentation>'.encode()
        output, removed = normalize_presentation_catalogs(source)
        self.assertEqual(set(removed), {'notesMasterIdLst', 'sldMasterIdLst', 'handoutMasterIdLst'})
        self.assertEqual([name for name in source if source[name] != output[name]], ['ppt/presentation.xml'])
        again, removed = normalize_presentation_catalogs(output)
        self.assertEqual(again, output)
        self.assertEqual(removed, [])

    def test_invalid_layout_mod_attribute_is_removed_but_extension_payload_survives(self):
        name='ppt/slideLayouts/slideLayout7.xml'
        source={name:f'<p:sldLayout xmlns:p="{NS["p"]}"><p:extLst mod="1"><p:ext uri="custom"><data xmlns="urn:custom">keep</data></p:ext></p:extLst></p:sldLayout>'.encode()}
        result,changes=normalize_layout_extensions(source)
        self.assertEqual(changes,[name])
        node=xml(result[name]).find('p:extLst',NS)
        self.assertNotIn('mod',node.attrib)
        self.assertEqual(node[0][0].text,'keep')
        self.assertIn(b'mod="1"',source[name])
        self.assertEqual(normalize_layout_extensions(result),(result,[]))


if __name__ == '__main__':
    unittest.main()
