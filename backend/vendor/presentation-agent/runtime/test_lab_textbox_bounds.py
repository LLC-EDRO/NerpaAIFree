import unittest
from types import SimpleNamespace as Obj
from unittest.mock import patch
from runtime.text_frames import TemplateTextFrame, TemplateParagraph
from runtime.textbox_repair import recover_textbox_bounds
from runtime.security import xml, NS


class TextboxBoundsTest(unittest.TestCase):
    def fixture(self, anchor='t', fill='noFill'):
        part='ppt/slides/slide7.xml'
        content=f'''<p:sld xmlns:p="{NS['p']}" xmlns:a="{NS['a']}"><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="901"/></p:nvSpPr><p:spPr><a:xfrm><a:off x="100" y="200"/><a:ext cx="3810000" cy="127000"/></a:xfrm><a:prstGeom prst="rect"/><a:{fill}/></p:spPr><p:txBody><a:bodyPr anchor="{anchor}"><a:noAutofit/></a:bodyPr></p:txBody></p:sp></p:spTree></p:cSld></p:sld>'''.encode()
        shape=Obj(object_kind='shape',hidden=False,text_frame=True,shape_id=901,source_part=part,rich_text=Obj(paragraphs=[Obj(runs=[Obj(text='Заголовок')])]))
        frame=TemplateTextFrame(source_element_id='fixture',source_fingerprint='x',width_emu=300*12700,height_emu=10*12700,
            top_emu=7*12700,bottom_emu=7*12700,paragraphs=[TemplateParagraph(has_text=True,font_family='DejaVu Sans',font_size_pt=20)])
        return {part:content},shape,frame

    def test_only_invisible_height_changes_and_original_package_stays_immutable(self):
        parts,shape,frame=self.fixture()
        with patch('runtime.textbox_repair.source_text_frame',return_value=frame):
            result,changes=recover_textbox_bounds(parts,[shape])
        self.assertEqual(len(changes),1)
        original=xml(parts[shape.source_part]); updated=xml(result[shape.source_part])
        before=original.find('.//a:xfrm/a:ext',NS);after=updated.find('.//a:xfrm/a:ext',NS)
        self.assertEqual(before.get('cx'),after.get('cx'))
        self.assertGreater(int(after.get('cy')),frame.top_emu+frame.bottom_emu)
        self.assertEqual(original.find('.//a:xfrm/a:off',NS).attrib,updated.find('.//a:xfrm/a:off',NS).attrib)
        self.assertEqual(before.get('cy'),'127000')

    def test_centered_or_visible_shapes_are_not_resized(self):
        for anchor,fill in [('ctr','noFill'),('t','solidFill')]:
            parts,shape,frame=self.fixture(anchor,fill)
            with patch('runtime.textbox_repair.source_text_frame',return_value=frame):
                result,changes=recover_textbox_bounds(parts,[shape])
            self.assertEqual(changes,[])
            self.assertEqual(result,parts)
