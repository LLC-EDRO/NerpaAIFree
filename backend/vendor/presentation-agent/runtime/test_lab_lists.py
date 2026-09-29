import unittest
import pymupdf
from runtime.text_frames import number_label, NUMBER_SCHEMES, TemplateParagraph, TemplateTextFrame, TemplateTextFitter
from app.text.replacement import replacement_paragraph_sources
from runtime.render_quality import located, normalized


def characters(text):
    return [dict(char=c, box=pymupdf.Rect(i, 1, i+1, 2),
                 origin=(i, 2), direction=(1, 0))
            for i, c in enumerate(normalized(text))]


class NativeListsTest(unittest.TestCase):
    def test_mixed_heading_and_list_skip_old_blank_spacers_without_losing_first_number(self):
        self.assertEqual(replacement_paragraph_sources([True,False,True,True],['Заголовок','Первый','Второй']),[0,2,3])
        self.assertEqual(replacement_paragraph_sources([True,False,True,True],['Заголовок','','Первый','Второй']),[0,1,2,3])
        self.assertEqual(replacement_paragraph_sources([True,False,True],['Заголовок','Первый','Второй']),[0,2,2])
    def numbered(self, values, marker_x=0, scheme='arabicPeriod'):
        result = []
        for i, value in enumerate(values):
            for text, x in ((number_label(scheme, i+1), marker_x), (normalized(value), 20)):
                for j, c in enumerate(text):
                    result.append(dict(char=c, box=pymupdf.Rect(x+j, i*20, x+j+1, i*20+10),
                                       origin=(x+j, i*20+8), direction=(1, 0)))
        return result

    def test_native_numbers_are_allowed_only_in_the_list_gutter(self):
        area = pymupdf.Rect(0, 0, 200, 100)
        value = 'Первый 82%\nВторой 12'
        chars = self.numbered(value.split('\n'))
        found = located(chars, value, area, numbered=True)
        self.assertEqual(''.join(c['char'] for c in found), normalized(value))
        self.assertFalse(located(chars, value, area))
        self.assertFalse(located(self.numbered(value.split('\n'), marker_x=19), value, area, numbered=True))
        self.assertFalse(located(self.numbered(['Первый 82%', 'Второй']), value, area, numbered=True))
        self.assertFalse(located(self.numbered(['Второй 12', 'Первый 82%']), value, area, numbered=True))

    def test_native_bullets_between_exact_paragraphs(self):
        chars = characters('•Первый 82%•Второй 12')
        result = located(chars, 'Первый 82%\nВторой 12', pymupdf.Rect(0, 0, 100, 10), {'•'})
        self.assertEqual(''.join(c['char'] for c in result), normalized('Первый 82%Второй 12'))

    def test_arabic_list_variants_fit_and_match_only_in_native_gutter(self):
        for scheme in NUMBER_SCHEMES:
            with self.subTest(scheme=scheme):
                p = TemplateParagraph(has_text=True, font_family='DejaVu Sans', font_size_pt=18,
                    bullet_auto=True, bullet_text=number_label(scheme, 12), bullet_start=12,
                    bullet_scheme=scheme, indent_emu=-40*12700, margin_left_emu=45*12700)
                frame=TemplateTextFrame(source_element_id='a',source_fingerprint='b',width_emu=400*12700,height_emu=120*12700,paragraphs=[p])
                self.assertEqual(TemplateTextFitter().fit(frame,['Первый','Второй']).status,'fits')
                area=pymupdf.Rect(0,0,200,100)
                self.assertTrue(located(self.numbered(['Первый','Второй'],scheme=scheme),'Первый\nВторой',area,numbered=True))
                self.assertFalse(located(self.numbered(['Первый','Второй'],marker_x=19,scheme=scheme),'Первый\nВторой',area,numbered=True))

    def test_missing_number_or_reordered_paragraph_does_not_pass(self):
        area = pymupdf.Rect(0, 0, 100, 10)
        self.assertFalse(located(characters('•Первый 82%•Второй'), 'Первый 82%\nВторой 12', area, {'•'}))
        self.assertFalse(located(characters('•Второй 12•Первый 82%'), 'Первый 82%\nВторой 12', area, {'•'}))

    def test_unexpected_content_and_unmarked_list_do_not_pass(self):
        area = pymupdf.Rect(0, 0, 100, 10)
        self.assertFalse(located(characters('•Первый 82%99•Второй 12'), 'Первый 82%\nВторой 12', area, {'•'}))
        self.assertFalse(located(characters('•Первый 82%•Второй 12'), 'Первый 82%\nВторой 12', area))


if __name__ == '__main__':
    unittest.main()
