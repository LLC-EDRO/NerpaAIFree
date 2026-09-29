import unittest
from runtime.fonts import choose_bullet_fonts
from runtime.text_frames import TemplateParagraph
from runtime.security import xml, NS


class BulletFontsTest(unittest.TestCase):
    def test_supported_symbols_and_automatic_numbers_do_not_need_replacement(self):
        values=[TemplateParagraph(has_text=True,font_family='DejaVu Sans',font_size_pt=18,bullet_text='●'),
                TemplateParagraph(has_text=True,font_family='Missing Font',font_size_pt=18,bullet_text='1)',bullet_auto=True)]
        self.assertEqual(choose_bullet_fonts(values),[])


if __name__ == '__main__':
    unittest.main()

class InheritedMarkerFontsTest(unittest.TestCase):
    def test_inherited_font_is_materialized_on_exact_paragraph_only(self):
        from types import SimpleNamespace as Obj
        from unittest.mock import patch
        from runtime.fonts import recover_marker_fonts
        from runtime.text_frames import TemplateTextFrame
        part = 'ppt/slides/slide29.xml'
        original = f'''<p:sld xmlns:p="{NS['p']}" xmlns:a="{NS['a']}"><p:cSld><p:spTree><p:sp>
        <p:nvSpPr><p:cNvPr id="883"/></p:nvSpPr><p:txBody><a:bodyPr/><a:p><a:pPr><a:buChar char="●"/><a:defRPr sz="1800"/></a:pPr><a:r><a:rPr><a:latin typeface="Body Font"/></a:rPr><a:t>Text</a:t></a:r></a:p>
        <a:p><a:r><a:t>Unaffected</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'''.encode()
        shape=Obj(hidden=False,shape_id=883,source_part=part,rich_text=True,text_frame=True,object_kind='shape',children=[])
        model=Obj(tables=[],slides=[Obj(objects=[shape])])
        paragraph=TemplateParagraph(has_text=True,font_family='Body Font',font_size_pt=18,bullet_text='●')
        frame=TemplateTextFrame(source_element_id='a',source_fingerprint='x',width_emu=1,height_emu=1,paragraphs=[paragraph])
        with patch('runtime.text_frames.source_text_frame',return_value=frame):
            result, changes=recover_marker_fonts({part:original},model)
        self.assertEqual(len(changes),1)
        root=xml(result[part]); prop=root.find('.//a:pPr',NS)
        self.assertEqual(prop.find('a:buFont',NS).get('typeface'),'DejaVu Sans')
        self.assertEqual([n.tag.split('}')[-1] for n in prop],['buFont','buChar','defRPr'])
        self.assertEqual(root.find('.//a:rPr/a:latin',NS).get('typeface'),'Body Font')
        self.assertEqual(root.findall('.//a:p',NS)[1].find('a:pPr',NS),None)
        self.assertNotIn(b'buFont',original)
