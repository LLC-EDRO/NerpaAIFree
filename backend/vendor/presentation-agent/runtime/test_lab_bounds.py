import unittest
from runtime.text_frames import TemplateTextFitter, TemplateTextFrame, TemplateParagraph, visible_frame_width

class VisibleBounds(unittest.TestCase):
    def frame(self, visible=None):
        return TemplateTextFrame(source_element_id='test',source_fingerprint='test',width_emu=200*12700,height_emu=100*12700,left_emu=0,right_emu=0,top_emu=0,bottom_emu=0,wrap=True,visible_width_emu=visible,
            paragraphs=[TemplateParagraph(has_text=True,font_family='DejaVu Sans',font_size_pt=16,bold=False,italic=False)])
    def test_wrapped_fit_cannot_hide_off_page_ink(self):
        text='Education and technology: university research'
        fitter=TemplateTextFitter()
        self.assertEqual(fitter.fit(self.frame(),[text]).status,'fits')
        clipped=fitter.fit(self.frame(180*12700),[text])
        self.assertEqual(clipped.status,'overflow')
        self.assertIn('source_text_outside_page',clipped.reason_codes)
        self.assertEqual(fitter.fit(self.frame(180*12700),['Education','and technology','university research']).status,'fits')
    def test_alignment_and_group_scale(self):
        frame=self.frame().model_dump()
        self.assertIsNone(visible_frame_width(frame,dict(x=10,w=200),960))
        self.assertEqual(visible_frame_width(frame,dict(x=800,w=200,align='left'),960),160*12700)
        self.assertEqual(visible_frame_width(frame,dict(x=800,w=200,align='right'),960),0)
        self.assertEqual(visible_frame_width(frame,dict(x=800,w=200,align='center'),960),120*12700)
        self.assertEqual(visible_frame_width(frame,dict(x=800,w=400,align='left'),960),80*12700)

if __name__=='__main__':unittest.main()
