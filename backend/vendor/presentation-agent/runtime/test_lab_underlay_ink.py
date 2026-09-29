import unittest
import pymupdf
from runtime.render_quality import inspect_page,rebuilt_reserved
from runtime.rendered_contrast import glyph_colors,page_color_context

class UnderlayInkTest(unittest.TestCase):
    def test_empty_frame_can_extend_beyond_card_but_visible_ink_must_stay_inside(self):
        doc=pymupdf.open();page=doc.new_page(width=300,height=180)
        page.draw_rect(pymupdf.Rect(10,10,220,100),fill=(.8,.8,.8),color=None)
        page.insert_text((20,60),'INSIDE',fontsize=15)
        anchor=dict(key='r_text',shapeId=2,x=20,y=40,w=200,h=90,underlayCandidates=[1])
        profile=dict(protectedBoxes=[],textAnchors=[anchor],decorations=[dict(shapeId=1,box=dict(x=10,y=10,w=210,h=90))])
        scene=dict(texts=[dict(anchor,allowUnderlayShapes=[1])],pictures=[],charts=[])
        reserved=rebuilt_reserved(profile,scene)
        result=inspect_page(page,[dict(key='r_text',value='INSIDE',rect=pymupdf.Rect(20,40,220,130))],reserved=reserved)
        self.assertFalse(any('rendered_text_overlaps_reserved_object' in i['details'] for i in result['issues']))
        # A foreground shape cannot be waived even if a model requests it.
        anchor['underlayCandidates']=[]
        result=inspect_page(page,[dict(key='r_text',value='INSIDE',rect=page.rect)],reserved=rebuilt_reserved(profile,scene))
        self.assertTrue(any('rendered_text_overlaps_reserved_object' in i['details'] for i in result['issues']))
        doc.close()

    def test_cached_background_matches_independent_colour_measurement(self):
        doc=pymupdf.open();page=doc.new_page(width=400,height=150)
        page.draw_rect(pymupdf.Rect(0,0,110,150),fill=(.1,.1,.1),color=None)
        page.insert_text((10,60),'LIGHT',fontsize=18,color=(1,1,1))
        page.insert_text((150,60),'HIDDEN',fontsize=18,color=(1,1,1))
        context=page_color_context(page)
        for word in ['LIGHT','HIDDEN']:
            self.assertEqual(glyph_colors(page,word,page.rect,['#000000','#FFFFFF'],context),glyph_colors(page,word,page.rect,['#000000','#FFFFFF']))
        doc.close()
