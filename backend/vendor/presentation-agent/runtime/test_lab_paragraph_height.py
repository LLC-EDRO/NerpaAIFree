import unittest
from runtime.text_frames import TemplateTextFitter, TemplateTextFrame, TemplateParagraph, overflow_guidance

EMU = 12700


class ParagraphHeightTest(unittest.TestCase):
    def frame(self, height=62, paragraphs=None, width=375):
        return TemplateTextFrame(source_element_id='test', source_fingerprint='test',
            width_emu=width*EMU, height_emu=height*EMU, left_emu=0, right_emu=0,
            top_emu=0, bottom_emu=0, wrap=True, paragraphs=paragraphs or [
                TemplateParagraph(has_text=True, font_family='DejaVu Sans', font_size_pt=18)])

    def test_three_lines_fit_without_shortening_and_four_do_not(self):
        fitter = TemplateTextFitter()
        self.assertEqual(fitter.fit(self.frame(), ['Форум педагогов', '20 педагогов награждены', '6 получили письма']).status, 'fits')
        self.assertEqual(fitter.fit(self.frame(), ['Форум', '20', '6', 'Югра']).status, 'overflow')
        self.assertEqual(fitter.fit(self.frame(height=45), ['Форум', '20', '6']).status, 'overflow')

    def test_blank_paragraphs_and_large_middle_style_keep_their_height(self):
        fitter = TemplateTextFitter()
        self.assertEqual(fitter.fit(self.frame(), ['Форум', '', '', '20']).status, 'overflow')
        normal = self.frame().paragraphs[0]
        frame = self.frame(paragraphs=[normal, normal.model_copy(update={'font_size_pt': 40}), normal])
        self.assertEqual(fitter.fit(frame, ['Форум', '20', '6']).status, 'overflow')
        spaced = normal.model_copy(update={'space_after': ('points', 12)})
        self.assertEqual(fitter.fit(self.frame(paragraphs=[spaced]), ['Форум', '20', '6']).status, 'overflow')

    def test_height_feedback_does_not_request_pointless_word_shortening(self):
        text = 'Форум\n20\n6'
        report = TemplateTextFitter().fit(self.frame(height=30), text.split('\n'))
        advice = overflow_guidance(report, text)
        self.assertEqual(advice['overflowAxes'], ['height'])
        self.assertEqual(advice['paragraphCount'], 3)
        self.assertNotIn('suggestedMaxChars', advice)
        self.assertIn('Объедини', advice['message'])
        text = 'Очень длинная строка без переноса'
        frame = self.frame(width=30).model_copy(update={'wrap': False})
        report = TemplateTextFitter().fit(frame, [text])
        advice = overflow_guidance(report, text)
        self.assertIn('width', advice['overflowAxes'])
        self.assertIn('suggestedMaxChars', advice)


if __name__ == '__main__':
    unittest.main()
