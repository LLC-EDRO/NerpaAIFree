import unittest
from runtime.notes_compat import normalize_notes_masters, THEME_REL
from runtime.package_cleanup import compact_package
from runtime.security import NS, xml, relationship_target
from runtime.test_lab_package_cleanup import package


def fixture():
    parts = package(with_notes=True)
    parts['ppt/notesMasters/notesMaster1.xml'] = f'<p:notesMaster xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/></p:spTree></p:cSld><p:clrMap accent1="accent1"/></p:notesMaster>'.encode()
    parts['ppt/theme/design.xml'] = f'<a:theme xmlns:a="{NS["a"]}" name="source"/>'.encode()
    rels = xml(parts['ppt/_rels/presentation.xml.rels'])
    from lxml import etree
    etree.SubElement(rels, '{%s}Relationship' % NS['pr'], Id='theme', Type=THEME_REL, Target='theme/design.xml')
    parts['ppt/_rels/presentation.xml.rels'] = etree.tostring(rels)
    return parts


class NotesCompatibilityTest(unittest.TestCase):
    def test_repairs_missing_style_and_theme_without_changing_slides_or_notes(self):
        source = fixture()
        output, changed = normalize_notes_masters(source)
        self.assertEqual(changed, ['ppt/notesMasters/notesMaster1.xml'])
        style = xml(output[changed[0]]).find('p:notesStyle', NS)
        self.assertEqual(len(style), 9)
        rel_name = 'ppt/notesMasters/_rels/notesMaster1.xml.rels'
        theme = xml(output[rel_name])[0]
        self.assertEqual(relationship_target(rel_name, theme.get('Target')), 'ppt/theme/design.xml')
        for name, data in source.items():
            if name not in changed:
                self.assertEqual(output[name], data, name)
        self.assertEqual(normalize_notes_masters(output), (output, []))

    def test_keeps_existing_custom_notes_styles_and_theme_byte_identical(self):
        source, _ = normalize_notes_masters(fixture())
        self.assertEqual(normalize_notes_masters(source), (source, []))

    def test_theme_typography_before_extensions_does_not_copy_foreign_relationships(self):
        from lxml import etree
        source = fixture()
        root = xml(source['ppt/presentation.xml'])
        root.append(xml(f'<p:defaultTextStyle xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><a:lvl1pPr><a:defRPr sz="1600"><a:latin typeface="Custom font"/></a:defRPr></a:lvl1pPr></p:defaultTextStyle>'.encode()))
        source['ppt/presentation.xml'] = etree.tostring(root)
        master = 'ppt/notesMasters/notesMaster1.xml'
        root = xml(source[master]); etree.SubElement(root, '{%s}extLst' % NS['p'])
        source[master] = etree.tostring(root)
        rel_name = 'ppt/notesMasters/_rels/notesMaster1.xml.rels'
        source[rel_name] = f'<Relationships xmlns="{NS["pr"]}"><Relationship Id="rId1" Type="custom" Target="elsewhere.xml"/></Relationships>'.encode()
        output, _ = normalize_notes_masters(source)
        root = xml(output[master])
        self.assertEqual([etree.QName(e).localname for e in root][-2:], ['notesStyle', 'extLst'])
        self.assertEqual(root.find('p:notesStyle/a:lvl1pPr/a:defRPr/a:latin', NS).get('typeface'), '+mn-lt')
        self.assertEqual(xml(output[rel_name])[1].get('Id'), 'rId2')

    def test_compaction_repairs_and_is_idempotent_but_omits_unused_notes(self):
        source = fixture()
        output, report = compact_package(source)
        self.assertEqual(report['normalizedNotesMasters'], ['ppt/notesMasters/notesMaster1.xml'])
        self.assertEqual(compact_package(output)[0], output)
        source.pop('ppt/slides/_rels/slide1.xml.rels')
        output, report = compact_package(source)
        self.assertEqual(report['normalizedNotesMasters'], [])
        self.assertNotIn('ppt/notesMasters/notesMaster1.xml', output)


if __name__ == '__main__':
    unittest.main()
