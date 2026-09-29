import unittest
from types import SimpleNamespace as Obj
from runtime.tables import cell_frame
from runtime.text_frames import TemplateTextFitter
from runtime.security import NS


class TableListsTest(unittest.TestCase):
    def frame(self, marker):
        properties=Obj(raw_xml=f'<a:pPr xmlns:a="{NS["a"]}" marL="508000" indent="-381000">{marker}</a:pPr>',
            margin_left_emu=508000,margin_right_emu=0,indent_emu=-381000,
            line_spacing=None,space_before=None,space_after=None)
        font=Obj(font_family='DejaVu Sans',font_size_pt=18,bold=False,italic=False)
        p=Obj(runs=[Obj(text='Body',effective_style=font)],default_style=font,properties=properties,bullet=Obj(type='char'))
        style=Obj(font_family='DejaVu Sans',font_size_pt=18,font_weight=400,italic=False)
        cell=Obj(row=0,col=0,col_span=1,row_span=1,margins={},effective_style=style,rich_text=Obj(paragraphs=[p]))
        table=Obj(grid_column_widths_emu=[400*12700],rows=[Obj(height_emu=150*12700)])
        shape=Obj(source_object_id='table',raw_xml_ref={},geometry_full=None)
        return cell_frame(table,cell,shape)

    def test_char_and_native_number_lists_are_measured_in_table_cells(self):
        for marker in ['<a:buChar char="●"/>','<a:buAutoNum type="arabicParenR" startAt="3"/>']:
            frame=self.frame(marker)
            self.assertEqual(frame.paragraphs[0].reason_codes,[])
            self.assertEqual(TemplateTextFitter().fit(frame,['Первая запись','Вторая запись']).status,'fits')
        self.assertTrue(self.frame('<a:buAutoNum type="arabicParenR" startAt="3"/>').paragraphs[0].bullet_auto)

    def test_unknown_numbering_and_image_bullets_remain_explicitly_unsupported(self):
        for marker in ['<a:buAutoNum type="unknown"/>','<a:buBlip/>']:
            frame=self.frame(marker)
            self.assertIn('table_bullet_unsupported',frame.paragraphs[0].reason_codes)
            self.assertEqual(TemplateTextFitter().fit(frame,['Первая запись']).status,'unverifiable')
