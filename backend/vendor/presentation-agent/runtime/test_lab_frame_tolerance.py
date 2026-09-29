import unittest
import pymupdf
from runtime.render_quality import inspect_page

class FrameToleranceTest(unittest.TestCase):
    def fixture(self):
        doc=pymupdf.open();page=doc.new_page(width=300,height=200)
        page.insert_text((30,50),'A clear heading',fontsize=18)
        # Last line extends below a transparent editing frame, entirely on-page.
        spec=dict(key='heading',value='A clear heading',rect=pymupdf.Rect(25,25,240,43),allowFrameOverflow=True)
        return doc,page,spec
    def test_visible_frame_overhang_is_warning_with_measured_geometry(self):
        doc,page,spec=self.fixture();report=inspect_page(page,[spec])
        self.assertEqual(report['issues'],[])
        self.assertEqual(report['warnings'][0]['reason'],'rendered_text_outside_frame_visible')
        self.assertGreater(report['warnings'][0]['overflowPt']['bottom'],2)
        doc.close()
    def test_styled_original_and_table_cell_bounds_remain_enforced(self):
        doc,page,spec=self.fixture();spec.pop('allowFrameOverflow')
        report=inspect_page(page,[spec]);issue=report['issues'][0]
        self.assertIn('rendered_text_outside_frame',issue['details'])
        self.assertIn('renderedTextBox',issue);doc.close()
    def test_overhang_into_image_or_chart_remains_error(self):
        doc,page,spec=self.fixture()
        report=inspect_page(page,[spec],reserved=[dict(shapeId=7,role='chart',rect=pymupdf.Rect(30,44,210,75))])
        self.assertTrue(any('rendered_text_overlaps_reserved_object' in i['details'] for i in report['issues']))
        self.assertEqual(report['warnings'],[]);doc.close()
    def test_overhang_into_retained_label_remains_error(self):
        doc,page,spec=self.fixture();page.insert_text((30,50),'Other label',fontsize=18)
        report=inspect_page(page,[spec]);self.assertTrue(report['issues']);self.assertEqual(report['warnings'],[]);doc.close()
    def test_off_page_text_is_not_softened(self):
        doc=pymupdf.open();page=doc.new_page(width=150,height=100)
        page.insert_text((120,50),'Heading off page',fontsize=18)
        spec=dict(key='a',value='Heading off page',rect=pymupdf.Rect(115,25,145,43),allowFrameOverflow=True)
        report=inspect_page(page,[spec]);self.assertTrue(report['issues']);self.assertEqual(report['warnings'],[]);doc.close()

    def test_low_contrast_is_advisory_without_text_shortening(self):
        doc=pymupdf.open();page=doc.new_page(width=300,height=200)
        page.insert_text((30,60),'Bright green caption',fontsize=18,color=(.12,1,.56))
        spec=dict(key='caption',value='Bright green caption',rect=pymupdf.Rect(20,30,290,90))
        report=inspect_page(page,[spec])
        self.assertEqual(report['issues'],[])
        self.assertEqual(report['warnings'][0]['reason'],'rendered_text_low_contrast')
        self.assertNotIn('suggestedMaxChars',report['warnings'][0])
        self.assertEqual(report['warnings'][0]['backgroundColor'],'#FFFFFF')
        self.assertGreaterEqual(report['warnings'][0]['backgroundCoverage'],.85);doc.close()

    def test_low_contrast_does_not_hide_an_actual_occlusion(self):
        doc=pymupdf.open();page=doc.new_page(width=300,height=200)
        page.insert_text((30,60),'Covered caption',fontsize=18)
        page.draw_rect(pymupdf.Rect(20,30,290,90),color=(1,1,1),fill=(1,1,1),overlay=True)
        spec=dict(key='caption',value='Covered caption',rect=pymupdf.Rect(20,30,290,90))
        report=inspect_page(page,[spec])
        self.assertTrue(any('rendered_text_occluded' in i['details'] for i in report['issues']));doc.close()
