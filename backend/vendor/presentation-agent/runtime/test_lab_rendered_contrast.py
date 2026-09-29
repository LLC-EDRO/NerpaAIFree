import unittest
import pymupdf
from runtime.rendered_contrast import glyph_colors,recolor_body
from runtime.security import xml,NS

class RenderedContrastTest(unittest.TestCase):
    def test_rebuilt_colour_repair_uses_new_position_and_original_object_identity(self):
        from runtime.rendered_contrast import rebuilt_contrast_slots
        profile=dict(textAnchors=[dict(key='r_label',shapeId=47,role='body')])
        native=dict(rebuild=dict(texts=[dict(key='r_label',x=100,y=70,w=140,h=30)]))
        slots=rebuilt_contrast_slots(profile,native)
        self.assertEqual(slots,[dict(key='r_label',shapeId=47,role='body',x=100,y=70,w=140,h=30)])
    def test_white_word_on_white_is_recolored_but_white_on_dark_is_retained(self):
        doc=pymupdf.open();page=doc.new_page(width=400,height=150)
        page.draw_rect(pymupdf.Rect(0,0,110,150),fill=(.2,.1,.3),color=None)
        page.insert_text((10,70),'VISIBLE',fontsize=18,color=(1,1,1))
        page.insert_text((150,70),'HIDDEN',fontsize=18,color=(1,1,1))
        colors=glyph_colors(page,'VISIBLE HIDDEN',page.rect,['#000000','#FFFFFF'])
        self.assertEqual(colors,{i:'#000000' for i in range(7,13)})
        doc.close()
    def test_native_runs_keep_exact_text_style_and_paragraphs(self):
        body=xml(f'<p:txBody xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><a:p><a:r><a:rPr b="1" sz="2800"><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></a:rPr><a:t>VISIBLE HIDDEN</a:t></a:r></a:p></p:txBody>'.encode())
        self.assertTrue(recolor_body(body,{i:'#000000' for i in range(7,13)}))
        self.assertEqual(''.join(body.xpath('.//a:t/text()',namespaces=NS)),'VISIBLE HIDDEN')
        self.assertEqual(body.xpath('.//a:rPr/@b',namespaces=NS),['1','1'])
        self.assertEqual(body.xpath('.//a:rPr/@sz',namespaces=NS),['2800','2800'])
        self.assertEqual(body.xpath('.//a:srgbClr/@val',namespaces=NS),['FFFFFF','000000'])
    def test_missing_or_ambiguous_text_is_not_patched(self):
        doc=pymupdf.open();page=doc.new_page(width=200,height=100)
        page.insert_text((10,40),'EXISTING',fontsize=18,color=(1,1,1))
        self.assertEqual(glyph_colors(page,'MISSING',page.rect,['#000000','#FFFFFF']),{})
        doc.close()
    def test_letter_crossing_light_and_dark_background_is_not_guessed(self):
        doc=pymupdf.open();page=doc.new_page(width=200,height=100)
        page.draw_rect(pymupdf.Rect(0,0,24,100),fill=(0,0,0),color=None)
        page.insert_text((10,70),'M',fontsize=50,color=(1,1,1))
        self.assertEqual(glyph_colors(page,'M',page.rect,['#000000','#FFFFFF']),{})
        doc.close()
