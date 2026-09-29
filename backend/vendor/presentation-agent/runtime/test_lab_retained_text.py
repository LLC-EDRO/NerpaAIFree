import unittest
import pymupdf
from runtime.render_quality import inspect_page
class RetainedTextTest(unittest.TestCase):
    def test_source_text_hidden_by_opaque_shape_is_not_an_obstacle(self):
        doc=pymupdf.open();page=doc.new_page(width=300,height=200)
        page.insert_text((25,140),'Hidden source',fontsize=18)
        page.draw_rect(pymupdf.Rect(20,115,290,155),fill=(1,1,1),color=None)
        page.insert_text((25,140),'Generated content',fontsize=18)
        spec=dict(key='body',value='Generated content',rect=pymupdf.Rect(20,120,280,155))
        self.assertEqual(inspect_page(page,[spec])['issues'],[]);doc.close()
    def test_filled_text_must_not_cover_uneditable_footer(self):
        doc=pymupdf.open();page=doc.new_page(width=300,height=200)
        page.insert_text((25,160),'Generated content',fontsize=18)
        page.insert_text((25,161),'Source footer',fontsize=12)
        spec=dict(key='body',value='Generated content',rect=pymupdf.Rect(20,120,280,190))
        self.assertTrue(any('rendered_text_overlaps_source_text' in i['details'] for i in inspect_page(page,[spec])['issues']))
        doc.close()
    def test_nearby_source_footer_is_not_a_collision(self):
        doc=pymupdf.open();page=doc.new_page(width=300,height=200)
        page.insert_text((25,140),'Generated content',fontsize=18)
        page.insert_text((25,185),'Source footer',fontsize=12)
        spec=dict(key='body',value='Generated content',rect=pymupdf.Rect(20,120,280,155))
        self.assertEqual(inspect_page(page,[spec])['issues'],[]);doc.close()
