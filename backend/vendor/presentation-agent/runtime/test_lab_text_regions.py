import unittest
import pymupdf
from runtime.security import xml, NS
from runtime.text_frames import TemplateTextFrame, TemplateParagraph, TemplateTextFitter
from runtime.text_regions import text_limits, constrained_frame
from runtime.render_quality import inspect_page

class TextRegionsTest(unittest.TestCase):
    def root(self, anchor='t'):
        return xml(f'<p:sld xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:sp><p:nvSpPr><p:cNvPr id="123"/></p:nvSpPr><p:txBody><a:bodyPr anchor="{anchor}"/></p:txBody></p:sp></p:sld>'.encode())

    def test_card_budget_ends_above_native_icon_without_changing_frame(self):
        slot=dict(key='body',shapeId=123,x=20,y=30,w=180,h=180,size=18,text='Title')
        icon=dict(shapeId=777,role='icon',box=dict(x=30,y=110,w=18,h=18))
        limits=text_limits([slot],[icon],self.root())
        self.assertLess(limits['body']['y']+limits['body']['h'],110)
        frame=TemplateTextFrame(source_element_id='test',source_fingerprint='test',width_emu=180*12700,height_emu=180*12700,
            top_emu=0,bottom_emu=0,left_emu=0,right_emu=0,wrap=True,
            paragraphs=[TemplateParagraph(has_text=True,font_family='DejaVu Sans',font_size_pt=18)])
        fitter=TemplateTextFitter();bounded=constrained_frame(frame.model_dump(),slot,limits['body'],fitter)
        text=['A line']*6
        self.assertEqual(fitter.fit(frame,text).status,'fits')
        self.assertEqual(fitter.fit(bounded,text).status,'overflow')
        self.assertEqual(frame.height_emu,180*12700)
        self.assertEqual(frame.width_emu,bounded.width_emu)
        self.assertEqual(text_limits([slot],[icon],self.root('ctr')), {})

    def test_adjacent_column_and_photo_underlay_do_not_reduce_budget(self):
        slot=dict(key='body',shapeId=123,x=20,y=30,w=180,h=180,size=18,text='Title')
        icon=dict(shapeId=777,role='icon',box=dict(x=220,y=110,w=18,h=18))
        self.assertEqual(text_limits([slot],[icon],self.root()),{})

    def test_text_block_cannot_straddle_icon_even_if_no_letter_is_hidden(self):
        doc=pymupdf.open();page=doc.new_page(width=240,height=240)
        page.insert_text((30,50),'First line',fontsize=18)
        page.insert_text((30,100),'Second line',fontsize=18)
        contracts=[dict(key='body',value='First line\nSecond line',rect=pymupdf.Rect(20,20,200,200))]
        reserved=[dict(shapeId=800,role='icon',rect=pymupdf.Rect(30,65,48,83))]
        report=inspect_page(page,contracts,reserved=reserved)
        self.assertTrue(any('rendered_text_overlaps_reserved_object' in i['details'] for i in report['issues']))
        self.assertEqual(inspect_page(page,contracts,reserved=[dict(reserved[0],rect=pymupdf.Rect(205,65,223,83))])['issues'],[])
        doc.close()

    def test_small_caption_between_body_lines_repairs_body_only(self):
        doc=pymupdf.open();page=doc.new_page(width=240,height=240)
        page.insert_text((30,50),'First line',fontsize=18)
        page.insert_text((30,100),'Second line',fontsize=18)
        page.insert_text((30,75),'Caption',fontsize=10)
        contracts=[dict(key='body',value='First line\nSecond line',rect=pymupdf.Rect(20,20,200,200)),dict(key='caption',value='Caption',rect=pymupdf.Rect(25,62,180,78))]
        report=inspect_page(page,contracts)
        self.assertEqual([i['key'] for i in report['issues'] if 'rendered_text_overlap' in i['details']],['body'])
        doc.close()

    def test_native_chart_is_reserved_but_photo_underlay_is_not(self):
        from runtime.text_regions import reserved_regions
        item=dict(shape_id=87,object_kind='chart',geometry=dict(x_emu=12700,y_emu=12700,width_emu=1270000,height_emu=1270000))
        self.assertEqual(reserved_regions([item],{})[0]['role'],'chart')
        self.assertEqual(reserved_regions([dict(item,object_kind='picture')],{}),[])
