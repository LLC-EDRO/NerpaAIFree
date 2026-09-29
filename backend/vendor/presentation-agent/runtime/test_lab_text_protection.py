import unittest
from runtime.text_protection import retain_text_artwork, protected_furniture


class TextProtectionTest(unittest.TestCase):
    def test_recurring_caption_and_footer_placeholders_are_available_for_rebuild(self):
        for role in ('footer', 'decoration', 'body'):
            for placeholder in (None, 'ftr', 'hdr'):
                item = dict(placeholder_type=placeholder, rich_text=dict(paragraphs=[
                    dict(runs=[dict(text='Сезонные изменения морского льда')])]))
                assignment = dict(role=role, recurring_pattern_id='repeated', confidence=dict(score=1))
                self.assertFalse(protected_furniture(assignment, item))
                self.assertFalse(retain_text_artwork('Сезонные изменения морского льда', role))

    def test_logo_page_number_and_ornament_remain_protected(self):
        for text, role in [('NORTH STUDIO','logo'),('07 / 12','page_number'),('◆','decoration')]:
            self.assertTrue(retain_text_artwork(text,role))
            item = dict(rich_text=dict(paragraphs=[dict(runs=[dict(text=text)])]))
            self.assertTrue(protected_furniture(dict(role=role),item))

    def test_short_topic_and_metric_are_not_ornaments(self):
        for text in ('AI','A','8','CO₂','Q4'):
            self.assertFalse(retain_text_artwork(text,'decoration'))


if __name__ == '__main__':
    unittest.main()
