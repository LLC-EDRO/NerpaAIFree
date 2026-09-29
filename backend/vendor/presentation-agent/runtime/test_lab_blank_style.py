import unittest
from runtime.fill import replace_text
from runtime.security import xml,NS

class BlankStyleTest(unittest.TestCase):
    def shape(self,text='',props=''):
        return xml(('''<p:sp xmlns:p="%s" xmlns:a="%s"><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r>%s<a:t>%s</a:t></a:r><a:endParaRPr b="1" sz="1800"><a:solidFill><a:srgbClr val="FF0000"/></a:solidFill><a:latin typeface="Inter"/></a:endParaRPr></a:p></p:txBody></p:sp>'''%(NS['p'],NS['a'],props,text)).encode())
    def test_blank_paragraph_uses_native_insertion_style(self):
        shape=self.shape();replace_text(shape,'Новый текст')
        p=shape.find('.//a:rPr',NS)
        self.assertEqual((p.get('b'),p.get('sz')),('1','1800'))
        self.assertEqual(p.find('a:latin',NS).get('typeface'),'Inter')
        self.assertEqual(p.find('a:solidFill/a:srgbClr',NS).get('val'),'FF0000')
    def test_existing_text_keeps_run_style_and_empty_explicit_style_wins(self):
        for text in ('Пример',''):
            shape=self.shape(text,'<a:rPr b="0" sz="2400"><a:latin typeface="Arial"/></a:rPr>')
            replace_text(shape,'Новый текст');p=shape.find('.//a:rPr',NS)
            self.assertEqual((p.get('b'),p.get('sz')),('0','2400'))
            self.assertEqual(p.find('a:latin',NS).get('typeface'),'Arial')
