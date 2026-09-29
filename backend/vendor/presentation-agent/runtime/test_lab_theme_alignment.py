import unittest
from types import SimpleNamespace as Obj
from unittest.mock import patch
from runtime.text_frames import source_text_frame, TemplateTextFrame, TemplateParagraph
from runtime.security import NS

class ThemeAlignmentTest(unittest.TestCase):
    def parse(self, alignment='just', rtl='0'):
        raw=f'<a:pPr xmlns:a="{NS["a"]}" algn="{alignment}" rtl="{rtl}"><a:buFont typeface="+mj-lt"/><a:buAutoNum type="arabicPeriod"/></a:pPr>'
        paragraph=Obj(runs=[],level=0,properties=Obj(raw_xml=raw))
        shape=Obj(rich_text=Obj(paragraphs=[paragraph]),text_frame=Obj(style_layers=[],font_aliases={'+mj-lt':'DejaVu Sans'}))
        measured=TemplateTextFrame(source_element_id='a',source_fingerprint='b',width_emu=1000000,height_emu=1000000,
            paragraphs=[TemplateParagraph(has_text=True,font_family='DejaVu Sans',font_size_pt=18,reason_codes=['paragraph_alignment_unsupported','paragraph_bullets_or_tabs_unsupported'])])
        with patch('runtime.text_frames.upstream_frame',return_value=measured):
            return source_text_frame(shape)

    def test_theme_marker_font_resolves_exactly_and_ordinary_justification_is_supported(self):
        for align in ('just','justLow'):
            p=self.parse(align).paragraphs[0]
            self.assertEqual(p.bullet_font,'DejaVu Sans')
            self.assertEqual(p.reason_codes,[])
            self.assertTrue(p.bullet_auto)

    def test_distributed_and_rtl_are_not_treated_as_ordinary_justification(self):
        self.assertIn('paragraph_alignment_unsupported',self.parse('dist').paragraphs[0].reason_codes)
        self.assertIn('paragraph_alignment_unsupported',self.parse('just','1').paragraphs[0].reason_codes)
