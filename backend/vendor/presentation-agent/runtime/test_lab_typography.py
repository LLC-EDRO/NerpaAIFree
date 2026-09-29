import unittest
from types import SimpleNamespace as O
from runtime.typography import inherited_text_color
from runtime.fill import replace_text
from runtime.security import NS,xml

class TypographyTest(unittest.TestCase):
    def shape(self, direct=None):
        run=O(text='Title',raw_properties_xml=direct,effective_style=O(color=None))
        paragraph=O(level=0,properties=O(raw_xml=None),runs=[run])
        def layer(color):return O(paragraph_styles={'lvl1pPr':f'<a:lvl1pPr xmlns:a="{NS["a"]}"><a:defRPr><a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:defRPr></a:lvl1pPr>'})
        return O(source_part='ppt/slides/slide1.xml',rich_text=O(paragraphs=[paragraph]),text_frame=O(style_layers=[layer('111111'),layer('FFFFFF')]))
    def test_layout_colour_overrides_master_and_explicit_run_overrides_layout(self):
        self.assertEqual(inherited_text_color(self.shape(),{}),'#FFFFFF')
        self.assertEqual(inherited_text_color(self.shape(f'<a:rPr xmlns:a="{NS["a"]}"><a:solidFill><a:srgbClr val="AABBCC"/></a:solidFill></a:rPr>'),{}),'#AABBCC')
    def test_replacement_retains_run_colours_and_exact_words(self):
        shape=xml(f'<p:sp xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}"><p:txBody><a:bodyPr/><a:p><a:r><a:rPr><a:solidFill><a:srgbClr val="000000"/></a:solidFill></a:rPr><a:t>Start </a:t></a:r><a:r><a:rPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill></a:rPr><a:t>highlighted central words </a:t></a:r><a:r><a:rPr><a:solidFill><a:srgbClr val="000000"/></a:solidFill></a:rPr><a:t>end.</a:t></a:r></a:p></p:txBody></p:sp>'.encode())
        text='New beautiful central sentence ends.'
        replace_text(shape,text)
        self.assertEqual(''.join(shape.xpath('.//a:t/text()',namespaces=NS)),text)
        self.assertEqual(shape.xpath('.//a:rPr/a:solidFill/a:srgbClr/@val',namespaces=NS),['000000','FFFFFF','000000'])
        self.assertTrue(all(not t or t[-1].isspace() for t in shape.xpath('.//a:t/text()',namespaces=NS)[:-1]))
